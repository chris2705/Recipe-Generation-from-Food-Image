"""
Diet Classifier for AI Recipe Generator.

Analyses a nutrition profile + ingredient list to:
  1. Assign diet tags (e.g., "High Protein", "Vegan", "Keto-Friendly")
  2. Compute a health score (0–100)
  3. Generate human-readable nutrition level labels

Rules are data-driven and configurable via DIET_RULES and HEALTH_SCORE_RULES.
"""

import re
import logging
from typing import List, Dict, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Keyword sets for ingredient-based classification
# ---------------------------------------------------------------------------

MEAT_KEYWORDS = {
    "chicken", "beef", "pork", "lamb", "mutton", "turkey", "duck",
    "veal", "venison", "sausage", "bacon", "pepperoni", "salami",
    "ham", "prosciutto", "chorizo", "meatball", "steak", "burger patty",
    "ground meat", "minced meat",
}

FISH_KEYWORDS = {
    "fish", "salmon", "tuna", "cod", "tilapia", "sardine", "mackerel",
    "trout", "halibut", "snapper", "anchovy", "shrimp", "prawn",
    "lobster", "crab", "scallop", "clam", "mussel", "oyster", "squid",
    "octopus", "seafood",
}

DAIRY_KEYWORDS = {
    "milk", "cheese", "butter", "cream", "yogurt", "curd", "ghee",
    "whey", "mozzarella", "parmesan", "cheddar", "brie", "feta",
    "ricotta", "cottage cheese",
}

EGG_KEYWORDS = {"egg", "eggs", "egg white", "egg yolk"}

ANIMAL_PRODUCT_KEYWORDS = MEAT_KEYWORDS | FISH_KEYWORDS | DAIRY_KEYWORDS | EGG_KEYWORDS

REFINED_CARB_KEYWORDS = {
    "white bread", "white rice", "white flour", "white pasta",
    "refined flour", "maida", "all-purpose flour",
}

WHOLE_GRAIN_KEYWORDS = {
    "brown rice", "whole wheat", "whole grain", "oat", "oats", "quinoa",
    "barley", "millet", "buckwheat", "whole grain bread",
}


# ---------------------------------------------------------------------------
# Diet tag rules
# Each rule is a callable (nutrition_dict, ingredients_text) → bool
# ---------------------------------------------------------------------------

def _ingr_contains_any(ingredients_text: str, keywords: set) -> bool:
    """Check if any keyword appears in the ingredient text."""
    for kw in keywords:
        if kw in ingredients_text:
            return True
    return False


DIET_RULES: List[Dict] = [
    {
        "tag": "High Protein",
        "emoji": "💪",
        "color": "success",
        "check": lambda n, i: n.get("protein", 0) >= 25,
    },
    {
        "tag": "Low Carb",
        "emoji": "🥬",
        "color": "info",
        "check": lambda n, i: n.get("carbs", 999) <= 20,
    },
    {
        "tag": "Keto-Friendly",
        "emoji": "🥑",
        "color": "warning",
        "check": lambda n, i: (
            n.get("fat", 0) >= 15 and
            n.get("carbs", 999) <= 20 and
            n.get("protein", 0) >= 15
        ),
    },
    {
        "tag": "Low Sugar",
        "emoji": "🍬",
        "color": "primary",
        "check": lambda n, i: n.get("sugar", 999) <= 5,
    },
    {
        "tag": "High Fiber",
        "emoji": "🌾",
        "color": "secondary",
        "check": lambda n, i: n.get("fiber", 0) >= 8,
    },
    {
        "tag": "Vegan",
        "emoji": "🌱",
        "color": "success",
        "check": lambda n, i: not _ingr_contains_any(i, ANIMAL_PRODUCT_KEYWORDS),
    },
    {
        "tag": "Vegetarian",
        "emoji": "🥗",
        "color": "success",
        "check": lambda n, i: (
            not _ingr_contains_any(i, MEAT_KEYWORDS) and
            not _ingr_contains_any(i, FISH_KEYWORDS)
        ),
    },
    {
        "tag": "Low Sodium",
        "emoji": "🧂",
        "color": "info",
        "check": lambda n, i: n.get("sodium", 999) <= 200,
    },
    {
        "tag": "Balanced Meal",
        "emoji": "⚖️",
        "color": "primary",
        "check": lambda n, i: (
            15 <= n.get("protein", 0) <= 45 and
            20 <= n.get("carbs", 0) <= 60 and
            5  <= n.get("fat", 0)   <= 30 and
            300 <= n.get("calories", 0) <= 700
        ),
    },
    {
        "tag": "Whole Grain",
        "emoji": "🌾",
        "color": "secondary",
        "check": lambda n, i: _ingr_contains_any(i, WHOLE_GRAIN_KEYWORDS),
    },
]


# ---------------------------------------------------------------------------
# Health score rules
# Each rule awards or deducts points
# ---------------------------------------------------------------------------

