"""
Chef AI Service — Context-aware Gemini-powered cooking assistant.

Production-grade resilience features:
  - Automatic retry with exponential backoff for HTTP 503 / rate-limit errors
  - Model fallback chain: gemini-2.5-flash → gemini-2.0-flash
  - MAX_CONTEXT_MESSAGES = 6 (reduced from 10 for cost + speed)
  - Image sent only on first message or when user explicitly requests it
  - Friendly user-facing error messages (raw API errors never exposed)
  - Circuit-breaker style cooldown after repeated failures

Cost optimization:
  - Compact context: system prompt + last 6 messages only
  - Image excluded from follow-up messages by default
  - No full conversation replay
"""

import os
import time
import logging
from PIL import Image as PILImage

logger = logging.getLogger(__name__)

# ─── Configuration ──────────────────────────────────────────────────────
MAX_CONTEXT_MESSAGES = 6       # Pairs of user+assistant sent to Gemini
MAX_RETRIES = 3                # Retry attempts for transient errors
INITIAL_BACKOFF_SECS = 1.0     # First retry after 1s, then 2s, then 4s
COOLDOWN_SECS = 30             # After MAX_RETRIES exhausted, cooldown period
MODEL_PRIMARY = 'gemini-2.5-flash'
MODEL_FALLBACK = 'gemini-2.0-flash'

# ─── Circuit Breaker State ─────────────────────────────────────────────
_last_failure_time = 0.0       # Timestamp of the last exhausted-retry failure
_consecutive_failures = 0      # Count of back-to-back total failures

# ─── Language Map ───────────────────────────────────────────────────────
LANGUAGE_MAP = {
    'en': 'English',
    'ml': 'Malayalam',
    'hi': 'Hindi',
    'ta': 'Tamil',
    'te': 'Telugu',
    'kn': 'Kannada',
}

# ─── Retryable Error Detection ─────────────────────────────────────────
# These patterns in exception messages indicate transient errors worth retrying
_RETRYABLE_PATTERNS = [
    '503',
    'service unavailable',
    'overloaded',
    'resource exhausted',
    'resource_exhausted',
    'rate limit',
    'rate_limit',
    'quota',
    'too many requests',
    '429',
    'temporarily unavailable',
    'deadline exceeded',
    'internal error',
    '500',
]

# ─── Friendly Error Messages ──────────────────────────────────────────
_USER_MSG_OVERLOADED = (
    "🍳 Chef AI is taking a quick break — the kitchen is very busy right now! "
    "Please try again in about 30 seconds."
)
_USER_MSG_GENERAL_ERROR = (
    "I'm having a little trouble right now. "
    "Please try your question again in a moment."
)
_USER_MSG_NO_API_KEY = (
    "Chef AI is not available right now — the API key is not configured. "
    "Please contact the administrator."
)
_USER_MSG_COOLDOWN = (
    "🍳 The AI service is temporarily resting due to high demand. "
    "Please wait about 30 seconds and try again."
)


# ─── Chef AI System Prompt ─────────────────────────────────────────────
CHEF_SYSTEM_PROMPT = """You are Chef AI — a friendly, knowledgeable personal cooking assistant.

You are helping a user cook the recipe currently displayed in the application.

RECIPE CONTEXT:
- Food: {food_name}
- Ingredients: {ingredients}
- Steps: {recipe_steps}
- Nutrition: {nutrition_info}
- Source: {prediction_source}

RULES:
1. Answer in simple, clear language for beginners.
2. Avoid jargon unless asked.
3. Keep answers practical and actionable.
4. Recommend the easiest option first.
5. Reference the recipe above when discussing ingredients.
6. NEVER answer off-topic questions. Politely redirect to cooking.
7. Stay focused on: cooking, ingredients, substitutions, nutrition, serving, storage, food safety.
8. Be warm and encouraging.
9. Use short paragraphs and bullet points.
{language_instruction}"""


def _is_retryable(exception: Exception) -> bool:
    """Check if an exception is transient and worth retrying."""
    error_str = str(exception).lower()
    exc_type = type(exception).__name__.lower()
    combined = f"{exc_type}: {error_str}"
    return any(pattern in combined for pattern in _RETRYABLE_PATTERNS)


