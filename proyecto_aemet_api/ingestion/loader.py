"""Carga de datos en la base de datos."""
from __future__ import annotations

import asyncpg
import pandas as pd

# La AEMET y nuestra base de datos llaman a las mismas cosas con nombres distintos. 
# Por ejemplo, la AEMET dice "hrMedia" y nuestra base de datos dice "hrmedia". 
# Este diccionario es el "traductor": a la izquierda el nombre que da la AEMET, a la derecha el nombre que usa nuestra base de datos.
# (La tabla que manda es meteo.mediciones_diarias, definida en bd_meteo_v2.sql).
_MEDICIONES_COLUMNAS = {
    "fecha": "fecha",
    "indicativo": "indicativo",
    "tmed": "tmed",
    "prec": "prec",
    "tmin": "tmin",
    "tmax": "tmax",
    "dir": "dir",
    "velmedia": "velmedia",
    "racha": "racha",
    "sol": "sol",
    "presMax": "presmax",
    "presMin": "presmin",
    "hrMedia": "hrmedia",
    "hrMax": "hrmax",
    "hrMin": "hrmin",
    "pintMax": "pintmax",
    # Estas tres son "banderas" (verdadero/falso) que genera el transformer.
    "precAcum": "precAcum",
    "precIp": "precIp",
    "variasHoras": "variasHoras",
}

# Lo mismo pero para la tabla de estaciones.
_ESTACIONES_COLUMNAS = {
    "indicativo": "indicativo",
    "nombre": "nombre",
    "provincia": "provincia",
    "altitud": "altitud",
    "latitud": "latitud",
    "longitud": "longitud",
}


def _registros(df: pd.DataFrame, mapeo: dict[str, str]) -> list[dict]:
    # Prepara la tabla para guardarla en la base de datos:
    #   1) Se queda solo con las columnas que nos interesan.
    #   2) Les cambia el nombre usando el "traductor" (mapeo).
    #   3) Cambia los valores vacios (NaN) por None, que es lo unico que la base
    #      de datos entiende como "sin dato".
    # Devuelve una lista de filas, donde cada fila es un diccionario.
    existentes = {k: v for k, v in mapeo.items() if k in df.columns}
    renombrado = df[list(existentes)].rename(columns=existentes)
    limpio = renombrado.astype(object).where(pd.notna(renombrado), None)
    return limpio.to_dict(orient="records")


class DataLoader:
    # Su unico trabajo es meter los datos ya limpios en la base de datos.
    # Es "idempotente": significa que se puede ejecutar varias veces sin miedo.
    # Si un dato ya existia, lo actualiza en vez de duplicarlo o dar error. 
    # Esto es importante porque la ingesta se repite cada 5 dias y a veces la AEMET corrige datos de dias anteriores.

    def __init__(self, pool: asyncpg.Pool) -> None:
        # pool = grupo de conexiones a la base de datos ya abiertas y reutilizables.
        self._pool = pool

    async def cargar_estaciones(self, df: pd.DataFrame) -> int:
        # Guarda la lista de estaciones. Devuelve cuantas filas guardo.
        filas = _registros(df, _ESTACIONES_COLUMNAS)
        if not filas:
            return 0  # no habia nada que guardar

        columnas = list(_ESTACIONES_COLUMNAS.values())
        async with self._pool.acquire() as conn:
            await conn.executemany(
                f"""
                INSERT INTO meteo.estaciones ({", ".join(columnas)})
                VALUES ({_placeholders(columnas)})
                ON CONFLICT (indicativo) DO UPDATE SET
                    nombre = EXCLUDED.nombre,
                    provincia = EXCLUDED.provincia,
                    altitud = EXCLUDED.altitud,
                    latitud = EXCLUDED.latitud,
                    longitud = EXCLUDED.longitud
                """,
                [[f.get(c) for c in columnas] for f in filas],
            )
        return len(filas)

    async def cargar_mediciones(self, df: pd.DataFrame) -> int:
        # Guarda las mediciones diarias. Devuelve cuantas filas guardo.
        filas = _registros(df, _MEDICIONES_COLUMNAS)
        if not filas:
            return 0

        columnas = list(_MEDICIONES_COLUMNAS.values())
        # La pareja (indicativo, fecha) identifica cada fila de forma unica. 
        # Si ya existe, actualizamos el resto de columnas con los valores nuevos.
        columnas_update = [c for c in columnas if c not in ("fecha", "indicativo")]
        asignaciones = ", ".join(f"{c} = EXCLUDED.{c}" for c in columnas_update)

        async with self._pool.acquire() as conn:
            await conn.executemany(
                f"""
                INSERT INTO meteo.mediciones_diarias ({", ".join(columnas)})
                VALUES ({_placeholders(columnas)})
                ON CONFLICT (indicativo, fecha) DO UPDATE SET {asignaciones}
                """,
                [[f.get(c) for c in columnas] for f in filas],
            )
        return len(filas)


def _placeholders(columnas: list[str]) -> str:
    # Por seguridad, los valores NO se meten directamente en el texto del SQL. 
    # En su lugar se ponen "huecos" ($1, $2...) y la libreria rellena esos huecos con los valores de forma segura.
    return ", ".join(f"${i}" for i in range(1, len(columnas) + 1))
