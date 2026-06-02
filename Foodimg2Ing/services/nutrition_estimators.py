"""
Local Nutrition Estimation Engine for AI Recipe Generator.

Provides deterministic, ingredient-based nutrition estimation.
Uses a two-pass approach:
  1. Food-type detection from food name → applies category bias
  2. Ingredient keyword matching → sums per-ingredient nutrition contributions

Design is future-proof: GeminiNutritionEngine can replace this with
zero changes to NutritionService.
"""

import re
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Nutrition Result Dataclass
# ---------------------------------------------------------------------------

@dataclass
class NutritionResult:
    """Standardised output from any nutrition engine."""
    calories: int = 0
    protein: float = 0.0
    carbs: float = 0.0
    fat: float = 0.0
    fiber: float = 0.0
    sugar: float = 0.0
    sodium: int = 0
    servings: int = 2
    engine: str = "local"

    def to_dict(self) -> Dict:
        return {
            "calories": self.calories,
            "protein": round(self.protein, 1),
            "carbs": round(self.carbs, 1),
            "fat": round(self.fat, 1),
            "fiber": round(self.fiber, 1),
            "sugar": round(self.sugar, 1),
            "sodium": self.sodium,
            "servings": self.servings,
            "engine": self.engine,
        }


# ---------------------------------------------------------------------------
# Ingredient Knowledge Base
# Each entry: { keywords, cal_per_serving, protein, carbs, fat, fiber, sugar, sodium }
# Values represent the approximate nutritional contribution per common serving
# ---------------------------------------------------------------------------

