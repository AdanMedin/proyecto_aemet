"""
AWS Lambda: descarga del inventario de estaciones de la AEMET -> pickle en S3.

QUE HACE
--------
Descarga el inventario completo de estaciones meteorologicas de la AEMET
(unas 900: indicativo, nombre, provincia, altitud y coordenadas) y lo guarda
TAL CUAL en un pickle en S3. Ahi acaba su trabajo.

POR QUE EXISTE
--------------
Las mediciones diarias que descarga lambda_ingesta_aemet NO traen coordenadas;
solo este inventario las trae. Y sin coordenadas la API no puede buscar las
estaciones cercanas a un punto (el calculo de distancia las necesita). Ademas,
la base de datos tiene clave foranea: una medicion de una estacion que no
exista en meteo.estaciones no se puede guardar. Por eso este inventario es lo
primero que hay que cargar en un despliegue nuevo.

Es la equivalente en AWS a lo que en local hace el scheduler una vez al mes
llamando a POST /api/v1/admin/ingestar?estaciones=true.

CUANDO SE EJECUTA
-----------------
EventBridge Scheduler, el dia 1 de cada mes (cron(0 6 1 * ? *)). Las estaciones
casi nunca cambian, con una vez al mes basta. La PRIMERA vez hay que invocarla
a mano (evento {}) antes de activar la ingesta diaria de mediciones, si no las
mediciones se descartarian todas al no existir sus estaciones.

VARIABLES DE ENTORNO
--------------------
- AEMET_API_KEY: clave de la API de AEMET.
- S3_BUCKET_DATOS_RAW: bucket donde se guarda el pickle.
- AEMET_BASE_URL: opcional, por defecto la oficial de AEMET.

CONFIGURACION EN AWS
--------------------
- Handler: lambda_ingesta_estaciones.lambda_handler
- Memoria: 256 MB sobra. Timeout: 60s sobra.
- Permisos IAM: s3:PutObject sobre el bucket de datos crudos.
- NO necesita VPC.
- Dependencias a empaquetar: pandas, requests.

QUE DEVUELVE
------------
{"registros": 921, "s3_key": "estaciones/2026-09-01/estaciones.pkl", "guardado": true}

Al subir el pickle, S3 dispara automaticamente lambda_procesamiento_estaciones,
que es la que de verdad escribe en la base de datos.
"""

import json
import logging
import os
from datetime import date

import boto3
import pandas as pd
import requests

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client("s3")


# =====================================================================
# CLIENTE AEMET (dos pasos: pide URL temporal, luego descarga datos)
# =====================================================================

class AemetClient:
    # Mini-cliente de la API de la AEMET, igual que el de lambda_ingesta_aemet.
    def __init__(self, api_key: str):
        self._api_key = api_key
        self._base_url = os.environ.get(
            "AEMET_BASE_URL", "https://opendata.aemet.es/opendata/api"
        )

    def _resolver_descarga(self, url: str) -> list[dict]:
        # La AEMET responde en dos pasos: primero da una URL temporal donde
        # estan los datos, y luego hay que ir a esa URL a por ellos.
        resp = requests.get(url, params={"api_key": self._api_key}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("estado") != 200:
            # Clave mal, servicio caido... la AEMET explica que paso aqui.
            raise RuntimeError(f"Error AEMET: {data.get('descripcion')}")
        resp_datos = requests.get(data["datos"], timeout=30)
        resp_datos.raise_for_status()
        return resp_datos.json()

    def obtener_estaciones(self) -> pd.DataFrame:
        # El inventario completo: una sola peticion, sin trocear por fechas.
        url = f"{self._base_url}/valores/climatologicos/inventarioestaciones/todasestaciones"
        return pd.DataFrame(self._resolver_descarga(url))


# =====================================================================
# LAMBDA HANDLER
# =====================================================================

def lambda_handler(event: dict, context) -> dict:
    """
    Evento: {} (vacío).
    Guarda el pickle crudo en s3://<bucket>/estaciones/<fecha>/estaciones.pkl
    El evento S3 disparará la Lambda de procesamiento, que cargará el RDS.
    """
    logger.info("Descargando inventario de estaciones AEMET")

    try:
        api_key = os.environ["AEMET_API_KEY"]
        bucket = os.environ["S3_BUCKET_DATOS_RAW"]

        df = AemetClient(api_key).obtener_estaciones()

        if df.empty:
            logger.warning("AEMET no devolvió estaciones")
            return {
                "statusCode": 200,
                "body": json.dumps({"registros": 0, "guardado": False}),
            }

        logger.info(f"Descargadas {len(df)} estaciones")

        # Guarda el pickle con el prefijo "estaciones/" (las mediciones van
        # con "raw/"). Ese prefijo es lo que hace que S3 dispare la Lambda de
        # procesamiento de estaciones y no la de mediciones.
        key = f"estaciones/{date.today().isoformat()}/estaciones.pkl"
        s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=df.to_pickle(None),
            ContentType="application/octet-stream",
        )
        logger.info(f"Guardado en s3://{bucket}/{key}")

        return {
            "statusCode": 200,
            "body": json.dumps({
                "registros": len(df),
                "s3_key": key,
                "guardado": True,
            }),
        }

    except Exception as e:
        logger.error(f"Error: {str(e)}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
