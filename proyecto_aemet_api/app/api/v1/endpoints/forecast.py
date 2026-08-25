"""Endpoint de predicción de temperatura."""

from __future__ import annotations
import asyncio
from fastapi import APIRouter, Depends, HTTPException
from proyecto_aemet_api.app.dependencies import get_predictor
from proyecto_aemet_api.schemas.forecast import (
    PrediccionRequest,
    PrediccionResponse,
    PrediccionTemperaturaOut,
)
from proyecto_aemet_api.services.forecast_service import PredictorMeteo
from proyecto_aemet_api.services.coordenadas_service import obtener_coordenadas

router = APIRouter()

@router.post("/prediccion", response_model=PrediccionResponse)
async def prediccion(
    req: PrediccionRequest,
    predictor: PredictorMeteo = Depends(get_predictor),
):
    # Depends(get_predictor) = "antes de ejecutar esto, dame el predictor".
    # resultados = await predictor.predecir_temperatura(
    #     latitud=req.latitud,
    #     longitud=req.longitud,
    #     k=req.k,
    #     max_distancia_km=req.max_distancia_km,
    #)

     # Convertimos el nombre del municipio en coordenadas
    try:
        coordenadas = await asyncio.to_thread(
            obtener_coordenadas,
            req.municipio
        )

    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"No se han podido obtener las coordenadas del municipio: {e}",
        )

    resultados = await predictor.predecir_temperatura(
        latitud=coordenadas.latitud,
        longitud=coordenadas.longitud,
        #k=req.k,
        #max_distancia_km=req.max_distancia_km,
    )

    if not resultados:
        # 404 = no hay estaciones con modelo y datos suficientes en ese radio.
        raise HTTPException(
            status_code=404,
            detail="No hay estaciones con predicción disponible dentro del radio indicado",
        )

    # Traducimos las fichas internas al esquema de salida que ve el cliente.
    estaciones = [
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

    # Temperatura media ponderada por distancia (inversa): 1/distancia.
    # Una estacion a 1km pesa 10 veces mas que una a 10km.
    #
    # EXCEPCION: si hay alguna estacion a menos de 0.5 km del punto pedido,
    # se considera que el punto ES esa estacion (o esta practicamente encima),
    # y se devuelve su prediccion directamente, sin mezclar con las demas.
    cercana = next((e for e in estaciones if e.distancia_km < 0.5), None)
    if cercana is not None:
        temperatura_ponderada = cercana.temperatura_prevista
    else:
        pesos = [1.0 / max(e.distancia_km, 0.1) for e in estaciones]
        suma_pesos = sum(pesos)
        temperatura_ponderada = sum(
            e.temperatura_prevista * p for e, p in zip(estaciones, pesos)
        ) / suma_pesos

    return PrediccionResponse(
        municipio=coordenadas.municipio,
        provincia=coordenadas.provincia,
        fecha=estaciones[0].fecha,
        temperatura_ponderada=round(temperatura_ponderada,1,),
        estaciones=estaciones,
)
