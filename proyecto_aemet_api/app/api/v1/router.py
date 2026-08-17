"""Router principal de la API v1."""

from fastapi import APIRouter
from proyecto_aemet_api.app.api.v1.endpoints import admin, forecast, health

api_router = APIRouter()

# Enganchamos cada grupo de endpoints al router principal. Los tags solo sirven para agrupar y ordenar la documentacion automatica (/docs).
api_router.include_router(health.router, tags=["health"])
api_router.include_router(forecast.router, tags=["forecast"])
api_router.include_router(admin.router, tags=["admin"])
