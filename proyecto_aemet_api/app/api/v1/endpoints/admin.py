"""Endpoints de administración: ingesta y reentrenamiento bajo demanda."""
from __future__ import annotations

from fastapi import APIRouter, Request

# Un "router" agrupa rutas relacionadas. Estas son las rutas de administracion:
# no las usa el usuario normal, sino el sistema (o nosotros) para mantener los
# datos y los modelos al dia.
router = APIRouter()


@router.post("/admin/ingestar")
async def ingestar(request: Request, dias: int = 5, estaciones: bool = False):
    # Lanza la descarga de datos de la AEMET. 
    # El scheduler llama a esta ruta cada 5 dias de forma automatica, pero tambien se puede llamar a mano para probar. dias=5 significa "trae los ultimos 5 dias".
    
    # request.app.state.ingestion recupera el servicio de ingesta que se creo una sola vez al arrancar la API (no se crea de nuevo en cada llamada).
    servicio = request.app.state.ingestion
    resultado = {"mediciones": await servicio.cargar_mediciones(dias)}
    if estaciones:
        # Solo descargamos el inventario de estaciones si nos lo piden, porque casi nunca cambia y es una descarga grande.
        resultado["estaciones"] = await servicio.cargar_estaciones()
    return resultado


@router.post("/admin/reentrenar")
async def reentrenar(request: Request):
    # Reentrena todos los modelos con los datos mas recientes de la base de datos y los sube a la nube si esta configurada. 
    # El scheduler llama aqui cada 15 dias de forma automatica.
    return await request.app.state.training.reentrenar()