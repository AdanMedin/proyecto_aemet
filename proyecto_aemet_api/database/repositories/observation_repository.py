"""Repositorio de acceso a datos de mediciones diarias."""
from __future__ import annotations

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

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def obtener_ultimas_mediciones(
        self, indicativo: str, n: int
    ) -> list[asyncpg.Record]:
        # Devuelve las mediciones en orden CRONOLOGICO (antiguo - reciente), que es el orden que espera el constructor de features.
        async with self._pool.acquire() as connection:
            filas = await connection.fetch(self._QUERY_ULTIMAS, indicativo, n)
        return list(reversed(filas))