def _build_system_prompt(recipe_context: dict, language: str = 'en') -> str:
    """Build the system prompt with recipe context injected."""
    food_name = recipe_context.get('food_name', 'Unknown')
    ingredients = recipe_context.get('ingredients', [])
    recipe_steps = recipe_context.get('recipe_steps', [])
    nutrition = recipe_context.get('nutrition', {})
    prediction_source = recipe_context.get('prediction_source', 'AI Model')

    # Compact ingredient formatting
    if isinstance(ingredients, list):
        ingredients_str = ', '.join(ingredients)
    else:
        ingredients_str = str(ingredients)

    # Compact step formatting (numbered, single-line each)
    if isinstance(recipe_steps, list):
        steps_str = ' | '.join(
            f"{i+1}. {step}" for i, step in enumerate(recipe_steps)
        )
    else:
        steps_str = str(recipe_steps)

    # Compact nutrition string (only key metrics)
    if nutrition and isinstance(nutrition, dict):
        parts = []
        if nutrition.get('calories'):
            parts.append(f"{nutrition['calories']}kcal")
        if nutrition.get('protein'):
            parts.append(f"P:{nutrition['protein']}g")
        if nutrition.get('carbs'):
            parts.append(f"C:{nutrition['carbs']}g")
        if nutrition.get('fat'):
            parts.append(f"F:{nutrition['fat']}g")
        if nutrition.get('health_score'):
            parts.append(f"Score:{nutrition['health_score']}/100")
        nutrition_str = ', '.join(parts) if parts else 'N/A'
    else:
        nutrition_str = 'N/A'

    # Language instruction
    lang_name = LANGUAGE_MAP.get(language, 'English')
    if language != 'en':
        if language == 'ml':
            language_instruction = (
                "\n\nLANGUAGE: Respond entirely in Malayalam script (മലയാളം ലിപി). "
                "Do not use Latin transliteration or English characters. The response must be readable in proper Malayalam script."
            )
        else:
            language_instruction = (
                f"\n\nLANGUAGE: Respond entirely in {lang_name}. "
                f"Do not mix languages."
            )
    else:
        language_instruction = ""

    return CHEF_SYSTEM_PROMPT.format(
        food_name=food_name,
        ingredients=ingredients_str,
        recipe_steps=steps_str,
        nutrition_info=nutrition_str,
        prediction_source=prediction_source,
        language_instruction=language_instruction,
    )


def _build_conversation_contents(
    system_prompt: str,
    history: list,
    user_message: str,
    image_path: str = None,
    include_image: bool = False,
) -> list:
    """Build the contents list for Gemini generate_content()."""
    contents = [system_prompt]

    # Include image only when explicitly needed
    if include_image and image_path and os.path.exists(image_path):
        try:
            img = PILImage.open(image_path)
            # Resize large images to reduce token cost
            max_dim = 512
            if max(img.size) > max_dim:
                img.thumbnail((max_dim, max_dim), PILImage.Resampling.LANCZOS)
            contents.append(img)
            logger.info(f"[CHEF AI] Image included: {os.path.basename(image_path)} "
                        f"({img.size[0]}x{img.size[1]})")
        except Exception as e:
            logger.warning(f"[CHEF AI] Failed to load image: {e}")

    # Add conversation history (last N messages only)
    trimmed_history = history[-(MAX_CONTEXT_MESSAGES * 2):]
    for msg in trimmed_history:
        role_prefix = "User: " if msg['role'] == 'user' else "Chef AI: "
        # Truncate very long messages in history to save tokens
        text = msg['message']
        if len(text) > 500:
            text = text[:497] + "..."
        contents.append(role_prefix + text)

    # Add current user message
    contents.append("User: " + user_message)

    return contents


def _call_gemini_with_retry(client, contents: list) -> str | None:
    """
    Call Gemini with automatic retry + exponential backoff + model fallback.

    Tries MODEL_PRIMARY first. On transient failure, retries with backoff.
    If primary model exhausts retries, falls back to MODEL_FALLBACK.

    Returns the response text or None on total failure.
    Raises _GeminiFatalError for non-retryable errors.
    """
    global _last_failure_time, _consecutive_failures

    models_to_try = [MODEL_PRIMARY, MODEL_FALLBACK]

    for model_name in models_to_try:
        backoff = INITIAL_BACKOFF_SECS

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                logger.info(
                    f"[CHEF AI] Attempt {attempt}/{MAX_RETRIES} "
                    f"with model={model_name}"
                )

                response = client.models.generate_content(
                    model=model_name,
                    contents=contents,
                )

                if response and response.text:
                    # Success — reset circuit breaker
                    _consecutive_failures = 0
                    return response.text.strip()

                logger.warning(
                    f"[CHEF AI] Empty response from {model_name} "
                    f"(attempt {attempt})"
                )

            except Exception as e:
                if _is_retryable(e):
                    logger.warning(
                        f"[CHEF AI] Retryable error on {model_name} "
                        f"(attempt {attempt}/{MAX_RETRIES}): "
                        f"{type(e).__name__}: {e}"
                    )
                    if attempt < MAX_RETRIES:
                        logger.info(
                            f"[CHEF AI] Backing off {backoff:.1f}s "
                            f"before retry..."
                        )
                        time.sleep(backoff)
                        backoff *= 2  # Exponential backoff
                        continue
                    else:
                        # Exhausted retries for this model → try fallback
                        logger.warning(
                            f"[CHEF AI] Exhausted {MAX_RETRIES} retries "
                            f"on {model_name}."
                        )
                        break
                else:
                    # Non-retryable error — don't retry, don't fallback
                    logger.error(
                        f"[CHEF AI] Non-retryable error: "
                        f"{type(e).__name__}: {e}",
                        exc_info=True,
                    )
                    _consecutive_failures += 1
                    _last_failure_time = time.time()
                    raise

        # If we get here, this model's retries are exhausted → try next model
        if model_name != models_to_try[-1]:
            logger.info(
                f"[CHEF AI] Falling back from {model_name} "
                f"to {models_to_try[models_to_try.index(model_name) + 1]}"
            )

    # All models exhausted
    _consecutive_failures += 1
    _last_failure_time = time.time()
    logger.error(
        f"[CHEF AI] All models exhausted after retries. "
        f"Consecutive failures: {_consecutive_failures}"
    )
    return None


