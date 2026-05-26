"""
Hybrid Prediction Engine for AI Recipe Generator.

Orchestrates the decision between the local InverseCooking model
and the Google Gemini Vision API based on confidence scoring.

Flow:
  1. Run local model → get result + confidence
  2. If confidence >= threshold → return local result
  3. If confidence < threshold and Gemini enabled → call Gemini
  4. If Gemini succeeds → return Gemini result
  5. If Gemini fails → fall back to local result (never crash)
  6. If ENABLE_SECOND_OPINION → run both and return both
"""

import os
import logging

from Foodimg2Ing.output import output as run_local_model
from Foodimg2Ing.services.gemini_service import analyze_image as gemini_analyze
from Foodimg2Ing.services.prediction_logger import log_prediction

logger = logging.getLogger(__name__)


def _get_config():
    """Read configuration from environment variables."""
    return {
        'use_gemini': os.environ.get('USE_GEMINI_FALLBACK', 'True').lower() == 'true',
        'threshold': float(os.environ.get('CONFIDENCE_THRESHOLD', '0.70')),
        'second_opinion': os.environ.get('ENABLE_SECOND_OPINION', 'False').lower() == 'true',
    }


def _format_gemini_as_local(gemini_result: dict) -> dict:
    """
    Convert Gemini's response format to match the local model's output format.
    The local model returns lists of titles/ingredients/recipes (one per generation).
    """
    return {
        'title': [gemini_result['food_name']],
        'ingredients': [gemini_result['ingredients']],
        'recipe': [gemini_result['recipe']],
        'confidence': gemini_result.get('confidence', 0.8),
        'cooking_time': gemini_result.get('cooking_time', 'N/A'),
        'servings': gemini_result.get('servings', 'N/A'),
    }


def predict(image_path: str, img_url: str) -> dict:
    """
    Run the hybrid prediction pipeline.

    Args:
        image_path: Absolute filesystem path to the image.
        img_url: Relative URL path for displaying the image in templates.

    Returns:
        A dict with keys:
          - title (list)
          - ingredients (list of lists)
          - recipe (list of lists)
          - img (str, relative URL)
          - confidence (float, 0.0-1.0)
          - source (str, e.g. "Local AI Model", "Gemini Vision AI")
          - gemini_result (dict or None, present when second_opinion is True)
          - cooking_time (str, from Gemini or "Coming Soon")
          - servings (str, from Gemini or "Coming Soon")
    """
    config = _get_config()

    # --- Step 1: Run Local Model ---
    try:
        title, ingredients, recipe, confidence, metadata = run_local_model(image_path)
    except Exception as e:
        logger.error(f"Local model failed: {e}")
        # If even the local model fails, try Gemini as last resort
        title = ["Model Error"]
        ingredients = [[]]
        recipe = [["The local model encountered an error processing this image."]]
        confidence = 0.0
        metadata = {}

    local_result = {
        'title': title,
        'ingredients': ingredients,
        'recipe': recipe,
        'img': img_url,
        'confidence': confidence,
        'source': 'Local AI Model',
        'gemini_result': None,
        'cooking_time': 'Coming Soon',
        'servings': 'Coming Soon',
    }

    image_name = os.path.basename(image_path)

    # --- Step 2: Second Opinion Mode (always run both) ---
    if config['second_opinion'] and config['use_gemini']:
        logger.info(f"Second Opinion mode: running Gemini alongside local model.")
        gemini_raw = gemini_analyze(image_path)
        if gemini_raw:
            gemini_formatted = _format_gemini_as_local(gemini_raw)
            local_result['gemini_result'] = gemini_formatted
            local_result['source'] = 'Hybrid AI (Second Opinion)'
            local_result['cooking_time'] = gemini_raw.get('cooking_time', 'Coming Soon')
            local_result['servings'] = gemini_raw.get('servings', 'Coming Soon')

        log_prediction(
            image_name=image_name,
            local_prediction=title[0] if title else 'N/A',
            local_confidence=confidence,
            used_gemini=True,
            gemini_prediction=gemini_raw['food_name'] if gemini_raw else 'N/A',
            final_prediction=title[0] if title else 'N/A',
            source=local_result['source'],
        )
        return local_result

    # --- Step 3: Confidence-based Decision ---
    if confidence >= config['threshold']:
        # High confidence: use local result directly
        logger.info(f"Confidence {confidence:.2f} >= {config['threshold']}: using local model result.")
        log_prediction(
            image_name=image_name,
            local_prediction=title[0] if title else 'N/A',
            local_confidence=confidence,
            used_gemini=False,
            gemini_prediction='N/A',
            final_prediction=title[0] if title else 'N/A',
            source='Local AI Model',
        )
        return local_result

    # --- Step 4: Low Confidence - Try Gemini ---
    if config['use_gemini']:
        logger.info(f"Confidence {confidence:.2f} < {config['threshold']}: calling Gemini for fallback.")
        gemini_raw = gemini_analyze(image_path)

        if gemini_raw:
            gemini_formatted = _format_gemini_as_local(gemini_raw)
            result = {
                'title': gemini_formatted['title'],
                'ingredients': gemini_formatted['ingredients'],
                'recipe': gemini_formatted['recipe'],
                'img': img_url,
                'confidence': gemini_formatted['confidence'],
                'source': 'Gemini Vision AI',
                'gemini_result': None,
                'cooking_time': gemini_raw.get('cooking_time', 'Coming Soon'),
                'servings': gemini_raw.get('servings', 'Coming Soon'),
            }

            log_prediction(
                image_name=image_name,
                local_prediction=title[0] if title else 'N/A',
                local_confidence=confidence,
                used_gemini=True,
                gemini_prediction=gemini_raw['food_name'],
                final_prediction=gemini_raw['food_name'],
                source='Gemini Vision AI',
            )
            return result
        else:
            # Gemini failed, fall back to local
            logger.warning("Gemini unavailable. Falling back to local model result.")
            local_result['source'] = 'Local AI Model (Fallback)'

            log_prediction(
                image_name=image_name,
                local_prediction=title[0] if title else 'N/A',
                local_confidence=confidence,
                used_gemini=True,
                gemini_prediction='FAILED',
                final_prediction=title[0] if title else 'N/A',
                source='Local AI Model (Fallback)',
            )
            return local_result

    # Gemini disabled, return local result regardless of confidence
    log_prediction(
        image_name=image_name,
        local_prediction=title[0] if title else 'N/A',
        local_confidence=confidence,
        used_gemini=False,
        gemini_prediction='DISABLED',
        final_prediction=title[0] if title else 'N/A',
        source='Local AI Model',
    )
    return local_result
