"""
Recipe Book blueprint for AI Recipe Generator.

Routes:
  /recipe-book          – View all saved recipes (auth required)
  /recipe-book/save     – Save a recipe via AJAX (auth required)
  /recipe-book/<id>     – View a single saved recipe (auth required)
  /recipe-book/<id>/delete – Delete a saved recipe (auth required)
  /api/check-saved      – Check if a recipe is already saved (AJAX)
"""

import json
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, session
from flask_login import login_required, current_user
from Foodimg2Ing import db
from Foodimg2Ing.models import SavedRecipe

recipe_book_bp = Blueprint('recipe_book', __name__)


# ---------------------------------------------------------------------------
# Recipe Book – list all saved recipes
# ---------------------------------------------------------------------------
@recipe_book_bp.route('/recipe-book')
@login_required
def my_recipes():
    recipes = (
        SavedRecipe.query
        .filter_by(user_id=current_user.id)
        .order_by(SavedRecipe.created_at.desc())
        .all()
    )
    return render_template('recipe_book.html', recipes=recipes)


# ---------------------------------------------------------------------------
# Save Recipe (AJAX endpoint)
# ---------------------------------------------------------------------------
@recipe_book_bp.route('/recipe-book/save', methods=['POST'])
def save_recipe():
    data = request.get_json(silent=True) or {}

    # If user is not authenticated, store the recipe in session
    if not current_user.is_authenticated:
        session['pending_recipe'] = {
            'food_name': data.get('food_name', '').strip(),
            'ingredients': data.get('ingredients', []),
            'recipe_steps': data.get('recipe_steps', []),
            'image_path': data.get('image_path', ''),
            'prediction_source': data.get('prediction_source', ''),
            'confidence': data.get('confidence', 0.0),
            'cooking_time': data.get('cooking_time', ''),
            'servings': data.get('servings', ''),
            'timestamp': datetime.utcnow().isoformat()
        }
        session['post_login_action'] = 'save_recipe'
        session['next_url'] = data.get('next_url') or url_for('home')
        return jsonify({'login_required': True}), 401

    food_name = data.get('food_name', '').strip()
    ingredients = data.get('ingredients', [])
    recipe_steps = data.get('recipe_steps', [])
    image_path = data.get('image_path', '')
    prediction_source = data.get('prediction_source', '')
    confidence = data.get('confidence', 0.0)
    cooking_time = data.get('cooking_time', '')
    servings = data.get('servings', '')

    if not food_name:
        return jsonify({'error': 'Recipe name is required.'}), 400

    # Check for duplicates
    existing = SavedRecipe.query.filter_by(
        user_id=current_user.id,
        food_name=food_name,
        image_path=image_path
    ).first()

    if existing:
        return jsonify({
            'duplicate': True,
            'message': 'Recipe already exists in your recipe book.'
        }), 409

    # Create and save
    recipe = SavedRecipe(
        user_id=current_user.id,
        food_name=food_name,
        image_path=image_path,
        prediction_source=prediction_source,
        confidence=confidence,
        cooking_time=cooking_time if cooking_time else None,
        servings=servings if servings else None,
    )
    recipe.set_ingredients(ingredients)
    recipe.set_recipe_steps(recipe_steps)

    db.session.add(recipe)
    db.session.commit()

    return jsonify({
        'success': True,
        'message': 'Recipe saved successfully!',
        'recipe_id': recipe.id
    }), 201


# ---------------------------------------------------------------------------
# View a single saved recipe
# ---------------------------------------------------------------------------
@recipe_book_bp.route('/recipe-book/<int:recipe_id>')
@login_required
def view_recipe(recipe_id):
    recipe = SavedRecipe.query.get_or_404(recipe_id)

    # Security: only the owner can view their recipe
    if recipe.user_id != current_user.id:
        flash('You do not have permission to view this recipe.', 'danger')
        return redirect(url_for('recipe_book.my_recipes'))

    return render_template('recipe_detail.html', recipe=recipe)


# ---------------------------------------------------------------------------
# Delete a saved recipe
# ---------------------------------------------------------------------------
@recipe_book_bp.route('/recipe-book/<int:recipe_id>/delete', methods=['POST'])
@login_required
def delete_recipe(recipe_id):
    recipe = SavedRecipe.query.get_or_404(recipe_id)

    # Security: only the owner can delete their recipe
    if recipe.user_id != current_user.id:
        flash('You do not have permission to delete this recipe.', 'danger')
        return redirect(url_for('recipe_book.my_recipes'))

    db.session.delete(recipe)
    db.session.commit()

    flash('Recipe deleted from your recipe book.', 'info')
    return redirect(url_for('recipe_book.my_recipes'))


# ---------------------------------------------------------------------------
# Check if a recipe is already saved (AJAX)
# ---------------------------------------------------------------------------
@recipe_book_bp.route('/api/check-saved', methods=['POST'])
def check_saved():
    if not current_user.is_authenticated:
        return jsonify({'is_saved': False, 'authenticated': False})

    data = request.get_json(silent=True) or {}
    food_name = data.get('food_name', '').strip()
    image_path = data.get('image_path', '')

    existing = SavedRecipe.query.filter_by(
        user_id=current_user.id,
        food_name=food_name,
        image_path=image_path
    ).first()

    return jsonify({
        'is_saved': existing is not None,
        'authenticated': True,
        'recipe_id': existing.id if existing else None
    })
