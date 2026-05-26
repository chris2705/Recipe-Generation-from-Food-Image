from flask import render_template, url_for, flash, redirect, request
from Foodimg2Ing import app
from Foodimg2Ing.services.hybrid_predictor import predict as hybrid_predict
from werkzeug.utils import secure_filename
import os
import uuid

_TEMP_RESULTS = {}

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

    result_id = str(uuid.uuid4())
    _TEMP_RESULTS[result_id] = result_data
    return redirect(url_for('processing', result_id=result_id))

@app.route('/sample/<samplefoodname>')
def predictsample(samplefoodname):
    image_path = os.path.join(app.root_path, 'static', 'images', str(samplefoodname) + ".jpg")
    img = "images/" + str(samplefoodname) + ".jpg"

    # Use hybrid predictor (local model + optional Gemini fallback)
    result_data = hybrid_predict(image_path, img)

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