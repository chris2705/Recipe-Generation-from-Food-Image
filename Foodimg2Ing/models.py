"""
Database models for AI Recipe Generator.

Models:
  - User: Authentication and profile data.
  - SavedRecipe: User's saved recipes from AI predictions.

Changelog:
  v2 — Added nutrition columns (calories, protein, carbs, fat, fiber, sugar,
       sodium, nutrition_servings, health_score, diet_tags) for Phase 2
       Nutrition Analysis feature. All nullable for backwards compatibility.
  v3 — Added video_path, video_language, video_created_at for Phase 3
       AI Video Recipe Generator feature. All nullable for backwards compatibility.
"""

import json
from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from Foodimg2Ing import db


class User(UserMixin, db.Model):
    """Registered user account."""

    __tablename__ = 'user'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    saved_recipes = db.relationship(
        'SavedRecipe', backref='user', lazy='dynamic',
        cascade='all, delete-orphan'
    )

    def set_password(self, password):
        """Hash and store the password."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Verify a password against the stored hash."""
        return check_password_hash(self.password_hash, password)

    @property
    def recipe_count(self):
        """Number of saved recipes."""
        return self.saved_recipes.count()

    def __repr__(self):
        return f'<User {self.email}>'


class SavedRecipe(db.Model):
    """A recipe saved to a user's recipe book."""

    __tablename__ = 'saved_recipe'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    food_name = db.Column(db.String(200), nullable=False)
    ingredients = db.Column(db.Text, nullable=False)        # JSON list
    recipe_steps = db.Column(db.Text, nullable=False)       # JSON list
    image_path = db.Column(db.String(500), nullable=True)   # relative static path
    prediction_source = db.Column(db.String(100), nullable=True)
    confidence = db.Column(db.Float, nullable=True)
    cooking_time = db.Column(db.String(50), nullable=True)
    servings = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # --- Nutrition Analysis columns (Phase 2) ---
    # All nullable: existing recipes just show "N/A" until re-analyzed.
    calories         = db.Column(db.Integer, nullable=True)
    protein          = db.Column(db.Float,   nullable=True)
    carbs            = db.Column(db.Float,   nullable=True)
    fat              = db.Column(db.Float,   nullable=True)
    fiber            = db.Column(db.Float,   nullable=True)
    sugar            = db.Column(db.Float,   nullable=True)
    sodium           = db.Column(db.Integer, nullable=True)
    nutrition_servings = db.Column(db.Integer, nullable=True)  # numeric, separate from servings string
    health_score     = db.Column(db.Integer, nullable=True)    # 0-100
    diet_tags        = db.Column(db.Text,    nullable=True)    # JSON list of tag names

    # --- Video Generator columns (Phase 3) ---
    # All nullable: existing recipes just show no video until generated.
    video_path       = db.Column(db.String(500), nullable=True)  # relative static path to MP4
    video_language   = db.Column(db.String(50),  nullable=True)  # e.g. 'english', 'hindi'
    video_created_at = db.Column(db.DateTime,    nullable=True)

    # Prevent duplicate saves of the same recipe by the same user
    __table_args__ = (
        db.UniqueConstraint('user_id', 'food_name', 'image_path', name='uq_user_recipe'),
    )

    # --- Serialization helpers ---

    def get_ingredients(self):
        """Deserialize ingredients JSON to list."""
        try:
            return json.loads(self.ingredients)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_ingredients(self, ingredients_list):
        """Serialize ingredients list to JSON."""
        self.ingredients = json.dumps(ingredients_list)

    def get_recipe_steps(self):
        """Deserialize recipe steps JSON to list."""
        try:
            return json.loads(self.recipe_steps)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_recipe_steps(self, steps_list):
        """Serialize recipe steps list to JSON."""
        self.recipe_steps = json.dumps(steps_list)

    # --- Nutrition helpers ---

    def get_diet_tags(self) -> list:
        """Deserialize diet_tags JSON to list."""
        try:
            return json.loads(self.diet_tags) if self.diet_tags else []
        except (json.JSONDecodeError, TypeError):
            return []

    def set_diet_tags(self, tags_list: list):
        """Serialize diet tags list to JSON."""
        self.diet_tags = json.dumps(tags_list)

    def get_nutrition_dict(self) -> dict:
        """
        Return nutrition data as a dict suitable for template rendering.
        Returns None values for unset fields (pre-migration records).
        """
        return {
            "calories":          self.calories,
            "protein":           self.protein,
            "carbs":             self.carbs,
            "fat":               self.fat,
            "fiber":             self.fiber,
            "sugar":             self.sugar,
            "sodium":            self.sodium,
            "servings":          self.nutrition_servings,
            "health_score":      self.health_score,
            "diet_tag_names":    self.get_diet_tags(),
            "has_nutrition":     self.calories is not None,
        }

    def set_nutrition(self, nutrition_dict: dict):
        """Persist a nutrition analysis result dict to the model columns."""
        if not nutrition_dict:
            return
        self.calories           = nutrition_dict.get("calories")
        self.protein            = nutrition_dict.get("protein")
        self.carbs              = nutrition_dict.get("carbs")
        self.fat                = nutrition_dict.get("fat")
        self.fiber              = nutrition_dict.get("fiber")
        self.sugar              = nutrition_dict.get("sugar")
        self.sodium             = nutrition_dict.get("sodium")
        self.nutrition_servings = nutrition_dict.get("servings")
        self.health_score       = nutrition_dict.get("health_score")
        self.set_diet_tags(nutrition_dict.get("diet_tag_names", []))

    def __repr__(self):
        return f'<SavedRecipe {self.food_name} by User {self.user_id}>'