def chat(
    user_message: str,
    recipe_context: dict,
    history: list = None,
    language: str = 'en',
    image_path: str = None,
    include_image: bool = False,
) -> dict:
    """
    Send a message to Chef AI and get a response.

    Features:
      - Automatic retry with exponential backoff (503, 429, etc.)
      - Model fallback (gemini-2.5-flash → gemini-2.0-flash)
      - Circuit-breaker cooldown after repeated failures
      - User-friendly error messages (raw API errors never exposed)
      - Image sent only on first message or when explicitly requested

    Args:
        user_message: The user's chat message.
        recipe_context: Dict with recipe data (food_name, ingredients, etc.)
        history: List of previous messages [{role, message}, ...]
        language: Language code for response language.
        image_path: Absolute path to the food image.
        include_image: Whether to include the image in this Gemini call.

    Returns:
        Dict with keys:
          - 'response' (str): The AI response text
          - 'error' (str|None): Error code or None on success
          - 'retryable' (bool): Whether the client should offer a retry button
    """
    global _consecutive_failures

    if history is None:
        history = []

    # ── Check API key ──────────────────────────────────────────────────
    api_key = os.environ.get('GEMINI_API_KEY', '').strip()
    if not api_key or api_key == 'YOUR_KEY_HERE':
        logger.warning("[CHEF AI] No Gemini API key configured.")
        return {
            'response': _USER_MSG_NO_API_KEY,
            'error': 'no_api_key',
            'retryable': False,
        }

    # ── Circuit breaker: cooldown after repeated failures ──────────────
    if _consecutive_failures >= MAX_RETRIES:
        elapsed = time.time() - _last_failure_time
        if elapsed < COOLDOWN_SECS:
            remaining = int(COOLDOWN_SECS - elapsed)
            logger.warning(
                f"[CHEF AI] Circuit breaker active. "
                f"{remaining}s remaining in cooldown."
            )
            return {
                'response': _USER_MSG_COOLDOWN,
                'error': 'cooldown',
                'retryable': True,
            }
        else:
            # Cooldown expired — allow retry, reset counter
            _consecutive_failures = 0
            logger.info("[CHEF AI] Cooldown expired, resetting circuit breaker.")

    # ── Main execution ─────────────────────────────────────────────────
    try:
        from google import genai

        client = genai.Client(api_key=api_key)

        # Build system prompt
        system_prompt = _build_system_prompt(recipe_context, language)

        # Image decision: only on first message or explicit request
        should_include_image = include_image or len(history) == 0

        # Build conversation contents
        contents = _build_conversation_contents(
            system_prompt=system_prompt,
            history=history,
            user_message=user_message,
            image_path=image_path,
            include_image=should_include_image,
        )

        logger.info(
            f"[CHEF AI] Request: msg='{user_message[:50]}', "
            f"history={len(history)}, lang={language}, "
            f"image={'yes' if should_include_image else 'no'}, "
            f"context_msgs={min(len(history), MAX_CONTEXT_MESSAGES * 2)}"
        )

        # Call Gemini with retry + fallback
        ai_text = _call_gemini_with_retry(client, contents)

        if ai_text:
            logger.info(f"[CHEF AI] Success: {len(ai_text)} chars")
            return {
                'response': ai_text,
                'error': None,
                'retryable': False,
            }
        else:
            # All retries + fallback exhausted
            return {
                'response': _USER_MSG_OVERLOADED,
                'error': 'all_retries_exhausted',
                'retryable': True,
            }

    except ImportError:
        logger.error("[CHEF AI] google-genai package not installed.")
        return {
            'response': "Chef AI requires the google-genai package.",
            'error': 'import_error',
            'retryable': False,
        }
    except Exception as e:
        # Non-retryable error caught from _call_gemini_with_retry
        logger.error(
            f"[CHEF AI] Unrecoverable error: {type(e).__name__}: {e}",
            exc_info=True,
        )
        return {
            'response': _USER_MSG_GENERAL_ERROR,
            'error': 'api_error',
            'retryable': True,
        }
