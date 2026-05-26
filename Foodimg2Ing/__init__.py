from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

app = Flask(__name__, template_folder='Templates')

# --- Configuration ---
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-fallback-secret-key')

# Database: resolve path relative to the app package directory
_db_path = os.path.join(app.root_path, 'data', 'recipe_generator.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{_db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# --- Extensions ---
db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'info'


@login_manager.user_loader
def load_user(user_id):
    from Foodimg2Ing.models import User
    return User.query.get(int(user_id))


# --- Create database tables ---
with app.app_context():
    from Foodimg2Ing.models import User, SavedRecipe  # noqa: F401
    db.create_all()

# --- Register blueprints & routes ---
from Foodimg2Ing import routes  # noqa: F401, E402
from Foodimg2Ing.auth import auth_bp  # noqa: F401, E402
from Foodimg2Ing.recipe_book import recipe_book_bp  # noqa: F401, E402

app.register_blueprint(auth_bp)
app.register_blueprint(recipe_book_bp)