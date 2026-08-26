"""Repositorio de acceso a datos de estaciones."""

from __future__ import annotations
import asyncpg

class StationRepository:
    # Es la capa que habla con la base de datos. El resto del codigo le pide datos a esta clase y no escribe SQL por su cuenta, asi todo el SQL queda en un solo sitio.

    # Solo trae estaciones que tengan datos RECIENTES: la ultima medicion no
    # puede tener mas de 7 dias (si no, la ventana de 20 dias seria demasiado
    # vieja para predecir mañana). Usa la vista ultimos_10_dias para ir rapido.
    #
    # Ademas, el modelo se filtra aparte: hay estaciones con datos pero sin
    # modelo entrenado (pocas mediciones historicas). Esas tampoco sirven.
    _QUERY_CON_COORDENADAS = """
        SELECT DISTINCT e.indicativo, e.nombre, e.provincia, e.latitud, e.longitud
        FROM meteo.estaciones e
        JOIN meteo.ultimos_10_dias m ON m.indicativo = e.indicativo
        WHERE e.latitud IS NOT NULL AND e.longitud IS NOT NULL
          AND m.tmed IS NOT NULL AND m.hrmedia IS NOT NULL
    """

     # Trae todas las estaciones con coordenadas.
    # Se utiliza en el endpoint EDA porque puede consultar
    # periodos históricos aunque la estación ya no tenga
    # mediciones recientes.
    _QUERY_TODAS_CON_COORDENADAS = """
        SELECT
            indicativo, nombre, provincia, latitud, longitud
        FROM meteo.estaciones
        WHERE latitud IS NOT NULL
          AND longitud IS NOT NULL
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def obtener_estaciones_con_coordenadas(self) -> list[asyncpg.Record]:
        # Pide prestada una conexion del pool, lanza la consulta y devuelve todas las filas (cada fila es una estacion con sus coordenadas).
        async with self._pool.acquire() as connection:
            return await connection.fetch(self._QUERY_CON_COORDENADAS)

    async def obtener_todas_estaciones_con_coordenadas(
        self,
    ) -> list[asyncpg.Record]:
        async with self._pool.acquire() as connection:
            return await connection.fetch(
                self._QUERY_TODAS_CON_COORDENADAS
            )
