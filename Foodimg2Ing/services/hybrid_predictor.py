"""
Hybrid Prediction Engine for AI Recipe Generator.

Orchestrates the decision between the local InverseCooking model
and the Google Gemini Vision API based on confidence scoring.

Flow:
  1. Run local model → get result + confidence
  2. Detect invalid results (no eos, invalid recipe, errors) → force confidence to 0
  3. If confidence >= threshold → return local result
  4. If confidence < threshold and Gemini enabled → call Gemini
  5. If Gemini succeeds → return Gemini result
  6. If Gemini fails → fall back to local result (never crash)
  7. If ENABLE_SECOND_OPINION → also run Gemini for side panel (supplementary)
"""

import os
import logging

from Foodimg2Ing.output import output as run_local_model
from Foodimg2Ing.services.gemini_service import analyze_image as gemini_analyze
from Foodimg2Ing.services.prediction_logger import log_prediction

logger = logging.getLogger(__name__)

# Patterns that indicate the local model produced an unusable result
_INVALID_TITLES = {"Not a valid recipe!", "Model Error"}


def _get_config():
    """Read configuration from environment variables."""
    config = {
        'use_gemini': os.environ.get('USE_GEMINI_FALLBACK', 'True').lower() == 'true',
        'threshold': float(os.environ.get('CONFIDENCE_THRESHOLD', '0.50')),
        'second_opinion': os.environ.get('ENABLE_SECOND_OPINION', 'False').lower() == 'true',
    }
    logger.info(
        f"[HYBRID CONFIG] use_gemini={config['use_gemini']}, "
        f"threshold={config['threshold']}, "
        f"second_opinion={config['second_opinion']}"
    )
    return config


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


def _is_local_result_invalid(title: list, recipe: list) -> bool:
    """Check if the local model produced an unusable/error result."""
    if not title:
        return True
    primary_title = title[0] if title else ""
    if primary_title in _INVALID_TITLES:
        return True
    # Check if recipe contains error reasons (e.g. "Reason: no eos found")
    if recipe and isinstance(recipe[0], str) and recipe[0].startswith("Reason:"):
        return True
    return False


def _call_gemini(image_path: str) -> dict | None:
    """Call Gemini with logging. Returns formatted result or None."""
    logger.info("[HYBRID] >>> Calling Gemini Vision API...")
    gemini_raw = gemini_analyze(image_path)
    if gemini_raw:
        logger.info(f"[HYBRID] <<< Gemini returned: {gemini_raw['food_name']} "
                     f"(confidence={gemini_raw.get('confidence', 'N/A')})")
    else:
        logger.warning("[HYBRID] <<< Gemini returned None (call failed)")
    return gemini_raw


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
          - source (str, e.g. "Local AI Model", "Gemini Vision AI", "Gemini Fallback")
          - gemini_result (dict or None, present when second_opinion is True)
          - cooking_time (str, from Gemini or "Coming Soon")
          - servings (str, from Gemini or "Coming Soon")
    """
    config = _get_config()
    image_name = os.path.basename(image_path)
    local_model_crashed = False

    # ─── Step 1: Run Local Model ───────────────────────────────────────
    try:
        title, ingredients, recipe, confidence, metadata = run_local_model(image_path)
        logger.info(f"[HYBRID] Local model returned: title='{title[0] if title else 'N/A'}', "
                     f"confidence={confidence:.4f}")
    except Exception as e:
        logger.error(f"[HYBRID] Local model CRASHED: {type(e).__name__}: {e}")
        title = ["Model Error"]
        ingredients = [[]]
        recipe = [["The local model encountered an error processing this image."]]
        confidence = 0.0
        metadata = {}
        local_model_crashed = True

    # ─── Step 2: Detect Invalid Results ────────────────────────────────
    local_result_invalid = _is_local_result_invalid(title, recipe)
    if local_result_invalid and not local_model_crashed:
        logger.warning(f"[HYBRID] Local model produced invalid result: "
                       f"title='{title[0] if title else 'N/A'}', "
                       f"forcing confidence to 0.0")
        confidence = 0.0  # Force Gemini fallback

    # ─── Debug Logging ─────────────────────────────────────────────────
    needs_gemini = confidence < config['threshold']
    logger.info(f"[HYBRID] ┌─────────────────────────────────────")
    logger.info(f"[HYBRID] │ CONFIDENCE:  {confidence:.4f}")
    logger.info(f"[HYBRID] │ THRESHOLD:   {config['threshold']}")
    logger.info(f"[HYBRID] │ USING GEMINI: {needs_gemini and config['use_gemini']}")
    logger.info(f"[HYBRID] │ LOCAL VALID:  {not local_result_invalid}")
    logger.info(f"[HYBRID] │ LOCAL TITLE:  {title[0] if title else 'N/A'}")
    logger.info(f"[HYBRID] └─────────────────────────────────────")

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

    # ─── Step 3: Confidence-based Decision ─────────────────────────────
    if confidence >= config['threshold'] and not local_result_invalid:
        # High confidence + valid result: use local model
        logger.info(f"[HYBRID] ✅ Decision: LOCAL MODEL (confidence {confidence:.2f} >= {config['threshold']})")

        # Optionally run Gemini for Second Opinion side panel
        if config['second_opinion'] and config['use_gemini']:
            logger.info("[HYBRID] Second Opinion mode: also running Gemini for side panel")
            gemini_raw = _call_gemini(image_path)
            if gemini_raw:
                local_result['gemini_result'] = _format_gemini_as_local(gemini_raw)
                local_result['cooking_time'] = gemini_raw.get('cooking_time', 'Coming Soon')
                local_result['servings'] = gemini_raw.get('servings', 'Coming Soon')

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

    # ─── Step 4: Low Confidence or Invalid — Try Gemini ────────────────
    if config['use_gemini']:
        # Determine the right source label
        if local_model_crashed or local_result_invalid:
            fallback_source = 'Gemini Fallback'
            logger.info(f"[HYBRID] 🔄 Decision: GEMINI FALLBACK (local model {'crashed' if local_model_crashed else 'invalid'})")
        else:
            fallback_source = 'Gemini Vision AI'
            logger.info(f"[HYBRID] 🔄 Decision: GEMINI (confidence {confidence:.2f} < {config['threshold']})")

        gemini_raw = _call_gemini(image_path)

        if gemini_raw:
            gemini_formatted = _format_gemini_as_local(gemini_raw)
            result = {
                'title': gemini_formatted['title'],
                'ingredients': gemini_formatted['ingredients'],
                'recipe': gemini_formatted['recipe'],
                'img': img_url,
                'confidence': gemini_formatted['confidence'],
                'source': fallback_source,
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
                source=fallback_source,
            )
            logger.info(f"[HYBRID] ✅ Returning Gemini result: {gemini_raw['food_name']}")
            return result
        else:
            # Gemini also failed — return local result as last resort
            logger.warning("[HYBRID] ⚠️ Gemini also failed. Falling back to local model result.")
            local_result['source'] = 'Local AI Model (Fallback)'

            log_prediction(
                image_name=image_name,
                local_prediction=title[0] if title else 'N/A',
                local_confidence=confidence,
                used_gemini=True,
                gemini_prediction='ALSO_FAILED',
                final_prediction=title[0] if title else 'N/A',
                source='Local AI Model (Fallback)',
            )
            return local_result

    # ─── Step 5: Gemini Disabled ───────────────────────────────────────
    logger.info("[HYBRID] Gemini disabled. Returning local result regardless of confidence.")
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
