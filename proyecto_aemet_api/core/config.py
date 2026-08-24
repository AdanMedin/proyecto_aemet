"""Configuración central de la aplicación."""

from functools import lru_cache
from pathlib import Path

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Raíz del proyecto (2 niveles arriba de este archivo: core/ -> proyecto_aemet_api/ -> raíz)
_ENV_FILE = Path(__file__).parents[2] / ".env"
load_dotenv(_ENV_FILE)  # Carga las variables de entorno del .env en el entorno de ejecución (os.environ)

class Settings(BaseSettings):
    # BaseSettings lee automaticamente estos valores de variables de entorno o de un archivo .env, asi no dejamos contrasenhas escritas en el codigo.
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    # DSN = "Data Source Name": la cadena de conexion a PostgreSQL.
    # OBLIGATORIO y sin valor por defecto: se define en el .env (DATABASE_DSN) para no dejar credenciales escritas en el codigo.
    database_dsn: str

    # Carpeta donde estan los modelos entrenados (.joblib), uno por estacion.
    # Local: datos/MODELOS_RF/modelos_RF/ (espejo de S3). En S3: MODELOS_RF/modelos_RF/
    ruta_modelos: str = "datos/MODELOS_RF/modelos_RF"

    # Presupuesto maximo de RAM (en MB) para tener modelos cargados a la vez.
    modelos_max_memoria_mb: float = 8000.0

    # Tamano por defecto (MB) si no se puede medir un modelo en disco.
    modelo_tamano_defecto_mb: float = 18.0

    # Segundos que la cache de estaciones es valida antes de recargar (TTL).
    estaciones_ttl_segundos: float = 3600.0

    # --- AWS S3 (UN SOLO bucket para todo: aemet-hab-2026) ---
    # Bucket unico: dentro van mediciones diarias, historico, estaciones y modelos.
    # Vacio = desactivado (modo desarrollo: todo se lee/guarda en disco local).
    s3_bucket: str = ""
    aws_region: str = "eu-west-1"

    # Modelos dentro del bucket: MODELOS_RF/modelos_RF/*.joblib
    s3_prefijo_modelos: str = "MODELOS_RF/modelos_RF"

    # Respaldo: MODELOS_RF/respaldo_RF/ (aqui se mueven los modelos actuales
    # justo antes de subir los nuevos). Solo guarda UNA version.
    s3_prefijo_modelos_historicos: str = "MODELOS_RF/respaldo_RF"

    # Carpeta local de respaldo de modelos (espejo de S3).
    ruta_modelos_historicos: str = "datos/MODELOS_RF/respaldo_RF"

    # --- Datos crudos de AEMET ---
    # En S3 y en LOCAL van en la RAIZ del bucket/datos/ (sin subcarpetas):
    #   estaciones.pkl, ALL_10_YEARS, "2026-08-17 00:00:00_2026-08-17T00:00:00UTC"
    # Local espeja esa misma estructura en la carpeta datos/ de la raiz:
    ruta_datos: str = "datos"

@lru_cache
def get_settings() -> Settings:
    # lru_cache crea el objeto Settings una sola vez y reutiliza esa misma instancia en todas las llamadas (no vuelve a leer el .env cada vez).
    return Settings()
