"""Repositorio de acceso a datos de estaciones."""
from __future__ import annotations

import asyncpg


class StationRepository:
    # Es la unica capa que habla con la base de datos. El resto del codigo le pide datos a
    # esta clase y no escribe SQL por su cuenta: asi todo el SQL queda en un solo sitio.

    _QUERY_CON_COORDENADAS = """
        SELECT indicativo, nombre, provincia, latitud, longitud
        FROM meteo.estaciones
        WHERE latitud IS NOT NULL AND longitud IS NOT NULL
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def obtener_estaciones_con_coordenadas(self) -> list[asyncpg.Record]:
        # Pide prestada una conexion del pool, lanza la consulta y devuelve
        # todas las filas (cada fila = una estacion con sus coordenadas).
        async with self._pool.acquire() as connection:
            return await connection.fetch(self._QUERY_CON_COORDENADAS)
