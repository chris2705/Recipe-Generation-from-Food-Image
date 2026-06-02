import google.generativeai as genai
from PIL import Image
from dotenv import load_dotenv
import os

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

model = genai.GenerativeModel("gemini-2.5-flash")

# Change this path to your test food image
image = Image.open("pasta.jfif")

prompt = """
Identify this food image.

Return:
1. Food Name
2. Main Ingredients
3. Recipe
4. Estimated Calories
"""

response = model.generate_content([prompt, image])

print(response.text)