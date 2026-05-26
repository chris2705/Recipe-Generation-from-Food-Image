"""
Database models for AI Recipe Generator.

Models:
  - User: Authentication and profile data.
  - SavedRecipe: User's saved recipes from AI predictions.
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

    def __repr__(self):
        return f'<SavedRecipe {self.food_name} by User {self.user_id}>'
