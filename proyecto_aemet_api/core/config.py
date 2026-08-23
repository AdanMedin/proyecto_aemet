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
    ruta_modelos: str = "proyecto_aemet_api/ml/artifacts"

    # Presupuesto maximo de RAM (en MB) para tener modelos cargados a la vez.
    modelos_max_memoria_mb: float = 8000.0

    # Tamano por defecto (MB) si no se puede medir un modelo en disco.
    modelo_tamano_defecto_mb: float = 18.0

    # Segundos que la cache de estaciones es valida antes de recargar (TTL).
    estaciones_ttl_segundos: float = 3600.0

    # --- AWS S3 (subida de modelos) ---
    # Todo esto se lee del .env (aqui no va nada de AWS escrito).
    # Bucket donde estan los modelos .joblib en la nube. Vacio = desactivado
    # (los modelos se leen solo del disco local, modo desarrollo).
    s3_bucket_modelos: str = ""
    aws_region: str = "eu-west-1"

    # Subcarpeta dentro del bucket donde estan los modelos.
    s3_prefijo_modelos: str = "modelos"

    # Subcarpeta de respaldo: aqui se mueven los modelos actuales justo antes
    # de subir los nuevos (por si algo sale mal). Solo guarda UNA version.
    s3_prefijo_modelos_historicos: str = "modelos_historicos"

    # Carpeta local de respaldo de modelos (mismo comportamiento sin S3).
    ruta_modelos_historicos: str = "proyecto_aemet_api/ml/artifacts_historicos"

    # --- AWS S3 (datos crudos de AEMET) ---
    # Bucket donde se guardan los pickles con los datos descargados (sin limpiar).
    # Vacio = no se suben a la nube, se quedan solo en disco local.
    s3_bucket_datos_raw: str = ""
    s3_prefijo_datos_raw: str = "raw"

    # Carpeta local donde se guardan los datos crudos descargados (pickle + csv).
    ruta_datos_raw: str = "proyecto_aemet_api/resources"

@lru_cache
def get_settings() -> Settings:
    # lru_cache crea el objeto Settings una sola vez y reutiliza esa misma instancia en todas las llamadas (no vuelve a leer el .env cada vez).
    return Settings()
