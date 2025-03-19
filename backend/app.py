import os
import io
import base64
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import numpy as np
from PIL import Image
import torch
from torchvision import transforms
from model import U2NET
import json
from dotenv import load_dotenv
from flask_cors import CORS



# Load environment variables
load_dotenv()

# Handle different Pillow versions
try:
    LANCZOS = Image.LANCZOS
except AttributeError:
    LANCZOS = Image.Resampling.LANCZOS

app = Flask(__name__)
CORS(app, origins=["https://rcb.anirudhasah.com"])

# Configuration
UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', 'uploads')
BG_FOLDER = os.environ.get('BG_FOLDER', '../bg')
MODEL_DIR = os.environ.get('MODEL_DIR', 'saved_models')
MODEL_PATH = os.path.join(MODEL_DIR, 'u2net.pth')

# Create directories if they don't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(MODEL_DIR, exist_ok=True)

# Initialize model
model = None

# Load the U2NET model
def load_model():
    global model
    if model is not None:
        return model

    print("Loading U2-Net model...")
    model = U2NET(3, 1)
    if torch.cuda.is_available():
        model.load_state_dict(torch.load(MODEL_PATH))
        model.cuda()
    else:
        model.load_state_dict(torch.load(MODEL_PATH, map_location='cpu', weights_only = False))
    model.eval()
    print("Model loaded successfully!")
    return model

# Replace @app.before_first_request with a function that gets called on first API request
@app.before_request
def initialize():
    global model
    if model is None:
        model = load_model()

# Preprocessing for the model
def preprocess_image(image):
    # Resize
    image = image.resize((320, 320), LANCZOS)
    # Convert to tensor
    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
    ])
    image_tensor = transform(image).unsqueeze(0)
    return image_tensor

# Normalize the prediction
def normalize_prediction(prediction):
    ma = torch.max(prediction)
    mi = torch.min(prediction)
    return (prediction - mi) / (ma - mi)

# Root route for health check
@app.route('/')
def index():
    return jsonify({
        'status': 'ok',
        'message': 'RCB Profile Picture Generator API is running'
    })

# Get available backgrounds
@app.route('/api/backgrounds', methods=['GET'])
def get_backgrounds():
    backgrounds = []
    for filename in os.listdir(BG_FOLDER):
        if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
            with open(os.path.join(BG_FOLDER, filename), 'rb') as img_file:
                base64_image = base64.b64encode(img_file.read()).decode('utf-8')
                backgrounds.append({
                    'name': filename,
                    'preview': f"data:image/{filename.split('.')[-1]};base64,{base64_image}"
                })
    return jsonify(backgrounds)

# Process the image
@app.route('/api/process', methods=['POST'])
def process_image():
    if 'image' not in request.files or 'background' not in request.form:
        return jsonify({'error': 'Missing image or background'}), 400

    # Get the uploaded image
    file = request.files['image']
    background_name = request.form['background']

    # Read and process the image
    image = Image.open(file.stream).convert('RGB')
    original_size = image.size

    # Preprocess for the model
    image_tensor = preprocess_image(image)

    # Get prediction
    if torch.cuda.is_available():
        image_tensor = image_tensor.cuda()

    with torch.no_grad():
        prediction = model(image_tensor)[0]

    # Process the mask
    prediction = normalize_prediction(prediction)
    prediction = prediction.squeeze().cpu().numpy()

    # Resize mask to original image size and apply threshold for better segmentation
    mask = Image.fromarray((prediction * 255).astype(np.uint8))
    mask = mask.resize(original_size, LANCZOS)

    # Convert to numpy array and apply threshold
    mask_np = np.array(mask) / 255.0
    # Apply threshold to make the mask more binary (better separation)
    mask_np = np.where(mask_np > 0.2, 1.0, 0.0)

    # Load background
    background_path = os.path.join(BG_FOLDER, background_name)
    background = Image.open(background_path).convert('RGB')
    background = background.resize(original_size, LANCZOS)

    # Convert to RGBA for better compositing
    image_rgba = Image.new('RGBA', original_size)
    background_rgba = Image.new('RGBA', original_size)

    # Convert images to numpy arrays for processing
    image_np = np.array(image)
    background_np = np.array(background)

    # Expand mask dimensions for broadcasting
    mask_np = np.expand_dims(mask_np, axis=2)

    # Create foreground with alpha channel
    foreground_rgba = np.zeros((original_size[1], original_size[0], 4), dtype=np.uint8)
    foreground_rgba[:, :, 0:3] = image_np
    foreground_rgba[:, :, 3] = (mask_np[:, :, 0] * 255).astype(np.uint8)

    # Create background with alpha channel
    background_rgba_np = np.zeros((original_size[1], original_size[0], 4), dtype=np.uint8)
    background_rgba_np[:, :, 0:3] = background_np
    background_rgba_np[:, :, 3] = 255

    # Convert back to PIL images
    foreground_img = Image.fromarray(foreground_rgba, 'RGBA')
    background_img = Image.fromarray(background_rgba_np, 'RGBA')

    # Composite the images
    result = Image.new('RGBA', original_size, (0, 0, 0, 0))
    result.paste(background_img, (0, 0))
    result.paste(foreground_img, (0, 0), foreground_img)

    # Convert to RGB for final output
    result = result.convert('RGB')

    # Convert to base64 for response
    buffered = io.BytesIO()
    result.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode('utf-8')

    return jsonify({
        'result': f"data:image/png;base64,{img_str}"
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=os.environ.get('DEBUG', 'True').lower() == 'true',
            host='0.0.0.0',
            port=port)
