"""Configuración central de la aplicación."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Raíz del proyecto (2 niveles arriba de este archivo: core/ -> proyecto_aemet_api/ -> raíz)
_ENV_FILE = Path(__file__).parents[2] / ".env"

class Settings(BaseSettings):
    # BaseSettings lee automaticamente estos valores de variables de entorno o de un archivo .env, asi no dejamos contrasenhas escritas en el codigo.
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    # DSN = "Data Source Name": la cadena de conexion a PostgreSQL.
    # OBLIGATORIO y sin valor por defecto: se define en el .env (DATABASE_DSN) para no dejar credenciales escritas en el codigo.
    database_dsn: str

    # Carpeta donde estan los modelos entrenados (.joblib), uno por estacion.
    ruta_modelos: str = "proyecto_aemet_api/ml/artifacts"

    # Presupuesto maximo de RAM (en MB) para tener modelos cargados a la vez.
    modelos_max_memoria_mb: float = 8000.0

    # Tamano por defecto (MB) si no se puede medir un modelo en disco.
    modelo_tamano_defecto_mb: float = 18.0

    # Segundos que la cache de estaciones es valida antes de recargar (TTL).
    estaciones_ttl_segundos: float = 3600.0

    # --- AWS S3 (subida de modelos) ---
    # Bucket donde el reentrenamiento sube los .joblib. Vacio = desactivado (los modelos se quedan solo en disco local).
    s3_bucket_modelos: str = ""
    aws_region: str = "eu-west-1"

@lru_cache
def get_settings() -> Settings:
    # lru_cache crea el objeto Settings una sola vez y reutiliza esa misma instancia en todas las llamadas (no vuelve a leer el .env cada vez).
    return Settings()
