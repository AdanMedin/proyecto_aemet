"""
AWS Lambda: descarga de mediciones diarias de la AEMET -> pickle en S3.

QUE HACE
--------
Cada día descarga las mediciones de TODAS las estaciones de España de un solo
día: el de hoy menos 5 días. La AEMET tarda unos 5 días en publicar los datos
definitivos de un día, así que cada día pedimos el que acaba de quedar
disponible. Los datos se guardan TAL CUAL los da la AEMET (sin limpiar) en un
pickle en S3, y ahí acaba el trabajo de esta Lambda.

POR QUE EXISTE ASI
------------------
La ingesta esta partida en dos Lambdas a proposito:
  - Esta solo descarga. No toca la base de datos ni limpia nada.
  - lambda_procesamiento_ingesta.py lee el pickle, limpia y guarda en RDS.
S3 hace de intermediario y de disparador: cuando cae el pickle, la otra Lambda
se ejecuta sola (evento ObjectCreated). Si la limpieza falla, el dato crudo
sigue en S3 y se puede reprocesar sin volver a pedirlo a la AEMET.

Es la equivalente en AWS a lo que en local hace el scheduler llamando a
POST /api/v1/admin/ingestar?diario=true.

CUANDO SE EJECUTA
-----------------
EventBridge Scheduler, una vez al dia (por ejemplo cron(0 3 * * ? *)).
Tambien se puede invocar a mano pasando un dia concreto:
    {"fecha": "2026-08-14"}
Util para recuperar un dia que fallara.

VARIABLES DE ENTORNO
--------------------
- AEMET_API_KEY: clave de la API de AEMET (sin ella no descarga nada).
- S3_BUCKET_DATOS_RAW: bucket donde se guardan los pickles crudos.

CONFIGURACION EN AWS
--------------------
- Handler: lambda_ingesta_aemet.lambda_handler
- Memoria: 256 MB sobra. Timeout: 60s sobra (una peticion a AEMET).
- Permisos IAM: s3:PutObject sobre el bucket de datos crudos.
- NO necesita VPC (no habla con la base de datos).
- Dependencias a empaquetar: pandas, requests (boto3 ya viene en Lambda).

QUE DEVUELVE
------------
{"fecha": "2026-08-14", "registros": 921, "s3_key": "raw/2026-08-14/mediciones.pkl", "guardado": true}
"""

import json
import logging
import os
from datetime import date, timedelta
from typing import Any

import boto3
import pandas as pd
import requests

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client("s3")


# =====================================================================
# CLIENTE AEMET (solo descarga)
# =====================================================================

class AemetClient:
    # Mini-cliente de la API de la AEMET. Es una version recortada del
    # aemet_client.py del proyecto: solo lo justo para descargar un dia.
    def __init__(self, api_key: str):
        self._api_key = api_key
        self._base_url = "https://opendata.aemet.es/opendata/api"

    def _resolver_descarga(self, url: str) -> list[dict]:
        """AEMET devuelve URL temporal, hay que ir dos veces."""
        # La AEMET funciona en DOS pasos:
        #   1) Le pedimos los datos y NO nos los da: nos devuelve una direccion
        #      web temporal donde estan.
        #   2) Vamos a esa direccion y ahi si estan los datos de verdad.
        resp = requests.get(url, params={"api_key": self._api_key}, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        if data.get("estado") != 200:
            # La AEMET devolvio un mensaje de error (clave mal, dia sin datos...).
            raise RuntimeError(f"Error AEMET: {data.get('descripcion')}")

        resp_datos = requests.get(data["datos"], timeout=30)
        resp_datos.raise_for_status()
        return resp_datos.json()

    def obtener_medicion_dia(self, dia: date) -> pd.DataFrame:
        """Descarga mediciones de UN DÍA para todas las estaciones."""
        # "todasestaciones" = una sola peticion trae las ~900 estaciones.
        # Fechaini y fechafin son el mismo dia: solo queremos ese dia.
        url = (
            f"{self._base_url}/valores/climatologicos/diarios/datos/"
            f"fechaini/{dia.isoformat()}T00:00:00UTC/"
            f"fechafin/{dia.isoformat()}T00:00:00UTC/"
            f"todasestaciones"
        )
        datos = self._resolver_descarga(url)
        return pd.DataFrame(datos)


# =====================================================================
# GUARDADOR EN S3 (guarda pickle)
# =====================================================================

def guardar_pickle_s3(bucket: str, key: str, df: pd.DataFrame) -> str:
    """Guarda DataFrame como pickle en S3."""
    # to_pickle(None) devuelve los bytes directamente, sin escribir en disco
    # (en Lambda solo se puede escribir en /tmp, asi nos lo ahorramos).
    pickle_data = df.to_pickle(None)
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=pickle_data,
        ContentType="application/octet-stream",
        Metadata={"fecha_descarga": date.today().isoformat()}
    )
    logger.info(f"Guardado en S3: s3://{bucket}/{key}")
    return f"s3://{bucket}/{key}"


# =====================================================================
# LAMBDA HANDLER
# =====================================================================

def lambda_handler(event: dict, context: Any) -> dict:
    """
    Entry point para AWS Lambda.
    
    Evento esperado: {} (vacío, o puedes pasar un día específico)
    Ejemplo: {"fecha": "2024-08-14"}
    """
    logger.info(f"Iniciando descarga AEMET")

    try:
        api_key = os.environ["AEMET_API_KEY"]
        bucket = os.environ["S3_BUCKET_DATOS_RAW"]

        # Dia a descargar: hoy - 5 dias. La AEMET tarda unos 5 dias en dar
        # por buenos los datos de un dia, asi que el de hoy-5 es el mas
        # reciente que esta completo. Si el evento trae "fecha", se usa esa
        # (para recuperar a mano un dia que fallara).
        fecha_descarga = event.get("fecha")
        if fecha_descarga:
            dia = date.fromisoformat(fecha_descarga)
        else:
            dia = date.today() - timedelta(days=5)

        logger.info(f"Descargando mediciones de: {dia}")

        # Descarga
        cliente = AemetClient(api_key)
        df_mediciones = cliente.obtener_medicion_dia(dia)

        if df_mediciones.empty:
            logger.warning(f"Sin datos para {dia}")
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "fecha": dia.isoformat(),
                    "registros": 0,
                    "guardado": False
                }),
            }

        logger.info(f"Descargadas {len(df_mediciones)} mediciones")

        # Guarda en S3. La key lleva la fecha de los datos (no la de hoy):
        # raw/2026-08-14/mediciones.pkl. Al caer el archivo, S3 dispara la Lambda de procesamiento.
        key = f"raw/{dia.isoformat()}/mediciones.pkl"
        guardar_pickle_s3(bucket, key, df_mediciones)

        return {
            "statusCode": 200,
            "body": json.dumps({
                "fecha": dia.isoformat(),
                "registros": len(df_mediciones),
                "s3_key": key,
                "guardado": True
            }),
        }

    except Exception as e:
        logger.error(f"Error: {str(e)}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