INGREDIENT_KB: List[Dict] = [
    # ── Proteins ─────────────────────────────────────────────────────────────
    {"kw": ["chicken breast", "grilled chicken", "chicken"],
     "cal": 165, "pro": 31, "carb": 0, "fat": 3.6, "fib": 0, "sug": 0, "sod": 74},
    {"kw": ["beef", "ground beef", "minced beef", "steak", "burger patty"],
     "cal": 250, "pro": 26, "carb": 0, "fat": 17, "fib": 0, "sug": 0, "sod": 75},
    {"kw": ["pork", "bacon", "ham", "sausage", "pepperoni"],
     "cal": 242, "pro": 27, "carb": 0, "fat": 14, "fib": 0, "sug": 0, "sod": 1200},
    {"kw": ["salmon", "fish", "tuna", "cod", "tilapia", "shrimp", "prawn", "seafood"],
     "cal": 180, "pro": 25, "carb": 0, "fat": 8, "fib": 0, "sug": 0, "sod": 60},
    {"kw": ["egg", "eggs", "egg white"],
     "cal": 78, "pro": 6, "carb": 0.6, "fat": 5, "fib": 0, "sug": 0.6, "sod": 62},
    {"kw": ["tofu", "tempeh"],
     "cal": 76, "pro": 8, "carb": 2, "fat": 4, "fib": 0.3, "sug": 0, "sod": 7},
    {"kw": ["lentil", "lentils", "dal", "dhal"],
     "cal": 116, "pro": 9, "carb": 20, "fat": 0.4, "fib": 8, "sug": 1.8, "sod": 4},
    {"kw": ["chickpea", "chickpeas", "garbanzo"],
     "cal": 164, "pro": 9, "carb": 27, "fat": 2.6, "fib": 7, "sug": 4.8, "sod": 11},
    {"kw": ["black bean", "kidney bean", "white bean", "beans", "bean"],
     "cal": 132, "pro": 9, "carb": 24, "fat": 0.5, "fib": 8.7, "sug": 0.3, "sod": 1},
    {"kw": ["lamb", "mutton"],
     "cal": 258, "pro": 25, "carb": 0, "fat": 17, "fib": 0, "sug": 0, "sod": 72},

    # ── Dairy ─────────────────────────────────────────────────────────────────
    {"kw": ["cheese", "cheddar", "mozzarella", "parmesan", "feta", "gouda"],
     "cal": 113, "pro": 7, "carb": 0.4, "fat": 9, "fib": 0, "sug": 0.1, "sod": 174},
    {"kw": ["milk", "whole milk", "skim milk"],
     "cal": 61, "pro": 3.2, "carb": 4.8, "fat": 3.3, "fib": 0, "sug": 5, "sod": 43},
    {"kw": ["cream", "heavy cream", "whipping cream", "sour cream"],
     "cal": 340, "pro": 2.8, "carb": 2.8, "fat": 36, "fib": 0, "sug": 2.8, "sod": 38},
    {"kw": ["yogurt", "greek yogurt", "curd"],
     "cal": 59, "pro": 10, "carb": 3.6, "fat": 0.4, "fib": 0, "sug": 3.2, "sod": 36},
    {"kw": ["butter", "ghee"],
     "cal": 717, "pro": 0.9, "carb": 0.1, "fat": 81, "fib": 0, "sug": 0.1, "sod": 11},

    # ── Carbohydrates ─────────────────────────────────────────────────────────
    {"kw": ["rice", "white rice", "brown rice", "basmati", "jasmine rice"],
     "cal": 130, "pro": 2.7, "carb": 28, "fat": 0.3, "fib": 0.4, "sug": 0, "sod": 1},
    {"kw": ["pasta", "spaghetti", "penne", "noodle", "noodles", "macaroni"],
     "cal": 131, "pro": 5, "carb": 25, "fat": 1.1, "fib": 1.8, "sug": 0.6, "sod": 1},
    {"kw": ["bread", "bun", "baguette", "roll", "loaf", "sourdough"],
     "cal": 265, "pro": 9, "carb": 49, "fat": 3.2, "fib": 2.7, "sug": 5, "sod": 491},
    {"kw": ["flour", "all-purpose flour", "wheat flour"],
     "cal": 364, "pro": 10, "carb": 76, "fat": 1, "fib": 2.7, "sug": 0.3, "sod": 2},
    {"kw": ["potato", "potatoes", "sweet potato", "yam"],
     "cal": 77, "pro": 2, "carb": 17, "fat": 0.1, "fib": 2.2, "sug": 0.8, "sod": 6},
    {"kw": ["oat", "oats", "oatmeal", "rolled oats"],
     "cal": 389, "pro": 17, "carb": 66, "fat": 7, "fib": 10, "sug": 1, "sod": 2},
    {"kw": ["quinoa"],
     "cal": 120, "pro": 4.4, "carb": 21, "fat": 1.9, "fib": 2.8, "sug": 0, "sod": 7},
    {"kw": ["tortilla", "wrap", "roti", "chapati", "flatbread"],
     "cal": 218, "pro": 6, "carb": 38, "fat": 6, "fib": 2, "sug": 1, "sod": 400},

    # ── Vegetables ────────────────────────────────────────────────────────────
    {"kw": ["tomato", "tomatoes", "cherry tomato"],
     "cal": 18, "pro": 0.9, "carb": 3.9, "fat": 0.2, "fib": 1.2, "sug": 2.6, "sod": 5},
    {"kw": ["onion", "onions", "shallot", "scallion", "spring onion"],
     "cal": 40, "pro": 1.1, "carb": 9.3, "fat": 0.1, "fib": 1.7, "sug": 4.2, "sod": 4},
    {"kw": ["garlic", "garlic cloves"],
     "cal": 4, "pro": 0.2, "carb": 1, "fat": 0, "fib": 0.1, "sug": 0, "sod": 1},
    {"kw": ["bell pepper", "capsicum", "red pepper", "green pepper"],
     "cal": 20, "pro": 0.9, "carb": 4.6, "fat": 0.2, "fib": 1.7, "sug": 2.4, "sod": 3},
    {"kw": ["spinach", "kale", "lettuce", "greens", "arugula"],
     "cal": 23, "pro": 2.9, "carb": 3.6, "fat": 0.4, "fib": 2.2, "sug": 0.4, "sod": 79},
    {"kw": ["carrot", "carrots"],
     "cal": 41, "pro": 0.9, "carb": 9.6, "fat": 0.2, "fib": 2.8, "sug": 4.7, "sod": 69},
    {"kw": ["mushroom", "mushrooms"],
     "cal": 22, "pro": 3.1, "carb": 3.3, "fat": 0.3, "fib": 1, "sug": 1.7, "sod": 5},
    {"kw": ["broccoli", "cauliflower", "cabbage"],
     "cal": 34, "pro": 2.8, "carb": 6.6, "fat": 0.4, "fib": 2.6, "sug": 1.7, "sod": 33},
    {"kw": ["corn", "sweet corn", "maize"],
     "cal": 86, "pro": 3.2, "carb": 19, "fat": 1.2, "fib": 2.7, "sug": 3.2, "sod": 15},
    {"kw": ["peas", "green peas"],
     "cal": 81, "pro": 5.4, "carb": 14, "fat": 0.4, "fib": 5.5, "sug": 5.9, "sod": 5},

    # ── Fats & Oils ───────────────────────────────────────────────────────────
    {"kw": ["olive oil", "oil", "vegetable oil", "canola oil", "coconut oil"],
     "cal": 119, "pro": 0, "carb": 0, "fat": 14, "fib": 0, "sug": 0, "sod": 0},
    {"kw": ["avocado"],
     "cal": 160, "pro": 2, "carb": 9, "fat": 15, "fib": 7, "sug": 0.7, "sod": 7},
    {"kw": ["nuts", "almond", "cashew", "walnut", "peanut", "peanut butter"],
     "cal": 580, "pro": 21, "carb": 22, "fat": 50, "fib": 8, "sug": 5, "sod": 1},

    # ── Sugars & Sweeteners ───────────────────────────────────────────────────
    {"kw": ["sugar", "white sugar", "brown sugar", "cane sugar"],
     "cal": 387, "pro": 0, "carb": 100, "fat": 0, "fib": 0, "sug": 100, "sod": 1},
    {"kw": ["honey"],
     "cal": 304, "pro": 0.3, "carb": 82, "fat": 0, "fib": 0.2, "sug": 82, "sod": 4},
    {"kw": ["chocolate", "cocoa", "cacao"],
     "cal": 546, "pro": 5, "carb": 60, "fat": 32, "fib": 7, "sug": 48, "sod": 24},

    # ── Condiments & Sauces ───────────────────────────────────────────────────
    {"kw": ["soy sauce", "tamari"],
     "cal": 8, "pro": 1.3, "carb": 0.8, "fat": 0.1, "fib": 0.1, "sug": 0, "sod": 902},
    {"kw": ["ketchup", "tomato sauce", "marinara"],
     "cal": 112, "pro": 1.7, "carb": 27, "fat": 0.3, "fib": 0.3, "sug": 22, "sod": 907},
    {"kw": ["mayonnaise", "mayo"],
     "cal": 680, "pro": 1, "carb": 0.6, "fat": 75, "fib": 0, "sug": 0.5, "sod": 635},
    {"kw": ["salt"],
     "cal": 0, "pro": 0, "carb": 0, "fat": 0, "fib": 0, "sug": 0, "sod": 388},

    # ── Fruits ────────────────────────────────────────────────────────────────
    {"kw": ["lemon", "lime", "orange", "citrus"],
     "cal": 29, "pro": 1.1, "carb": 9, "fat": 0.3, "fib": 2.8, "sug": 2.5, "sod": 2},
    {"kw": ["apple", "pear", "peach", "mango", "banana"],
     "cal": 52, "pro": 0.3, "carb": 14, "fat": 0.2, "fib": 2.4, "sug": 10, "sod": 1},
    {"kw": ["strawberry", "blueberry", "raspberry", "berries"],
     "cal": 33, "pro": 0.7, "carb": 8, "fat": 0.3, "fib": 2, "sug": 5, "sod": 1},
]


