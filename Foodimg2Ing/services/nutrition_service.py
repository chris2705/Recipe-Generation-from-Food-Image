"""
Nutrition Service for AI Recipe Generator.

Orchestrates the nutrition analysis pipeline:
  - Phase 1 (current): LocalNutritionEngine (ingredient-based estimation)
  - Phase 2 (future):  GeminiNutritionEngine (real-time API analysis)

Switch between engines via environment variable:
  USE_GEMINI_NUTRITION=False   → LocalNutritionEngine
  USE_GEMINI_NUTRITION=True    → GeminiNutritionEngine

Architecture:
  NutritionService
  ├── LocalNutritionEngine   (nutrition_estimators.py)
  └── GeminiNutritionEngine  (stub, activate when ready)

Future Nutrition History hooks are documented at the bottom of this file.
"""

import os
import json
import logging
from typing import List, Optional, Dict

from Foodimg2Ing.services.nutrition_estimators import LocalNutritionEngine, NutritionResult
from Foodimg2Ing.services.diet_classifier import DietClassifier

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GeminiNutritionEngine — Stub (future Phase 2)
# ---------------------------------------------------------------------------

class GeminiNutritionEngine:
    """
    Future Gemini-powered nutrition analysis engine.

    To activate: set USE_GEMINI_NUTRITION=True in .env and implement
    the analyze() method below using the Google Gemini API.

    Prompt template:
    ─────────────────────────────────────────────────────────────────────────
    You are a professional nutritionist. Analyze the following recipe and
    return ONLY valid JSON — no markdown, no explanation.

    Required JSON structure:
    {
      "calories": <integer, total for one serving>,
      "protein": <float, grams per serving>,
      "carbs": <float, grams per serving>,
      "fat": <float, grams per serving>,
      "fiber": <float, grams per serving>,
      "sugar": <float, grams per serving>,
      "sodium": <integer, milligrams per serving>,
      "servings": <integer, number of servings this recipe makes>
    }

    Food name: {food_name}
    Ingredients: {ingredients_text}
    Instructions: {steps_text}
    ─────────────────────────────────────────────────────────────────────────
    """

    NUTRITION_PROMPT = """You are a professional nutritionist. Analyze the following recipe and return ONLY valid JSON.

Required JSON structure:
{{
  "calories": <integer, total for one serving>,
  "protein": <float, grams per serving>,
  "carbs": <float, grams per serving>,
  "fat": <float, grams per serving>,
  "fiber": <float, grams per serving>,
  "sugar": <float, grams per serving>,
  "sodium": <integer, milligrams per serving>,
  "servings": <integer, total servings this recipe makes>
}}

Food name: {food_name}
Ingredients: {ingredients_text}
Instructions: {steps_text}

Return ONLY the JSON object. No markdown, no explanation."""

    def analyze(
        self,
        food_name: str,
        ingredients: List[str],
        recipe_steps: Optional[List[str]] = None,
    ) -> Optional[NutritionResult]:
        """
        Call Gemini API for nutrition analysis.
        Returns None on any failure (caller will fall back to local engine).
        """
        api_key = os.environ.get('GEMINI_API_KEY', '').strip()
        if not api_key or api_key == 'YOUR_KEY_HERE':
            logger.warning("Gemini API key not configured for nutrition analysis.")
            return None

        try:
            from google import genai

            client = genai.Client(api_key=api_key)

            prompt = self.NUTRITION_PROMPT.format(
                food_name=food_name,
                ingredients_text=", ".join(ingredients),
                steps_text=" ".join(recipe_steps or []),
            )

            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=[prompt],
            )

            if not response or not response.text:
                logger.warning("Gemini nutrition: empty response.")
                return None

            text = response.text.strip()
            # Strip markdown code fences if present
            if text.startswith('```'):
                lines = [l for l in text.split('\n') if not l.strip().startswith('```')]
                text = '\n'.join(lines)

            data = json.loads(text)

            return NutritionResult(
                calories=int(data.get("calories", 0)),
                protein=float(data.get("protein", 0)),
                carbs=float(data.get("carbs", 0)),
                fat=float(data.get("fat", 0)),
                fiber=float(data.get("fiber", 0)),
                sugar=float(data.get("sugar", 0)),
                sodium=int(data.get("sodium", 0)),
                servings=int(data.get("servings", 2)),
                engine="gemini",
            )

        except json.JSONDecodeError as e:
            logger.error(f"Gemini nutrition: JSON parse error: {e}")
        except ImportError:
            logger.error("google-genai not installed. Run: pip install google-generativeai")
        except Exception as e:
            logger.error(f"Gemini nutrition API error: {type(e).__name__}: {e}")

        return None


