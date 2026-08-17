"""Endpoint de predicción de temperatura."""

from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from proyecto_aemet_api.app.dependencies import get_predictor
from proyecto_aemet_api.schemas.forecast import PrediccionRequest, PrediccionTemperaturaOut
from proyecto_aemet_api.services.forecast_service import PredictorMeteo

router = APIRouter()

@router.post("/prediccion", response_model=list[PrediccionTemperaturaOut])
async def prediccion(
    req: PrediccionRequest,
    predictor: PredictorMeteo = Depends(get_predictor),
):
    # Depends(get_predictor) = "antes de ejecutar esto, dame el predictor".
    resultados = await predictor.predecir_temperatura(
        latitud=req.latitud,
        longitud=req.longitud,
        k=req.k,
        max_distancia_km=req.max_distancia_km,
    )

    if not resultados:
        # 404 = no hay estaciones con modelo y datos suficientes en ese radio.
        raise HTTPException(
            status_code=404,
            detail="No hay estaciones con predicción disponible dentro del radio indicado",
        )

    # Traducimos las fichas internas al esquema de salida que ve el cliente.
    return [
        PrediccionTemperaturaOut(
            indicativo=r.estacion.indicativo,
            nombre=r.estacion.nombre,
            provincia=r.estacion.provincia,
            latitud=r.estacion.latitud,
            longitud=r.estacion.longitud,
            distancia_km=r.estacion.distancia_km,
            fecha=r.fecha,
            temperatura_prevista=r.temperatura,
        )
        for r in resultados
    ]