# ---------------------------------------------------------------------------
# Food-type bias table  (applied on top of ingredient sums)
# Keys are regex patterns matched against the food name.
# ---------------------------------------------------------------------------

FOOD_TYPE_BIASES: List[Dict] = [
    # Burgers / Sandwiches
    {"pattern": r"burger|sandwich|sub|wrap|slider",
     "cal_add": 150, "pro_mul": 1.1, "carb_add": 30, "fat_add": 10, "sod_add": 300, "servings": 1},
    # Pizza
    {"pattern": r"pizza",
     "cal_add": 100, "pro_mul": 1.0, "carb_add": 20, "fat_add": 12, "sod_add": 400, "servings": 2},
    # Salads
    {"pattern": r"salad",
     "cal_add": -100, "pro_mul": 0.9, "carb_add": -5, "fat_add": 2, "sod_add": 50, "servings": 2},
    # Pasta dishes
    {"pattern": r"pasta|spaghetti|lasagna|fettuccine|carbonara|bolognese",
     "cal_add": 80, "pro_mul": 1.0, "carb_add": 20, "fat_add": 5, "sod_add": 250, "servings": 2},
    # Soups / Stews
    {"pattern": r"soup|stew|broth|curry|chowder|chili",
     "cal_add": -50, "pro_mul": 0.9, "carb_add": 10, "fat_add": -3, "sod_add": 500, "servings": 4},
    # Cakes / Desserts
    {"pattern": r"cake|cupcake|muffin|brownie|cookie|dessert|pie|tart|pudding|cheesecake",
     "cal_add": 200, "pro_mul": 0.5, "carb_add": 40, "fat_add": 15, "sod_add": 150, "servings": 8},
    # Grilled / Roasted meats
    {"pattern": r"grilled|roasted|baked|barbecue|bbq|smoked",
     "cal_add": 0, "pro_mul": 1.2, "carb_add": -5, "fat_add": 0, "sod_add": 100, "servings": 2},
    # Fried foods
    {"pattern": r"fried|fry|crispy|tempura|fritter",
     "cal_add": 120, "pro_mul": 0.9, "carb_add": 15, "fat_add": 12, "sod_add": 200, "servings": 2},
    # Stir-fry / Asian
    {"pattern": r"stir.?fry|stir fry|fried rice|lo mein|pad thai|ramen|sushi",
     "cal_add": 50, "pro_mul": 1.0, "carb_add": 20, "fat_add": 5, "sod_add": 600, "servings": 2},
    # Smoothies / Drinks
    {"pattern": r"smoothie|shake|juice|drink|beverage",
     "cal_add": -100, "pro_mul": 0.7, "carb_add": 15, "fat_add": -5, "sod_add": -50, "servings": 1},
    # Omelette / Egg dishes
    {"pattern": r"omelette|omelet|scrambled|frittata|quiche",
     "cal_add": 20, "pro_mul": 1.2, "carb_add": -5, "fat_add": 5, "sod_add": 100, "servings": 1},
    # Rice dishes
    {"pattern": r"rice|biryani|pilaf|risotto|fried rice",
     "cal_add": 80, "pro_mul": 0.9, "carb_add": 25, "fat_add": 3, "sod_add": 200, "servings": 2},
]


