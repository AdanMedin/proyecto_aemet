"""Esquemas Pydantic del forecasting."""

from datetime import date
from typing import Optional
from pydantic import BaseModel

# Lo que el cliente envia al pedir una prediccion (entrada). Pydantic valida automaticamente que lleguen los tipos correctos.
class PrediccionRequest(BaseModel):
    latitud: float
    longitud: float
    k: int = 5 # cuantas estaciones cercanas devolver
    max_distancia_km: float = 50.0  # radio maximo de busqueda

# Lo que la API DEVUELVE por cada estacion encontrada (salida).
class EstacionCercanaOut(BaseModel):
    indicativo: str
    nombre: str
    provincia: Optional[str]
    latitud: float
    longitud: float
    distancia_km: float
    tiene_modelo: bool

# Lo que la API DEVUELVE por cada estacion con prediccion (salida).
class PrediccionTemperaturaOut(BaseModel):
    indicativo: str
    nombre: str
    provincia: Optional[str]
    latitud: float
    longitud: float
    distancia_km: float
    fecha: date  # dia predicho (mañana)
    temperatura_prevista: float  # tmed prevista en grados Celsius

# Respuesta completa del endpoint de prediccion: las estaciones con su
# prediccion individual + la temperatura media ponderada por distancia.
class PrediccionResponse(BaseModel):
    fecha: date  # dia predicho (mañana)
    # Media ponderada: cada estacion pesa 1/distancia (mas cerca = mas peso).
    # Asi una estacion a 1km influye 10 veces mas que una a 10km.
    temperatura_ponderada: float
    estaciones: list[PrediccionTemperaturaOut]