"""Script de entrenamiento del modelo.

Utilidad manual para desarrollo local. En produccion, el scheduler llama
directamente al endpoint POST /api/v1/admin/reentrenar de la API.

Uso:
    python -m proyecto_aemet_api.scripts.train_model
"""
from __future__ import annotations

import os
import sys

import requests

_API = os.environ.get("API_BASE_URL", "http://localhost:8000")


def main() -> None:
    respuesta = requests.post(f"{_API}/api/v1/admin/reentrenar", timeout=3600)
    respuesta.raise_for_status()
    print(respuesta.json())
    sys.exit(0)


if __name__ == "__main__":
    main()
