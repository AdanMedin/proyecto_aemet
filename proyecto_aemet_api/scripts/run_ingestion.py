"""Script de ingesta de datos.

Utilidad manual para desarrollo local. En produccion, el scheduler llama
directamente al endpoint POST /api/v1/admin/ingestar de la API.

Uso:
    python -m proyecto_aemet_api.scripts.run_ingestion            # ultimos 5 dias
    python -m proyecto_aemet_api.scripts.run_ingestion --dias 30  # ultimos 30
    python -m proyecto_aemet_api.scripts.run_ingestion --estaciones  # solo inventario
"""
from __future__ import annotations

import argparse
import os
import sys

import requests

_API = os.environ.get("API_BASE_URL", "http://localhost:8000")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingesta AEMET -> PostgreSQL (via API)")
    parser.add_argument("--dias", type=int, default=5, help="dias hacia atras (default 5)")
    parser.add_argument(
        "--estaciones",
        action="store_true",
        help="carga tambien el inventario de estaciones",
    )
    args = parser.parse_args()

    respuesta = requests.post(
        f"{_API}/api/v1/admin/ingestar",
        # guardar_bd=true: el script es para cargar datos de verdad en la BD
        # (en el endpoint el default es false, para que sea seguro probarlo).
        params={"dias": args.dias, "estaciones": args.estaciones, "guardar_bd": True},
        timeout=600,
    )
    respuesta.raise_for_status()
    print(respuesta.json())
    sys.exit(0)


if __name__ == "__main__":
    main()
