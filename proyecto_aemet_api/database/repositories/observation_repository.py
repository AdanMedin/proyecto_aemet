"""Repositorio de acceso a datos de mediciones diarias."""

from __future__ import annotations
from datetime import date
import asyncpg

class ObservationRepository:
    # Unica capa que habla con la base de datos para las mediciones diarias.
    # De aqui salen los datos limpios que alimentan a los modelos.

    # Coge las ultimas n mediciones registradas de una estacion que tengan tmed y hrmedia (las dos columnas que necesita el modelo). 
    # Se ordena de mas reciente a mas antigua y se limita a n en la propia consulta.
    _QUERY_ULTIMAS = """
        SELECT fecha, tmed, hrmedia
        FROM meteo.mediciones_diarias
        WHERE indicativo = $1
          AND tmed IS NOT NULL
          AND hrmedia IS NOT NULL
        ORDER BY fecha DESC
        LIMIT $2
    """

   # Obtiene la temperatura media histórica de un conjunto de estaciones
    # entre dos fechas.
    # Se utiliza en el endpoint EDA para recuperar el histórico de las
    # estaciones cercanas al municipio solicitado.

    _QUERY_TEMPERATURAS_PERIODO = """
        SELECT fecha, indicativo, tmed
        FROM meteo.mediciones_diarias
        WHERE indicativo = ANY($1)
          AND fecha BETWEEN $2 AND $3
          AND tmed IS NOT NULL
        ORDER BY fecha, indicativo
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def obtener_ultimas_mediciones(
        self,
        indicativo: str,
        n: int,
    ) -> list[asyncpg.Record]:
        # Devuelve las mediciones en orden cronológico (antiguo → reciente),
        # que es el formato esperado por el constructor de features.
        async with self._pool.acquire() as connection:
            filas = await connection.fetch(
                self._QUERY_ULTIMAS,
                indicativo,
                n,
            )
        return list(reversed(filas))


    async def obtener_temperaturas_periodo(
        self,
        indicativos: list[str],
        fecha_inicio: date,
        fecha_fin: date,
    ) -> list[asyncpg.Record]:
        # Devuelve todas las temperaturas medias disponibles para un conjunto
        # de estaciones dentro de un intervalo de fechas.
        # El resultado queda ordenado por fecha e indicativo para facilitar
        # el tratamiento posterior.

        async with self._pool.acquire() as connection:
            filas = await connection.fetch(
                self._QUERY_TEMPERATURAS_PERIODO,
                indicativos,
                fecha_inicio,
                fecha_fin,
            )
        return list(filas)