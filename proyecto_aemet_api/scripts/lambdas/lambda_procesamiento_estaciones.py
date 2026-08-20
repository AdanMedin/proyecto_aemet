"""
AWS Lambda: pickle del inventario de estaciones de S3 -> base de datos (RDS).

QUE HACE
--------
Se ejecuta SOLA cada vez que lambda_ingesta_estaciones deja un pickle nuevo en
S3 (evento ObjectCreated con prefijo "estaciones/"). Lee el pickle con el
inventario crudo y actualiza la tabla meteo.estaciones.

LA TRANSFORMACION
-----------------
La AEMET da las coordenadas en un formato peculiar de grados, minutos y
segundos con hemisferio, por ejemplo "402830N". Aqui se convierten a decimal
(40.475) con la misma funcion que transformer.py del proyecto. La altitud,
que viene como texto, pasa a numero.

EL GUARDADO ES IDEMPOTENTE
--------------------------
Se usa INSERT ... ON CONFLICT (indicativo) DO UPDATE: si la estacion ya
existia, se actualizan sus datos (por si la AEMET corrige un nombre o una
coordenada). Nunca borra estaciones: las que la AEMET quite simplemente se
quedan en la tabla, con su historico de mediciones intacto.

CUANDO SE EJECUTA
-----------------
Automaticamente, disparada por S3 (tras la ejecucion mensual de
lambda_ingesta_estaciones). Tambien se puede invocar a mano:
    {"bucket": "aemet-datos-raw", "key": "estaciones/2026-09-01/estaciones.pkl"}

VARIABLES DE ENTORNO
--------------------
- DATABASE_DSN: cadena de conexion al RDS.
- S3_BUCKET_DATOS_RAW: bucket de los pickles (para invocaciones manuales).

CONFIGURACION EN AWS
--------------------
- Handler: lambda_procesamiento_estaciones.lambda_handler
- Memoria: 256 MB sobra. Timeout: 60s sobra (unas 900 filas).
- VPC: SI, misma VPC que el RDS y acceso al puerto 5432.
- Permisos IAM: s3:GetObject sobre el bucket de datos crudos.
- Dependencias a empaquetar: pandas, numpy, psycopg2-binary.

QUE DEVUELVE
------------
{"s3_key": "estaciones/2026-09-01/estaciones.pkl", "estaciones_guardadas": 921}
"""

import json
import logging
import os
import re
from urllib.parse import unquote_plus

import boto3
import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client("s3")


# =====================================================================
# TRANSFORMACIÓN (idéntica a transform_estaciones de transformer.py)
# =====================================================================

def _dms_a_decimal(valor):
    # "402830N" -> 40.475 (grados, minutos, segundos, hemisferio)
    # La AEMET da las coordenadas en ese formato raro de texto; la BD y la API
    # las necesitan en decimal para poder calcular distancias.
    if pd.isna(valor):
        return np.nan
    valor = str(valor).strip()
    m = re.fullmatch(r"(\d+)(\d{2})(\d{2})([NSEW])", valor, re.IGNORECASE)
    if not m:
        # Si ya viene en decimal (a veces pasa), lo usamos tal cual.
        return pd.to_numeric(valor, errors="coerce")
    grados, minutos, segundos, hemisferio = m.groups()
    decimal = int(grados) + int(minutos) / 60 + int(segundos) / 3600
    if hemisferio.upper() in ("S", "W"):
        # Sur y Oeste son coordenadas negativas.
        decimal = -decimal
    return round(decimal, 6)


def transformar_estaciones(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "altitud" in df.columns:
        # La altitud viene como texto ("113"): pasa a numero.
        df["altitud"] = pd.to_numeric(df["altitud"], errors="coerce")
    if "latitud" in df.columns:
        df["latitud"] = df["latitud"].apply(_dms_a_decimal)
    if "longitud" in df.columns:
        df["longitud"] = df["longitud"].apply(_dms_a_decimal)
    return df


# =====================================================================
# CARGA EN BD (idéntica a DataLoader.cargar_estaciones del proyecto)
# =====================================================================

_ESTACIONES_COLUMNAS = {
    "indicativo": "indicativo",
    "nombre": "nombre",
    "provincia": "provincia",
    "altitud": "altitud",
    "latitud": "latitud",
    "longitud": "longitud",
}


def cargar_estaciones(conn, df: pd.DataFrame) -> int:
    # Filtra/renombra columnas, NaN -> None, upsert por indicativo.
    existentes = {k: v for k, v in _ESTACIONES_COLUMNAS.items() if k in df.columns}
    renombrado = df[list(existentes)].rename(columns=existentes)
    limpio = renombrado.astype(object).where(pd.notna(renombrado), None)
    registros = limpio.to_dict(orient="records")
    if not registros:
        return 0

    columnas = list(existentes.values())
    for fila in registros:
        # altitud es SMALLINT en la BD: no acepta floats.
        if "altitud" in fila and fila["altitud"] is not None:
            fila["altitud"] = int(fila["altitud"])

    filas = [tuple(f.get(c) for c in columnas) for f in registros]

    # El indicativo identifica cada estacion. Si ya existe, se actualizan sus
    # datos (por si la AEMET corrige un nombre o una coordenada).
    query = f"""
        INSERT INTO meteo.estaciones ({", ".join(columnas)})
        VALUES %s
        ON CONFLICT (indicativo) DO UPDATE SET
            nombre = EXCLUDED.nombre,
            provincia = EXCLUDED.provincia,
            altitud = EXCLUDED.altitud,
            latitud = EXCLUDED.latitud,
            longitud = EXCLUDED.longitud
    """
    with conn.cursor() as cur:
        execute_values(cur, query, filas)
    return len(filas)


# =====================================================================
# LAMBDA HANDLER
# =====================================================================

def lambda_handler(event: dict, context) -> dict:
    """
    Evento desde S3 (automático cuando se sube el pickle del inventario):
    {
        "Records": [{
            "s3": {
                "bucket": {"name": "aemet-datos-raw"},
                "object": {"key": "estaciones/2026-09-01/estaciones.pkl"}
            }
        }]
    }

    Evento manual:
    {
        "bucket": "aemet-datos-raw",
        "key": "estaciones/2026-09-01/estaciones.pkl"
    }
    """
    logger.info(f"Evento: {event}")

    try:
        dsn = os.environ["DATABASE_DSN"]
        bucket_default = os.environ["S3_BUCKET_DATOS_RAW"]

        if "Records" in event:
            record = event["Records"][0]["s3"]
            bucket = record["bucket"]["name"]
            # La key llega URL-encoded desde S3
            key = unquote_plus(record["object"]["key"])
        else:
            bucket = event.get("bucket", bucket_default)
            key = event["key"]

        logger.info(f"Procesando s3://{bucket}/{key}")

        # 1) Lee pickle crudo de S3
        resp = s3_client.get_object(Bucket=bucket, Key=key)
        df_raw = pd.read_pickle(resp["Body"])
        logger.info(f"Pickle cargado: {len(df_raw)} estaciones")

        # 2) Transforma (DMS -> decimal)
        df = transformar_estaciones(df_raw)

        # 3) Guarda en RDS
        conn = psycopg2.connect(dsn)
        try:
            guardadas = cargar_estaciones(conn, df)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        logger.info(f"Guardadas {guardadas} estaciones en RDS")
        return {
            "statusCode": 200,
            "body": json.dumps({"s3_key": key, "estaciones_guardadas": guardadas}),
        }

    except Exception as e:
        logger.error(f"Error: {str(e)}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
