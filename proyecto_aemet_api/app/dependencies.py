"""Dependencias inyectables de FastAPI."""

from __future__ import annotations
from fastapi import Request
from proyecto_aemet_api.services.forecast_service import PredictorMeteo
from proyecto_aemet_api.services.eda_service import EDAService

# FastAPI llamará a esta función y pasará su resultado al endpoint que la pida.
# El predictor se creó una sola vez al arrancar y vive en app.state.

def get_predictor(
    request: Request,
) -> PredictorMeteo:
    return request.app.state.predictor

# Servicio EDA creado al arrancar la aplicación.
# Igual que el predictor, únicamente lo recuperamos desde app.state.
def get_eda_service(
    request: Request,
) -> EDAService:
    return request.app.state.eda_service