"""Esquemas Pydantic para consultas históricas EDA."""

from datetime import date

from pydantic import BaseModel


class EDARequest(BaseModel):
    consulta: str


class ConsultaEDA(BaseModel):
    municipio: str
    fecha_inicio: date
    fecha_fin: date


class ConsultaEDACompleta(BaseModel):
    municipio: str
    provincia: str
    latitud: float
    longitud: float
    fecha_inicio: date
    fecha_fin: date


class TemperaturaHistoricaOut(BaseModel):
    fecha: date
    temperatura_media: float | None


class EDAResponse(BaseModel):
    consulta_original: str
    municipio: str
    provincia: str
    fecha_inicio: date
    fecha_fin: date
    datos: list[TemperaturaHistoricaOut]