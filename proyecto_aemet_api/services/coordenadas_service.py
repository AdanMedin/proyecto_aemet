from google import genai
from pydantic import BaseModel
from dotenv import load_dotenv
import os

os.environ.get("GEMINI_API_KEY")
load_dotenv()
MODEL = "gemini-3.1-flash-lite"
GOOGLE_API_KEY = os.getenv("GEMINI_API_KEY")

class CoordenadasMunicipio(BaseModel):
    municipio: str
    provincia: str
    latitud: float
    longitud: float


client = genai.Client(
    api_key=os.environ["GEMINI_API_KEY"]
)


def obtener_coordenadas(municipio: str) -> CoordenadasMunicipio:

    prompt = f"""
    El usuario proporciona el nombre de un municipio de España.

    Municipio: {municipio}

    Devuelve las coordenadas geográficas aproximadas del centro del municipio.

    Requisitos:
    - El municipio debe estar en España.
    - Latitud y longitud deben estar en grados decimales.
    - La longitud oeste debe ser negativa.
    - La longitud este debe ser positiva.
    - No añadas explicaciones.
    """

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_schema": CoordenadasMunicipio,
        },
    )

    return CoordenadasMunicipio.model_validate_json(
        response.text
    )