# list_models.py
import google.generativeai as genai
import os
from dotenv import load_dotenv

load_dotenv() # Carga tu .env para obtener la API key

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    print("Error: GOOGLE_API_KEY no encontrada en .env")
else:
    genai.configure(api_key=GOOGLE_API_KEY)
    print("Modelos disponibles que soportan 'generateContent':")

    try:
        for m in genai.list_models():

          if 'generateContent' in m.supported_generation_methods:
            print(f"- {m.name}")
    except Exception as e:
        print(f"Error al listar modelos: {e}")
        print("Verifica que tu API Key sea válida y esté habilitada.")
