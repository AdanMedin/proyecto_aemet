"""Handlers para AWS Lambda: ingesta y reentrenamiento.

Este módulo es un ADAPTADOR que permite que los servicios existentes (IngestionService, TrainingService) funcionen en Lambda.

Lambda crea un nuevo proceso para cada ejecución:
  1) Carga este módulo
  2) Crea conexión a BD
  3) Ejecuta el servicio
  4) Cierra conexión
  5) El proceso muere

Los handlers se configuran en AWS Lambda como punto de entrada:
  - Para ingesta: proyecto_aemet_api.aws.lambda_handlers.handler_ingesta
  - Para reentrenamiento: proyecto_aemet_api.aws.lambda_handlers.handler_reentrenamiento
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from proyecto_aemet_api.core.config import get_settings
from proyecto_aemet_api.database.session import cerrar_pool, crear_pool
from proyecto_aemet_api.ingestion.loader import DataLoader
from proyecto_aemet_api.services.ingestion_service import IngestionService
from proyecto_aemet_api.services.training_service import TrainingService

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


async def _ingesta_async(dias: int = 5, cargar_estaciones: bool = False) -> dict[str, Any]:
    """Ejecuta la ingesta de datos de AEMET en contexto de Lambda.

    Argumentos:
    dias: Número de días atrás a descargar (default: 5)
    cargar_estaciones: Si True, también actualiza el inventario de estaciones

    Returns:
    Dict con resultado de la ingesta {"mediciones": N, "estaciones": M}
    """
    settings = get_settings()
    pool = await crear_pool(settings.database_dsn)

    try:
        loader = DataLoader(pool)
        service = IngestionService(loader)

        resultado = {"mediciones": await service.cargar_mediciones(dias)}

        if cargar_estaciones:
            resultado["estaciones"] = await service.cargar_estaciones()

        logger.info(f"Ingesta completada: {resultado}")
        return resultado

    except Exception as e:
        logger.error(f"Error en ingesta: {str(e)}", exc_info=True)
        raise

    finally:
        await cerrar_pool(pool)


async def _reentrenamiento_async() -> dict[str, Any]:
    """Ejecuta el reentrenamiento de modelos en contexto de Lambda.

    Entrena modelos usando el histórico completo de BD y sube los modelos a S3 si está configurado.

    Returns:
    Dict con resultado {"modelos": N, "sin_datos": M, "subidos_s3": K}
    """
    settings = get_settings()
    pool = await crear_pool(settings.database_dsn)

    try:
        service = TrainingService(
            pool,
            ruta_salida=settings.ruta_modelos,
            s3_bucket=settings.s3_bucket_modelos,
            aws_region=settings.aws_region,
        )

        resultado = await service.reentrenar()
        logger.info(f"Reentrenamiento completado: {resultado}")
        return resultado

    except Exception as e:
        logger.error(f"Error en reentrenamiento: {str(e)}", exc_info=True)
        raise

    finally:
        await cerrar_pool(pool)


def handler_ingesta(event: dict, context: Any) -> dict:
    """Handler de Lambda para ingesta de datos de AEMET.

    Event esperado:
    "dias": 5, (opcional) días atrás a descargar
    "estaciones": false (opcional) actualizar inventario de estaciones

    Retorna:
    "statusCode": 200 o 500,
    "body": JSON con resultado o error
    """
    logger.info(f"Iniciando ingesta. Event: {event}")

    try:
        dias = event.get("dias", 5)
        cargar_estaciones = event.get("estaciones", False)

        # Ejecuta la función async en el event loop de Lambda
        resultado = asyncio.run(_ingesta_async(dias, cargar_estaciones))

        return {
            "statusCode": 200,
            "body": json.dumps(resultado),
        }

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Fallo en handler_ingesta: {error_msg}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": error_msg}),
        }


def handler_reentrenamiento(event: dict, context: Any) -> dict:
    """Handler de Lambda para reentrenamiento de modelos.

    Event no espera parámetros.

    Retorna:
    "statusCode": 200 o 500,
    "body": JSON con resultado o error
    """
    logger.info("Iniciando reentrenamiento.")

    try:
        resultado = asyncio.run(_reentrenamiento_async())

        return {
            "statusCode": 200,
            "body": json.dumps(resultado),
        }

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Fallo en handler_reentrenamiento: {error_msg}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": error_msg}),
        }


# Aliases para que sea más fácil usar estos handlers en Lambda
ingestar = handler_ingesta
reentrenar = handler_reentrenamiento