# ---------------------------------------------------------------------------
# NutritionService — Main Orchestrator
# ---------------------------------------------------------------------------

class NutritionService:
    """
    Central nutrition analysis service.

    Usage:
        service = NutritionService()
        result = service.analyze(food_name, ingredients, recipe_steps)
        # result is a full dict ready to pass to templates and DB
    """

    def __init__(self):
        self._local_engine = LocalNutritionEngine()
        self._gemini_engine = GeminiNutritionEngine()
        self._classifier = DietClassifier()

    def _use_gemini(self) -> bool:
        """Read USE_GEMINI_NUTRITION flag from environment."""
        return os.environ.get('USE_GEMINI_NUTRITION', 'False').lower() == 'true'

    def analyze(
        self,
        food_name: str,
        ingredients: List[str],
        recipe_steps: Optional[List[str]] = None,
    ) -> Dict:
        """
        Perform nutrition analysis and return a complete result dict.

        Args:
            food_name: Dish name (e.g., "Grilled Chicken Burger")
            ingredients: List of ingredient strings
            recipe_steps: Optional list of cooking instruction strings

        Returns:
            Dict with all nutrition data + diet tags + health score:
            {
                "calories": int,
                "protein": float,
                "carbs": float,
                "fat": float,
                "fiber": float,
                "sugar": float,
                "sodium": int,
                "servings": int,
                "engine": str,           # "local" or "gemini"
                "health_score": int,     # 0-100
                "health_label": str,     # "Excellent"/"Good"/"Fair"/"Poor"
                "diet_tags": list,       # [{"tag": str, "emoji": str, "color": str}]
                "diet_tag_names": list,  # ["High Protein", "Low Carb", ...]
                "nutrition_levels": dict,# {"calories": "High", "protein": "Medium", ...}
                "nutrition_summary": list# ["High Protein", "Low Sugar"]
            }
        """
        # --- Engine selection ---
        nutrition_result: Optional[NutritionResult] = None

        if self._use_gemini():
            logger.info("NutritionService: using Gemini engine.")
            nutrition_result = self._gemini_engine.analyze(food_name, ingredients, recipe_steps)
            if nutrition_result is None:
                logger.warning("Gemini nutrition failed; falling back to local engine.")

        if nutrition_result is None:
            logger.info("NutritionService: using local engine.")
            nutrition_result = self._local_engine.analyze(food_name, ingredients, recipe_steps)

        # --- Diet classification ---
        nutrition_dict = nutrition_result.to_dict()
        classification = self._classifier.classify(nutrition_dict, ingredients)

        # --- Merge into final result ---
        result = {**nutrition_dict, **classification}
        return result

    # ── Future Nutrition History hooks (Phase 7) ─────────────────────────────
    # These methods are stubs to signal the intended API surface.
    # Implement by querying SavedRecipe with the new nutrition columns.

    @staticmethod
    def get_user_nutrition_history(user_id: int) -> Dict:
        """
        [FUTURE] Aggregate a user's nutrition history from saved recipes.

        Returns:
            {
                "total_calories_logged": int,
                "average_protein": float,
                "average_carbs": float,
                "most_common_diet_tag": str,
                "recipe_count": int,
                "health_score_avg": float,
            }

        Implementation hint:
            from Foodimg2Ing.models import SavedRecipe
            from sqlalchemy import func
            from Foodimg2Ing import db
            stats = db.session.query(
                func.avg(SavedRecipe.calories),
                func.avg(SavedRecipe.protein),
                func.sum(SavedRecipe.calories),
                func.count(SavedRecipe.id),
            ).filter(SavedRecipe.user_id == user_id).one()
        """
        raise NotImplementedError("Phase 7: Nutrition History not yet implemented.")

    @staticmethod
    def get_daily_goal_progress(user_id: int, target_calories: int = 2000) -> Dict:
        """
        [FUTURE] Show today's calorie intake vs target from saved recipes.
        Implementation: filter SavedRecipe by created_at.date() == today.
        """
        raise NotImplementedError("Phase 7: Daily goal progress not yet implemented.")


# ---------------------------------------------------------------------------
# Module-level singleton (import once, reuse)
# ---------------------------------------------------------------------------

_nutrition_service = NutritionService()


def analyze_nutrition(
    food_name: str,
    ingredients: List[str],
    recipe_steps: Optional[List[str]] = None,
) -> Dict:
    """
    Convenience function for importing a single callable.

    Usage:
        from Foodimg2Ing.services.nutrition_service import analyze_nutrition
        nutrition = analyze_nutrition(food_name, ingredients, recipe_steps)
    """
    return _nutrition_service.analyze(food_name, ingredients, recipe_steps)
