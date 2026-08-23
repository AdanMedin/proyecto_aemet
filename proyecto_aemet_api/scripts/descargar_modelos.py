"""Descarga unos pocos modelos de S3 a disco local, para desarrollo.

Uso:
    python -m proyecto_aemet_api.scripts.descargar_modelos 1690A 3195 8175

Descarga esos modelos a la carpeta configurada en RUTA_MODELOS.
Despues, quita S3_BUCKET_MODELOS del .env y la API los lee de disco (rapido).
"""
from __future__ import annotations

import os
import sys

from proyecto_aemet_api.core.config import get_settings
from proyecto_aemet_api.ml.s3_storage import S3Storage


def main() -> None:
    indicativos = sys.argv[1:]
    if not indicativos:
        print("Pasa los indicativos de las estaciones, por ejemplo:")
        print("  python -m proyecto_aemet_api.scripts.descargar_modelos 1690A 3195")
        sys.exit(1)

    s = get_settings()
    os.makedirs(s.ruta_modelos, exist_ok=True)
    s3 = S3Storage(s.s3_bucket_modelos, s.aws_region, s.s3_prefijo_modelos)

    for indicativo in indicativos:
        ruta = os.path.join(s.ruta_modelos, f"{indicativo}.joblib")
        print(f"Descargando {indicativo}...", end=" ", flush=True)
        if s3.descargar_modelo(indicativo, ruta):
            mb = os.path.getsize(ruta) / (1024 * 1024)
            print(f"OK ({mb:.0f} MB)")
        else:
            print("NO EXISTE en S3")


if __name__ == "__main__":
    main()
