"""Cliente de la API de datos de AEMET."""
from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd
import requests

# La web de la AEMET no deja pedir mas de 15 dias de datos de golpe. Si queremos
# mas, hay que pedir los datos por bloques de 15 dias. Este numero guarda ese limite.
_MAX_DIAS_POR_PETICION = 15

# Direccion web base de la API de la AEMET. Si no esta en la configuracion,
# usamos la direccion oficial por defecto.
_BASE_URL = os.environ.get(
    "AEMET_BASE_URL", "https://opendata.aemet.es/opendata/api"
)


class AemetClient:
    # Esta clase es la unica que habla con la web de la AEMET. Su trabajo es descargar los datos "crudos" (tal como los da la AEMET, sin limpiar).
    # Devuelve tablas (DataFrames). La limpieza la hace el transformer despues.
    
    # DataFrame = una tabla en memoria, como una hoja de Excel (libreria pandas).

    def __init__(self, api_key: str | None = None) -> None:
        # La clave de la AEMET es como una contrasena que nos identifica. Si no nos la pasan, la lee de la configuracion (el archivo .env). Sin clave, la AEMET no nos deja descargar nada.
        self._api_key = api_key or os.environ["AEMET_API_KEY"]

    def _resolver_descarga(self, url: str) -> list[dict]:
        # La AEMET funciona en DOS pasos, algo poco habitual:
        #   1) Le pedimos los datos y NO nos los da directamente. Nos devuelve una direccion web temporal donde estan.
        #   2) Tenemos que ir a esa direccion temporal para descargar los datos de verdad.
        # Este metodo hace los dos pasos y devuelve los datos finales.
        respuesta = requests.get(url, params={"api_key": self._api_key}, timeout=30)
        respuesta.raise_for_status()  # si la web da error, paramos y avisamos
        resultado = respuesta.json()

        if resultado.get("estado") != 200:
            # La AEMET devolvio un mensaje de error (clave mal, rango mal...).
            raise RuntimeError(f"Error de AEMET: {resultado.get('descripcion')}")

        # Segundo paso: descargar los datos de la direccion temporal.
        respuesta_datos = requests.get(resultado["datos"], timeout=30)
        respuesta_datos.raise_for_status()
        return respuesta_datos.json()

    def obtener_mediciones(
        self, fecha_inicio: date, fecha_fin: date
    ) -> pd.DataFrame:
        # Descarga las mediciones de TODAS las estaciones entre dos fechas.
        # Como la AEMET solo deja 15 dias por peticion, si el rango es mas largo lo partimos en bloques de 15 dias y los juntamos al final.
        if fecha_fin < fecha_inicio:
            raise ValueError("fecha_fin no puede ser anterior a fecha_inicio")

        bloques: list[pd.DataFrame] = []
        cursor = fecha_inicio
        while cursor <= fecha_fin:
            # Calculamos hasta donde llega este bloque (maximo 15 dias).
            fin_bloque = min(cursor + timedelta(days=_MAX_DIAS_POR_PETICION - 1), fecha_fin)
            url = (
                f"{_BASE_URL}/valores/climatologicos/diarios/datos/"
                f"fechaini/{_fmt(cursor)}/fechafin/{_fmt(fin_bloque)}/todasestaciones"
            )
            bloques.append(pd.DataFrame(self._resolver_descarga(url)))
            # El siguiente bloque empieza justo despues de donde acabo este.
            cursor = fin_bloque + timedelta(days=1)

        if not bloques:
            return pd.DataFrame() # no se descargo nada: tabla vacia
        # Juntamos todos los bloques en una sola tabla.
        return pd.concat(bloques, ignore_index=True)

    def obtener_estaciones(self) -> pd.DataFrame:
        # Descarga el inventario completo de estaciones (nombre, provincia,
        # coordenadas...). Es una sola peticion, sin trocear.
        url = f"{_BASE_URL}/valores/climatologicos/inventarioestaciones/todasestaciones"
        return pd.DataFrame(self._resolver_descarga(url))


def _fmt(fecha: date) -> str:
    # La AEMET quiere las fechas en un formato muy concreto, por ejemplo "2026-08-17T00:00:00UTC". Esta funcion convierte una fecha normal a eso.
    return f"{fecha.isoformat()}T00:00:00UTC"
