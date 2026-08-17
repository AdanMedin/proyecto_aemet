"""Dependencias inyectables de FastAPI."""

from __future__ import annotations
from fastapi import Request
from proyecto_aemet_api.services.forecast_service import PredictorMeteo

    # FastAPI llamara a esta funcion y pasara su resultado al endpoint que la pida.
    # El predictor se creo una sola vez al arrancar y vive en app.state, asi que aqui solo lo recuperamos.
    # Se podría crear directasmente en la clase forecast.py, pero asi queda mas ordenado y desacoplado si en un futuro necesitamos utilizar el método get_predictor para otros endpoints o servicios.
def get_predictor(request: Request) -> PredictorMeteo:

    return request.app.state.predictor
