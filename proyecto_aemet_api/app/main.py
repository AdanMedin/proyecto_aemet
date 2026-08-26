"""Punto de entrada de la aplicación FastAPI."""

from __future__ import annotations
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from proyecto_aemet_api.app.api.v1.router import api_router
from proyecto_aemet_api.core.config import get_settings
from proyecto_aemet_api.database.repositories.observation_repository import (
    ObservationRepository,
)
from proyecto_aemet_api.database.repositories.station_repository import StationRepository
from proyecto_aemet_api.database.session import cerrar_pool, crear_pool
from proyecto_aemet_api.ingestion.loader import DataLoader
from proyecto_aemet_api.ml.predictor import (
    RegistroModelosLazy,
    crear_cargador_con_s3,
    crear_cargador_desde_disco,
    crear_medidor_tamano,
    crear_medidor_tamano_s3,
)
from proyecto_aemet_api.services.forecast_service import CacheEstaciones, PredictorMeteo
from proyecto_aemet_api.services.eda_service import EDAService
from proyecto_aemet_api.services.ingestion_service import IngestionService
from proyecto_aemet_api.services.training_service import TrainingService

@asynccontextmanager
# Un lifespan define que pasa al ARRANCAR y al APAGAR el servidor. Todo lo de antes del yield se ejecuta al arrancar, lo de despues, al parar.
async def lifespan(app: FastAPI):

    # Lee la configuracion de la app (variables de entorno, .env, etc) de aqui se obtienen la conexion a la base de datos y otros parametros.
    settings = get_settings()

    # 1) Conexion a la base de datos (pool de conexiones).
    pool = await crear_pool(settings.database_dsn)

    # 2) Capa de datos: cache de estaciones + repositorio de mediciones diarias.
    # La cache solo carga estaciones que tengan datos recientes Y modelo.
    repositorio_estaciones = StationRepository(pool)

    # Funcion que dice que estaciones tienen modelo (para filtrar la cache).
    if settings.s3_bucket:
        from proyecto_aemet_api.ml.s3_storage import S3Storage
        _s3 = S3Storage(settings.s3_bucket, settings.aws_region, settings.s3_prefijo_modelos)
        listar_modelos = _s3.listar_modelos
    else:
        # En local: las que tengan .joblib en la carpeta de artefactos.
        def listar_modelos() -> set[str]:
            ruta = Path(settings.ruta_modelos)
            return {f.stem for f in ruta.glob("*.joblib")}

    cache_estaciones = CacheEstaciones(
        repositorio_estaciones,
        ttl_segundos=settings.estaciones_ttl_segundos,
        listar_modelos=listar_modelos,
    )

    cache_estaciones_eda = CacheEstaciones(
        repositorio_estaciones,
        ttl_segundos=settings.estaciones_ttl_segundos,
        listar_modelos=None,
        usar_todas_estaciones=True,
    )

    repositorio_mediciones = ObservationRepository(pool)

    # 3) Registro de modelos de ML con carga perezosa y limite de memoria.
    # Si hay bucket S3 configurado, los modelos se leen de la nube DIRECTO A
    # MEMORIA (sin ocupar disco). Si no, se leen de la carpeta local.
    if settings.s3_bucket:
        cargador = crear_cargador_con_s3(
            settings.s3_bucket,
            settings.aws_region,
            settings.s3_prefijo_modelos,
        )
        medidor = crear_medidor_tamano_s3(
            settings.s3_bucket,
            settings.aws_region,
            settings.s3_prefijo_modelos,
            settings.modelo_tamano_defecto_mb,
        )
    else:
        cargador = crear_cargador_desde_disco(settings.ruta_modelos)
        medidor = crear_medidor_tamano(
            settings.ruta_modelos, settings.modelo_tamano_defecto_mb
        )
    registro_modelos = RegistroModelosLazy(
        cargador=cargador,
        max_memoria_mb=settings.modelos_max_memoria_mb,
        obtener_tamano_mb=medidor,
    )

    # 4) Servicio que junta cache de estaciones, modelos y mediciones de la BD.
    predictor = PredictorMeteo(cache_estaciones, registro_modelos, repositorio_mediciones)

    # <<< NUEVO >>>

    eda_service = EDAService(observation_repository=repositorio_mediciones, cache_estaciones=cache_estaciones_eda,)

    # 5) Servicios de operaciones: ingesta (AEMET -> BD) y reentrenamiento (BD -> modelos).
    ingestion = IngestionService(DataLoader(pool))
    training = TrainingService(
        pool,
        ruta_salida=settings.ruta_modelos,
        s3_bucket=settings.s3_bucket,
        aws_region=settings.aws_region,
        s3_prefijo=settings.s3_prefijo_modelos,
        s3_prefijo_historico=settings.s3_prefijo_modelos_historicos,
        ruta_historicos=settings.ruta_modelos_historicos,
    )

    # Guardamos en app.state lo que necesitaremos durante las peticiones. 
    # Esto guarda el pool de conexiones y el predictor(cache con estaciones cercanas + modelos) en el estado global de la aplicación, para poder acceder a ellos desde cualquier endpoint sin tener que recrearlos en cada petición.
    app.state.pool = pool
    app.state.predictor = predictor
    app.state.eda_service = eda_service
    app.state.ingestion = ingestion
    app.state.training = training
    app.state.settings = settings  # los endpoints de admin lo usan para rutas y buckets

    yield  # aqui la app esta viva atendiendo peticiones, despues de esto se ejecuta el codigo de apagado.

    # Al apagar: cerramos la conexion a base de datos de manera ordenada.
    await cerrar_pool(pool)

# Crea la instancia principal de la aplicación con su titulo y le pasa el lifespan definido arriba.
app = FastAPI(title="AEMET Forecast API", lifespan=lifespan)
# monta ese conjunto de rutas (api_router) dentro de la app principal y se le añade el prefijo /api/v1 a todas las rutas de ese router.
# Esto se hace para mantener la app ordenada y modular, tipicamente se separan las rutas por versiones o funcionalidades.
app.include_router(api_router, prefix="/api/v1")