HEALTH_SCORE_RULES: List[Dict] = [
    # Positive factors
    {"desc": "High protein",         "check": lambda n, i: n.get("protein", 0) >= 20,      "pts": 15},
    {"desc": "Good fiber",           "check": lambda n, i: n.get("fiber", 0) >= 5,          "pts": 15},
    {"desc": "Low sugar",            "check": lambda n, i: n.get("sugar", 0) <= 5,          "pts": 10},
    {"desc": "Low sodium",           "check": lambda n, i: n.get("sodium", 9999) <= 400,    "pts": 10},
    {"desc": "Balanced calories",    "check": lambda n, i: 300 <= n.get("calories", 0) <= 650, "pts": 10},
    {"desc": "Reasonable fat",       "check": lambda n, i: n.get("fat", 0) <= 20,           "pts": 8},
    {"desc": "Contains vegetables",  "check": lambda n, i: any(
        v in i for v in ["spinach", "broccoli", "kale", "carrot", "tomato",
                          "pepper", "pea", "lettuce", "mushroom", "cabbage"]), "pts": 8},
    {"desc": "Whole grains",         "check": lambda n, i: _ingr_contains_any(i, WHOLE_GRAIN_KEYWORDS), "pts": 7},
    {"desc": "Lean protein",         "check": lambda n, i: (
        _ingr_contains_any(i, {"chicken breast", "turkey", "fish", "tofu", "lentil"}) and
        n.get("fat", 0) <= 15), "pts": 7},
    # Negative factors
    {"desc": "Very high sodium",     "check": lambda n, i: n.get("sodium", 0) >= 1200,      "pts": -15},
    {"desc": "Very high sugar",      "check": lambda n, i: n.get("sugar", 0) >= 25,         "pts": -15},
    {"desc": "Very high calories",   "check": lambda n, i: n.get("calories", 0) >= 900,     "pts": -12},
    {"desc": "Very high fat",        "check": lambda n, i: n.get("fat", 0) >= 40,           "pts": -10},
    {"desc": "Low protein",          "check": lambda n, i: n.get("protein", 0) <= 5,        "pts": -5},
    {"desc": "Low fiber",            "check": lambda n, i: n.get("fiber", 0) <= 1,          "pts": -5},
]

# Base score (always start here)
HEALTH_SCORE_BASE = 50


# ---------------------------------------------------------------------------
# Nutrition level labels
# ---------------------------------------------------------------------------

def _get_level(value: float, thresholds: Tuple) -> str:
    """Map a value to Low / Medium / High based on (low_max, high_min)."""
    low_max, high_min = thresholds
    if value <= low_max:
        return "Low"
    elif value >= high_min:
        return "High"
    return "Medium"


NUTRITION_LEVEL_THRESHOLDS = {
    "calories": (300, 600),
    "protein":  (10,  25),
    "carbs":    (20,  50),
    "fat":      (8,   20),
    "fiber":    (3,   8),
    "sugar":    (5,   15),
    "sodium":   (200, 600),
}


# ---------------------------------------------------------------------------
# DietClassifier
# ---------------------------------------------------------------------------

class DietClassifier:
    """
    Classifies a recipe's diet profile from its nutrition data + ingredients.
    Provides:
      - Diet tags list (with emoji, tag name, color)
      - Health score (0–100)
      - Nutrition level labels (Low / Medium / High per macro)
    """

    def classify(
        self,
        nutrition: Dict,
        ingredients: List[str],
    ) -> Dict:
        """
        Run the diet classification.

        Args:
            nutrition: Dict with keys: calories, protein, carbs, fat, fiber, sugar, sodium
            ingredients: List of ingredient strings

        Returns:
            Dict with:
              - diet_tags: list of {"tag": str, "emoji": str, "color": str}
              - diet_tag_names: list of str (just the names, for DB storage)
              - health_score: int (0–100)
              - health_label: str ("Excellent" / "Good" / "Fair" / "Poor")
              - nutrition_levels: dict of {macro: "Low"/"Medium"/"High"}
              - nutrition_summary: list of str (e.g., "High Protein", "Medium Carbs")
        """
        ingr_text = " ".join(ingredients).lower()

        # --- Diet tags ---
        active_tags = []
        for rule in DIET_RULES:
            try:
                if rule["check"](nutrition, ingr_text):
                    active_tags.append({
                        "tag": rule["tag"],
                        "emoji": rule["emoji"],
                        "color": rule["color"],
                    })
            except Exception as e:
                logger.debug(f"Diet rule error for '{rule['tag']}': {e}")

        # --- Health score ---
        score = HEALTH_SCORE_BASE
        for rule in HEALTH_SCORE_RULES:
            try:
                if rule["check"](nutrition, ingr_text):
                    score += rule["pts"]
            except Exception as e:
                logger.debug(f"Health score rule error: {e}")

        score = max(0, min(100, score))

        if score >= 80:
            health_label = "Excellent"
        elif score >= 65:
            health_label = "Good"
        elif score >= 45:
            health_label = "Fair"
        else:
            health_label = "Poor"

        # --- Nutrition levels ---
        nutrition_levels = {}
        nutrition_summary = []
        for macro, thresholds in NUTRITION_LEVEL_THRESHOLDS.items():
            val = nutrition.get(macro, 0)
            level = _get_level(val, thresholds)
            nutrition_levels[macro] = level
            # Only add to summary if High or Low (not Medium, to keep it concise)
            if level in ("High", "Low"):
                label_map = {
                    "calories": "Calories", "protein": "Protein",
                    "carbs": "Carbs", "fat": "Fat",
                    "fiber": "Fiber", "sugar": "Sugar", "sodium": "Sodium",
                }
                nutrition_summary.append(f"{level} {label_map.get(macro, macro.title())}")

        logger.info(
            f"DietClassifier: score={score} ({health_label}), "
            f"tags={[t['tag'] for t in active_tags]}"
        )

        return {
            "diet_tags": active_tags,
            "diet_tag_names": [t["tag"] for t in active_tags],
            "health_score": score,
            "health_label": health_label,
            "nutrition_levels": nutrition_levels,
            "nutrition_summary": nutrition_summary,
        }
