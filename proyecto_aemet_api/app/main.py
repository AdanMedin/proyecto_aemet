"""Punto de entrada de la aplicación FastAPI."""

from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI
from proyecto_aemet_api.app.api.v1.router import api_router
from proyecto_aemet_api.core.config import get_settings
from proyecto_aemet_api.database.repositories.observation_repository import (
    ObservationRepository,
)
from proyecto_aemet_api.database.repositories.station_repository import StationRepository
from proyecto_aemet_api.database.session import cerrar_pool, crear_pool
from proyecto_aemet_api.ml.predictor import (
    RegistroModelosLazy,
    crear_cargador_desde_disco,
    crear_medidor_tamano,
)
from proyecto_aemet_api.services.forecast_service import CacheEstaciones, PredictorMeteo

@asynccontextmanager
# Un lifespan define que pasa al ARRANCAR y al APAGAR el servidor. Todo lo de antes del yield se ejecuta al arrancar, lo de despues, al parar.
async def lifespan(app: FastAPI):

    # Lee la configuracion de la app (variables de entorno, .env, etc) de aqui se obtienen la conexion a la base de datos y otros parametros.
    settings = get_settings()

    # 1) Conexion a la base de datos (pool de conexiones).
    pool = await crear_pool(settings.database_dsn)

    # 2) Capa de datos: cache de estaciones + repositorio de mediciones diarias.
    repositorio_estaciones = StationRepository(pool)
    cache_estaciones = CacheEstaciones(
        repositorio_estaciones, ttl_segundos=settings.estaciones_ttl_segundos
    )
    repositorio_mediciones = ObservationRepository(pool)

    # 3) Registro de modelos de ML con carga perezosa y limite de memoria.
    registro_modelos = RegistroModelosLazy(
        cargador=crear_cargador_desde_disco(settings.ruta_modelos),
        max_memoria_mb=settings.modelos_max_memoria_mb,
        obtener_tamano_mb=crear_medidor_tamano(
            settings.ruta_modelos, settings.modelo_tamano_defecto_mb
        ),
    )

    # 4) Servicio que junta cache de estaciones, modelos y mediciones de la BD.
    predictor = PredictorMeteo(cache_estaciones, registro_modelos, repositorio_mediciones)

    # Guardamos en app.state lo que necesitaremos durante las peticiones. 
    # Esto guarda el pool de conexiones y el predictor(cache con estaciones cercanas + modelos) en el estado global de la aplicación, para poder acceder a ellos desde cualquier endpoint sin tener que recrearlos en cada petición.
    app.state.pool = pool
    app.state.predictor = predictor

    yield  # aqui la app esta viva atendiendo peticiones, despues de esto se ejecuta el codigo de apagado.

    # Al apagar: cerramos la conexion a base de datos de manera ordenada.
    await cerrar_pool(pool)

# Crea la instancia principal de la aplicación con su titulo y le pasa el lifespan definido arriba.
app = FastAPI(title="AEMET Forecast API", lifespan=lifespan)
# monta ese conjunto de rutas (api_router) dentro de la app principal y se le añade el prefijo /api/v1 a todas las rutas de ese router.
# Esto se hace para mantener la app ordenada y modular, tipicamente se separan las rutas por versiones o funcionalidades.
app.include_router(api_router, prefix="/api/v1")