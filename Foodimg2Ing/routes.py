from flask import render_template, url_for, flash, redirect, request
from Foodimg2Ing import app
from Foodimg2Ing.services.hybrid_predictor import predict as hybrid_predict
from Foodimg2Ing.services.nutrition_service import analyze_nutrition
from werkzeug.utils import secure_filename
import os
import uuid
import logging

logger = logging.getLogger(__name__)

_TEMP_RESULTS = {}


def _enrich_with_nutrition(result_data: dict) -> dict:
    """
    Run nutrition analysis on a prediction result and merge the output.
    Safe: never crashes — returns result_data unchanged on any error.
    """
    try:
        title = result_data.get('title', ['Unknown'])[0]
        ingredients = result_data.get('ingredients', [[]])[0]
        recipe_steps = result_data.get('recipe', [[]])[0]

        # Only analyze valid recipes
        if title in ("Not a valid recipe!", "Model Error"):
            result_data['nutrition'] = None
            return result_data

        nutrition = analyze_nutrition(title, ingredients, recipe_steps)
        result_data['nutrition'] = nutrition
        logger.info(f"Nutrition analysis complete: {nutrition.get('calories')} kcal, "
                    f"score={nutrition.get('health_score')}")
    except Exception as e:
        logger.error(f"Nutrition analysis failed: {type(e).__name__}: {e}")
        result_data['nutrition'] = None

    return result_data


@app.route('/', methods=['GET'])
def home():
    return render_template('landing.html')


@app.route('/about', methods=['GET'])
def about():
    return render_template('about.html')


@app.route('/generate', methods=['GET'])
def generate():
    return render_template('generate.html')


@app.route('/demo', methods=['GET'])
def demo():
    return render_template('demo.html')


@app.route('/generate', methods=['POST'])
def predict():
    imagefile = request.files['imagefile']
    upload_dir = os.path.join(app.root_path, 'static', 'images', 'uploads')
    os.makedirs(upload_dir, exist_ok=True)
    filename = secure_filename(imagefile.filename)
    image_path = os.path.join(upload_dir, filename)
    imagefile.save(image_path)
    img = "images/uploads/" + filename

    # Use hybrid predictor (local model + optional Gemini fallback)
    result_data = hybrid_predict(image_path, img)
    result_data = _enrich_with_nutrition(result_data)

    result_id = str(uuid.uuid4())
    _TEMP_RESULTS[result_id] = result_data
    return redirect(url_for('processing', result_id=result_id))


@app.route('/sample/<samplefoodname>')
def predictsample(samplefoodname):
    image_path = os.path.join(app.root_path, 'static', 'images', str(samplefoodname) + ".jpg")
    img = "images/" + str(samplefoodname) + ".jpg"

    # Use hybrid predictor (local model + optional Gemini fallback)
    result_data = hybrid_predict(image_path, img)
    result_data = _enrich_with_nutrition(result_data)

    result_id = str(uuid.uuid4())
    _TEMP_RESULTS[result_id] = result_data
    return redirect(url_for('processing', result_id=result_id))


@app.route('/processing')
def processing():
    result_id = request.args.get('result_id')
    if not result_id or result_id not in _TEMP_RESULTS:
        return redirect(url_for('generate'))
    return render_template('processing.html', result_id=result_id)


@app.route('/result')
def result():
    result_id = request.args.get('result_id')
    data = _TEMP_RESULTS.get(result_id)
    if not data:
        return redirect(url_for('generate'))
    return render_template('result.html', result_id=result_id, **data)
