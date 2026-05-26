"""
Authentication blueprint for AI Recipe Generator.

Routes:
  /register  – Create a new account
  /login     – Log in to an existing account
  /logout    – Log out
  /profile   – View profile dashboard
"""

import re
from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from flask_login import login_user, logout_user, login_required, current_user
from Foodimg2Ing import db
from Foodimg2Ing.models import User, SavedRecipe

auth_bp = Blueprint('auth', __name__)


def handle_post_login_actions(user):
    """Check session for pending actions like save_recipe, execute them, and return redirect response if handled."""
    if session.get('post_login_action') == 'save_recipe':
        pending_recipe = session.get('pending_recipe')
        next_url = session.get('next_url') or url_for('home')

        if pending_recipe:
            food_name = pending_recipe.get('food_name', '').strip()
            image_path = pending_recipe.get('image_path', '')

            # Prevent duplicates
            existing = SavedRecipe.query.filter_by(
                user_id=user.id,
                food_name=food_name,
                image_path=image_path
            ).first()

            if existing:
                session['auto_saved_banner_duplicate'] = True
                flash('Recipe already exists in your Recipe Book.', 'info')
            else:
                recipe = SavedRecipe(
                    user_id=user.id,
                    food_name=food_name,
                    image_path=image_path,
                    prediction_source=pending_recipe.get('prediction_source', ''),
                    confidence=pending_recipe.get('confidence', 0.0),
                    cooking_time=pending_recipe.get('cooking_time') or None,
                    servings=pending_recipe.get('servings') or None,
                )
                recipe.set_ingredients(pending_recipe.get('ingredients', []))
                recipe.set_recipe_steps(pending_recipe.get('recipe_steps', []))

                db.session.add(recipe)
                db.session.commit()

                session['auto_saved_banner'] = True
                flash('Recipe saved successfully to your Recipe Book.', 'success')

        # Clean up session keys to prevent duplicate saves or redirects
        session.pop('pending_recipe', None)
        session.pop('post_login_action', None)
        session.pop('next_url', None)

        return redirect(next_url)
    return None


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------
@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    # Already logged in? Go home.
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')

        # --- Validation ---
        errors = []

        if not name or len(name) < 2:
            errors.append('Full name must be at least 2 characters.')

        if not email or not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
            errors.append('Please enter a valid email address.')

        if len(password) < 8:
            errors.append('Password must be at least 8 characters.')

        if not re.search(r'\d', password):
            errors.append('Password must contain at least one number.')

        if password != confirm:
            errors.append('Passwords do not match.')

        if User.query.filter_by(email=email).first():
            errors.append('An account with this email already exists.')

        if errors:
            for err in errors:
                flash(err, 'danger')
            return render_template('register.html', name=name, email=email)

        # --- Create user ---
        user = User(name=name, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        # Log the user in automatically upon successful registration
        login_user(user)

        # Check for post-login actions (auto-save recipe)
        action_response = handle_post_login_actions(user)
        if action_response:
            return action_response

        flash('Account created successfully! Welcome to your Recipe Book.', 'success')
        return redirect(url_for('home'))

    return render_template('register.html')


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        remember = bool(request.form.get('remember'))

        user = User.query.filter_by(email=email).first()

        if user is None or not user.check_password(password):
            flash('Invalid email or password.', 'danger')
            return render_template('login.html', email=email)

        login_user(user, remember=remember)

        # Check for post-login actions (auto-save recipe)
        action_response = handle_post_login_actions(user)
        if action_response:
            return action_response

        flash(f'Welcome back, {user.name}!', 'success')

        # Redirect to the page the user was trying to access, or home
        next_page = request.args.get('next')
        if next_page and next_page.startswith('/'):
            return redirect(next_page)
        return redirect(url_for('home'))

    return render_template('login.html')


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------
@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('home'))


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------
@auth_bp.route('/profile')
@login_required
def profile():
    return render_template('profile.html')
