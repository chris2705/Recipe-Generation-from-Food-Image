"""
Gemini Vision API Service Layer for AI Recipe Generator.

Provides image analysis via Google Gemini Vision API.
Returns structured recipe data (food name, ingredients, recipe steps).
Includes MD5-based caching to prevent duplicate API calls and
robust error handling to never crash the application.
"""

import os
import json
import hashlib
import logging

logger = logging.getLogger(__name__)

# In-memory cache: image_hash -> gemini_result
_gemini_cache = {}

# ─── Startup Validation ────────────────────────────────────────────
def _log_startup_status():
    """Log Gemini service status at import time."""
    api_key = os.environ.get('GEMINI_API_KEY', '').strip()
    has_key = bool(api_key) and api_key != 'YOUR_KEY_HERE'
    use_fallback = os.environ.get('USE_GEMINI_FALLBACK', 'True').lower() == 'true'
    threshold = os.environ.get('CONFIDENCE_THRESHOLD', '0.50')
    logger.info("=" * 60)
    logger.info("[GEMINI SERVICE] Startup Status:")
    logger.info(f"  Gemini API Key Loaded: {has_key}")
    logger.info(f"  Gemini Fallback Enabled: {use_fallback}")
    logger.info(f"  Confidence Threshold: {threshold}")
    if has_key:
        logger.info(f"  API Key (last 4): ...{api_key[-4:]}")
    else:
        logger.warning("  ⚠️  No valid Gemini API key found!")
    logger.info("=" * 60)

_log_startup_status()


def _get_image_hash(image_path: str) -> str:
    """Compute MD5 hash of an image file for caching."""
    hasher = hashlib.md5()
    try:
        with open(image_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                hasher.update(chunk)
    except Exception:
        return ""
    return hasher.hexdigest()


def _build_prompt() -> str:
    """Build the structured prompt for Gemini Vision."""
    return """Analyze this food image carefully.

You MUST return ONLY valid JSON with NO additional text, NO markdown formatting, and NO code blocks.

Required JSON structure:
{
  "food_name": "Name of the dish",
  "confidence": 0.85,
  "ingredients": ["ingredient 1", "ingredient 2", "ingredient 3"],
  "recipe": ["Step 1: Do something", "Step 2: Do something else"],
  "cooking_time": "30 minutes",
  "servings": "4 servings"
}

Rules:
- food_name: The most likely name of the dish shown in the image.
- confidence: A float between 0.0 and 1.0 representing how confident you are.
- ingredients: A list of likely ingredients. Be specific (e.g., "olive oil" not just "oil").
- recipe: A list of step-by-step cooking instructions. Each step should be a complete sentence.
- cooking_time: Estimated total cooking time.
- servings: Estimated number of servings.
- If you are uncertain about the food, lower the confidence value.
- Return ONLY the JSON object. No explanation, no markdown."""


def analyze_image(image_path: str) -> dict | None:
    """
    Analyze a food image using Google Gemini Vision API.

    Args:
        image_path: Absolute path to the food image file.

    Returns:
        A dict with keys: food_name, confidence, ingredients, recipe,
        cooking_time, servings. Returns None if the API call fails
        for any reason (missing key, network error, malformed response, etc.).
    """
    api_key = os.environ.get('GEMINI_API_KEY', '').strip()

    # Guard: no API key or placeholder key
    if not api_key or api_key == 'YOUR_KEY_HERE':
        logger.warning("Gemini API key not configured. Skipping Gemini analysis.")
        return None

    # Check cache first
    image_hash = _get_image_hash(image_path)
    if image_hash and image_hash in _gemini_cache:
        logger.info(f"Gemini cache hit for image hash: {image_hash[:8]}...")
        return _gemini_cache[image_hash]

    try:
        from google import genai
        from PIL import Image as PILImage

        client = genai.Client(api_key=api_key)

        # Load the image
        img = PILImage.open(image_path)

        prompt = _build_prompt()

        # Use Gemini Vision model (latest available)
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[prompt, img],
        )

        if not response or not response.text:
            logger.warning("Gemini returned empty response.")
            return None

        # Parse the JSON from the response
        response_text = response.text.strip()

        # Remove markdown code block wrappers if present
        if response_text.startswith('```'):
            lines = response_text.split('\n')
            # Remove first and last lines (``` markers)
            lines = [l for l in lines if not l.strip().startswith('```')]
            response_text = '\n'.join(lines)

        result = json.loads(response_text)

        # Validate required fields
        required_fields = ['food_name', 'ingredients', 'recipe']
        for field in required_fields:
            if field not in result:
                logger.warning(f"Gemini response missing required field: {field}")
                return None

        # Normalize the result
        normalized = {
            'food_name': str(result.get('food_name', 'Unknown Food')),
            'confidence': float(result.get('confidence', 0.7)),
            'ingredients': list(result.get('ingredients', [])),
            'recipe': list(result.get('recipe', [])),
            'cooking_time': str(result.get('cooking_time', 'N/A')),
            'servings': str(result.get('servings', 'N/A')),
        }

        # Cache the result
        if image_hash:
            _gemini_cache[image_hash] = normalized
            logger.info(f"Gemini result cached for hash: {image_hash[:8]}...")

        return normalized

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Gemini JSON response: {e}")
        return None
    except ImportError as e:
        logger.error(f"google-genai package not installed. Run: pip install google-genai. Error: {e}")
        return None
    except Exception as e:
        logger.error(f"Gemini API call failed: {type(e).__name__}: {e}", exc_info=True)
        return None

