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
    fecha: date  # dia predicho (siguiente a la ultima medicion)
    temperatura_prevista: float  # tmed prevista en grados Celsius