# ---------------------------------------------------------------------------
# Default values when ingredient matching is insufficient
# ---------------------------------------------------------------------------

DEFAULT_NUTRITION = NutritionResult(
    calories=380, protein=18.0, carbs=35.0, fat=14.0,
    fiber=4.0, sugar=6.0, sodium=420, servings=2
)

# Minimum calorie floor (a dish can't be under this)
MIN_CALORIES = 80
MAX_CALORIES = 2500

# Scaling factor: ingredient list values are per-ingredient, we want per-serving
SERVING_SCALE = 0.6


# ---------------------------------------------------------------------------
# LocalNutritionEngine
# ---------------------------------------------------------------------------

class LocalNutritionEngine:
    """
    Estimates nutrition from food name + ingredient list using a two-pass approach:
      1. Ingredient matching → sum macro contributions
      2. Food-type bias → adjust for dish category
    """

    def analyze(
        self,
        food_name: str,
        ingredients: List[str],
        recipe_steps: Optional[List[str]] = None,
    ) -> NutritionResult:
        """
        Run the local nutrition estimation.

        Args:
            food_name: Detected dish name (e.g., "Grilled Chicken Burger")
            ingredients: List of ingredient strings
            recipe_steps: Optional list of cooking steps (used for technique detection)

        Returns:
            NutritionResult with deterministic, ingredient-based estimates
        """
        if not ingredients:
            logger.warning("No ingredients provided; returning defaults.")
            result = NutritionResult(
                calories=DEFAULT_NUTRITION.calories,
                protein=DEFAULT_NUTRITION.protein,
                carbs=DEFAULT_NUTRITION.carbs,
                fat=DEFAULT_NUTRITION.fat,
                fiber=DEFAULT_NUTRITION.fiber,
                sugar=DEFAULT_NUTRITION.sugar,
                sodium=DEFAULT_NUTRITION.sodium,
                servings=DEFAULT_NUTRITION.servings,
                engine="local",
            )
            return result

        name_lower = food_name.lower()

        # --- Pass 1: Sum ingredient contributions ---
        total = {
            "cal": 0.0, "pro": 0.0, "carb": 0.0,
            "fat": 0.0, "fib": 0.0, "sug": 0.0, "sod": 0.0
        }
        matched_count = 0

        ingr_text = " ".join(ingredients).lower()

        for entry in INGREDIENT_KB:
            for kw in entry["kw"]:
                if kw in ingr_text:
                    # Scale down: each ingredient contributes proportionally
                    scale = SERVING_SCALE
                    total["cal"]  += entry["cal"]  * scale
                    total["pro"]  += entry["pro"]  * scale
                    total["carb"] += entry["carb"] * scale
                    total["fat"]  += entry["fat"]  * scale
                    total["fib"]  += entry["fib"]  * scale
                    total["sug"]  += entry["sug"]  * scale
                    total["sod"]  += entry["sod"]  * scale
                    matched_count += 1
                    break  # Only count each KB entry once

        # If very few matches, blend with defaults
        if matched_count < 2:
            blend = 0.7  # 70% defaults, 30% matched
            for k in total:
                default_val = getattr(DEFAULT_NUTRITION, {
                    "cal": "calories", "pro": "protein", "carb": "carbs",
                    "fat": "fat", "fib": "fiber", "sug": "sugar", "sod": "sodium"
                }[k])
                total[k] = total[k] * (1 - blend) + default_val * blend

        # --- Pass 2: Apply food-type bias ---
        servings = 2
        for bias in FOOD_TYPE_BIASES:
            if re.search(bias["pattern"], name_lower):
                total["cal"]  = total["cal"]  + bias.get("cal_add", 0)
                total["pro"]  = total["pro"]  * bias.get("pro_mul", 1.0)
                total["carb"] = total["carb"] + bias.get("carb_add", 0)
                total["fat"]  = total["fat"]  + bias.get("fat_add", 0)
                total["sod"]  = total["sod"]  + bias.get("sod_add", 0)
                servings      = bias.get("servings", 2)
                break  # Apply only the first matching bias

        # --- Pass 3: Technique modifiers from recipe steps ---
        if recipe_steps:
            steps_text = " ".join(recipe_steps).lower()
            if any(w in steps_text for w in ["deep fry", "deep-fry", "fry in oil"]):
                total["cal"] += 80
                total["fat"] += 8
            if any(w in steps_text for w in ["steam", "poach", "boil"]):
                total["cal"] -= 30
                total["fat"] -= 3

        # --- Pass 4: Divide by servings to get per-portion values ---
        # For multi-serving dishes (cakes=8, soups=4, etc.), macros should
        # represent a single portion, not the whole recipe.
        if servings > 1:
            for k in total:
                total[k] = total[k] / servings

        # --- Clamp values ---
        cal   = int(max(MIN_CALORIES, min(MAX_CALORIES, round(total["cal"]))))
        pro   = round(max(0.0, total["pro"]), 1)
        carb  = round(max(0.0, total["carb"]), 1)
        fat   = round(max(0.0, total["fat"]), 1)
        fib   = round(max(0.0, min(40.0, total["fib"])), 1)
        sug   = round(max(0.0, min(cal * 0.3, total["sug"])), 1)
        sod   = int(max(0, min(6000, round(total["sod"]))))

        logger.info(
            f"NutritionEngine: '{food_name}' → {cal} kcal, "
            f"P:{pro}g C:{carb}g F:{fat}g ({matched_count} ingredients matched)"
        )

        return NutritionResult(
            calories=cal,
            protein=pro,
            carbs=carb,
            fat=fat,
            fiber=fib,
            sugar=sug,
            sodium=sod,
            servings=servings,
            engine="local",
        )
