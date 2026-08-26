"""Endpoint para consultas históricas EDA."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from proyecto_aemet_api.app.dependencies import get_eda_service
from proyecto_aemet_api.schemas.eda import (
    EDARequest,
    EDAResponse,
)
from proyecto_aemet_api.services.eda_service import EDAService


router = APIRouter()


@router.post("/eda", response_model=EDAResponse)
async def eda(
    req: EDARequest,
    eda_service: EDAService = Depends(get_eda_service),
):
    try:
        resultado = await eda_service.obtener_temperaturas_historicas(
            consulta_usuario=req.consulta
        )

    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e),
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al obtener los datos históricos: {e}",
        )

    consulta = resultado["consulta"]
    temperaturas = resultado["temperaturas"]

    return EDAResponse(
        consulta_original=req.consulta,
        municipio=consulta.municipio,
        provincia=consulta.provincia,
        fecha_inicio=consulta.fecha_inicio,
        fecha_fin=consulta.fecha_fin,
        datos=temperaturas,
    )