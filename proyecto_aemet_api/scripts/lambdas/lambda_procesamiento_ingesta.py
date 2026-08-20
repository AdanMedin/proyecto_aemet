"""
AWS Lambda: pickle de mediciones de S3 -> limpieza -> base de datos (RDS).

QUE HACE
--------
Se ejecuta SOLA cada vez que lambda_ingesta_aemet deja un pickle nuevo en S3
(evento ObjectCreated con prefijo "raw/"). Lee el pickle con los datos crudos
de un dia, los limpia y los inserta en la tabla meteo.mediciones_diarias.

LA LIMPIEZA ES LA MISMA QUE EN LOCAL
------------------------------------
El DataTransformer de aqui es una copia exacta de ingestion/transformer.py
del proyecto. Hace lo mismo:
  - Los valores raros de la AEMET ("Acum", "Varias", "Ip") se detectan y se
    guardan como banderas booleanas (precAcum, variasHoras, precIp).
  - "Ip" (precipitacion inapreciable) se convierte en 0.05.
  - Las comas decimales pasan a puntos.
  - El renombre de columnas (hrMedia -> hrmedia, presMax -> presmax...) se
    hace al guardar, igual que en loader.py.

REGLA DE ESTACIONES DESCONOCIDAS
--------------------------------
Si llegan mediciones de una estacion que NO existe en meteo.estaciones, esas
mediciones se DESCARTAN (la base de datos tiene clave foranea y no las
aceptaria). Se apuntan en el log para poder verlas. La tabla de estaciones la
mantiene lambda_procesamiento_estaciones (mensual), asi que una estacion nueva
de la AEMET empieza a guardar datos tras el proximo refresco mensual.

EL GUARDADO ES IDEMPOTENTE
--------------------------
Se usa INSERT ... ON CONFLICT (indicativo, fecha) DO UPDATE: si el dato ya
existia, se actualiza en vez de duplicarse o fallar. Esto importa porque la
AEMET a veces corrige datos de dias anteriores, y porque se puede reprocesar
un pickle sin miedo.

CUANDO SE EJECUTA
-----------------
Automaticamente, disparada por S3. Tambien se puede invocar a mano para
reprocesar un dia:
    {"bucket": "aemet-datos-raw", "key": "raw/2026-08-14/mediciones.pkl"}

VARIABLES DE ENTORNO
--------------------
- DATABASE_DSN: cadena de conexion al RDS
  (postgresql://usuario:password@endpoint:5432/aemet).
- S3_BUCKET_DATOS_RAW: bucket de los pickles (para invocaciones manuales).

CONFIGURACION EN AWS
--------------------
- Handler: lambda_procesamiento_ingesta.lambda_handler
- Memoria: 512 MB recomendado. Timeout: 120s recomendado.
- VPC: SI, tiene que estar en la misma VPC que el RDS, con un security group
  que permita salir al puerto 5432 del security group del RDS.
- Permisos IAM: s3:GetObject sobre el bucket de datos crudos.
- Dependencias a empaquetar: pandas, numpy, psycopg2-binary.

QUE DEVUELVE
------------
{"s3_key": "raw/2026-08-14/mediciones.pkl", "mediciones_guardadas": 900,
 "mediciones_descartadas": 21}
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
# TRANSFORMADOR (copia exacta de transformer.py del proyecto)
# =====================================================================

class DataTransformer:
    COLUMNAS_FLOAT = [
        "tmed", "prec", "tmin", "tmax", "hrMedia", "pintMax",
        "velmedia", "racha", "presMax", "presMin", "sol",
    ]

    COLUMNAS_HORA = [
        "horatmin", "horatmax", "horaHrMax", "horaHrMin",
        "horaracha", "horaPresMax", "horaPresMin", "horaPIntMax",
    ]

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        # La fecha viene como texto "2026-08-14": la pasamos a fecha de verdad.
        # errors="coerce": si alguna viene mal, queda como vacia en vez de fallar.
        df["fecha"] = pd.to_datetime(df["fecha"], format="%Y-%m-%d", errors="coerce")

        # category = texto que se repite mucho (ocupa menos memoria y es mas rapido).
        for col in ["indicativo", "provincia", "nombre"]:
            if col in df.columns:
                df[col] = df[col].astype("category")

        # Solo revisamos las columnas que de verdad vinieron en el pickle.
        columnas_a_revisar = [
            c for c in self.COLUMNAS_FLOAT + self.COLUMNAS_HORA if c in df.columns
        ]

        # Detectar Acum / Varias / Ip (banderas booleanas).
        # La AEMET a veces escribe palabras donde deberia haber numeros:
        #   "Acum"   = precipitacion acumulada de varios dias, no se sabe cuanta de hoy
        #   "Varias" = la hora exacta no se sabe, paso varias veces
        #   "Ip"     = precipitacion inapreciable (llovio tan poco que no se mide)
        # Recorremos las columnas y marcamos en que filas aparece cada palabra.
        mask_acum = pd.Series(False, index=df.index)
        mask_varias = pd.Series(False, index=df.index)
        mask_ip = pd.Series(False, index=df.index)

        for col in columnas_a_revisar:
            serie = df[col].apply(
                lambda x: str(x).strip().lower() if pd.notna(x) else np.nan
            )
            mask_acum |= (serie == "acum")
            mask_varias |= (serie == "varias")
            mask_ip |= (serie == "ip")

        # Las tres banderas se guardan en la BD como columnas booleanas.
        df["precAcum"] = mask_acum.fillna(False)
        df["variasHoras"] = mask_varias.fillna(False)
        df["precIp"] = mask_ip.fillna(False)

        # Ahora si, limpieza numerica: quita las palabras y convierte a numero.
        for col in self.COLUMNAS_FLOAT:
            if col in df.columns:
                df[col] = df[col].apply(self._limpiar_numero)
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Estas columnas son enteras en la BD pero aqui las dejamos en float
        # (los None no caben en un int de pandas; el int de verdad se pone al guardar).
        for col in ["altitud", "hrMax", "hrMin", "dir"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)

        # Las horas vienen como "8" o "08:00": las dejamos siempre "HH:MM".
        for col in self.COLUMNAS_HORA:
            if col in df.columns:
                df[col] = df[col].apply(self._normalizar_hora)

        return df

    @staticmethod
    def _limpiar_numero(valor):
        if pd.isna(valor):
            return np.nan
        valor = str(valor).strip()
        if valor.lower() in ("ip",):
            # "Ip" = llovio tan poco que no se mide. Se guarda como 0.05
            # (un valor casi cero pero distinto de "no llovio nada").
            return "0.05"
        if valor.lower() in ("acum", "varias", "<na>", "nan", "none", ""):
            # Estas palabras no son un numero: la celda queda vacia.
            return np.nan
        # La AEMET usa coma decimal ("27,0"): la BD quiere punto ("27.0").
        return valor.replace(",", ".")

    @staticmethod
    def _normalizar_hora(valor):
        if pd.isna(valor):
            return np.nan
        valor = str(valor).strip()
        if valor in ("<NA>", "nan", "NaN", "None", ""):
            return np.nan
        if re.fullmatch(r"\d{1,2}", valor):
            # Viene solo la hora ("8"): la completamos como "08:00".
            return f"{int(valor):02d}:00"
        return valor


# =====================================================================
# MAPEOS Y PREPARACIÓN DE FILAS (idéntico a loader.py del proyecto)
# =====================================================================

# La AEMET y nuestra base de datos llaman a las mismas cosas con nombres
# distintos (la AEMET dice "hrMedia" y la BD dice "hrmedia"). Este diccionario
# es el traductor: a la izquierda el nombre que da la AEMET, a la derecha el
# nombre de la columna en la BD.
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
    "precAcum": "precAcum",
    "precIp": "precIp",
    "variasHoras": "variasHoras",
}

# SMALLINT en bd_meteo_v2.sql -> psycopg2 quiere int, no float.
_COLUMNAS_INT = {"dir", "hrmedia", "hrmax", "hrmin", "altitud"}
_COLUMNAS_BOOL = {"precAcum", "precIp", "variasHoras"}


def _filas(df: pd.DataFrame, mapeo: dict) -> tuple[list[tuple], list[str]]:
    # Prepara la tabla para meterla en la base de datos:
    #   1) Se queda solo con las columnas que nos interesan (y que existan).
    #   2) Les cambia el nombre usando el traductor (mapeo).
    #   3) Cambia los valores vacios (NaN) por None, que es lo unico que la
    #      base de datos entiende como "sin dato".
    #   4) Ajusta los tipos a lo que la BD espera (fecha de verdad, enteros
    #      de verdad, booleanos de verdad).
    existentes = {k: v for k, v in mapeo.items() if k in df.columns}
    renombrado = df[list(existentes)].rename(columns=existentes)
    limpio = renombrado.astype(object).where(pd.notna(renombrado), None)
    registros = limpio.to_dict(orient="records")

    columnas_bd = list(existentes.values())
    for fila in registros:
        if "fecha" in fila and fila["fecha"] is not None:
            # De Timestamp de pandas a date de Python (lo que pide la columna DATE).
            fila["fecha"] = pd.to_datetime(fila["fecha"]).date()
        for col in _COLUMNAS_INT:
            if col in fila and fila[col] is not None:
                # SMALLINT no acepta floats: "80.0" -> 80.
                fila[col] = int(fila[col])
        for col in _COLUMNAS_BOOL:
            if col in fila and fila[col] is not None:
                # De numpy.bool_ a bool de Python.
                fila[col] = bool(fila[col])

    return [tuple(f.get(c) for c in columnas_bd) for f in registros], columnas_bd


# =====================================================================
# CARGAS EN BD (upsert idempotente, igual que loader.py)
# =====================================================================

def cargar_mediciones(conn, df: pd.DataFrame) -> int:
    # Guarda las mediciones en meteo.mediciones_diarias. Devuelve cuantas filas guardo.
    filas, columnas = _filas(df, _MEDICIONES_COLUMNAS)
    if not filas:
        return 0

    # La pareja (indicativo, fecha) identifica cada fila de forma unica.
    # Si ya existe, actualizamos el resto de columnas con los valores nuevos
    # (la AEMET a veces corrige datos de dias anteriores).
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columnas if c not in ("fecha", "indicativo"))
    query = f"""
        INSERT INTO meteo.mediciones_diarias ({", ".join(columnas)})
        VALUES %s
        ON CONFLICT (indicativo, fecha) DO UPDATE SET {updates}
    """
    with conn.cursor() as cur:
        # execute_values mete todas las filas de golpe en una sola consulta
        # (mucho mas rapido que insertar una a una).
        execute_values(cur, query, filas)
    return len(filas)


def mediciones_de_estaciones_conocidas(conn, df: pd.DataFrame) -> pd.DataFrame:
    # Descarta mediciones cuya estación no existe en meteo.estaciones.
    # La tabla se refresca mensualmente; si AEMET publica datos de una estación
    # nueva, sus mediciones se ignoran hasta el próximo refresco del inventario.
    # (La base de datos tiene clave foranea: no aceptaria la medicion igualmente,
    # pero asi el error lo vemos en el log en vez de explotar a mitad del INSERT.)
    with conn.cursor() as cur:
        cur.execute("SELECT indicativo FROM meteo.estaciones")
        conocidas = {fila[0] for fila in cur.fetchall()}

    if not conocidas:
        # Tipico de un despliegue nuevo donde aun no se ha cargado el inventario.
        logger.warning("meteo.estaciones está vacía: no se insertará nada")
        return df.iloc[0:0]

    antes = len(df)
    df_filtrado = df[df["indicativo"].isin(conocidas)].copy()
    descartadas = antes - len(df_filtrado)
    if descartadas:
        # Apuntamos cuales se quedaron fuera (las 10 primeras, para no llenar el log).
        desconocidas = sorted(set(df["indicativo"]) - conocidas)
        logger.warning(
            f"Descartadas {descartadas} mediciones de {len(desconocidas)} "
            f"estaciones desconocidas: {desconocidas[:10]}"
        )
    return df_filtrado


# =====================================================================
# PROCESAMIENTO COMPLETO
# =====================================================================

def procesar(bucket: str, key: str, dsn: str) -> dict:
    # Lee pickle -> limpia -> filtra estaciones desconocidas -> inserta mediciones.
    logger.info(f"Procesando s3://{bucket}/{key}")

    # 1) Lee pickle crudo de S3
    resp = s3_client.get_object(Bucket=bucket, Key=key)
    df_raw = pd.read_pickle(resp["Body"])
    logger.info(f"Pickle cargado: {len(df_raw)} filas")

    # 2) Limpia con el transformer idéntico al proyecto
    df_limpio = DataTransformer().transform(df_raw)

    resultado = {}

    # 3) Una sola conexión síncrona (commit al final, rollback si algo falla)
    conn = psycopg2.connect(dsn)
    try:
        # Descarta mediciones de estaciones que no existen en la tabla
        df_limpio = mediciones_de_estaciones_conocidas(conn, df_limpio)

        # Inserta mediciones
        resultado["mediciones_guardadas"] = cargar_mediciones(conn, df_limpio)
        resultado["mediciones_descartadas"] = len(df_raw) - len(df_limpio)

        conn.commit()
        logger.info(f"OK: {resultado}")
        return resultado
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# =====================================================================
# LAMBDA HANDLER
# =====================================================================

def lambda_handler(event: dict, context) -> dict:
    """
    Evento desde S3 (automático al subir el pickle diario de mediciones):
    {
        "Records": [{
            "s3": {
                "bucket": {"name": "aemet-datos-raw"},
                "object": {"key": "raw/2026-08-14/mediciones.pkl"}
            }
        }]
    }

    Evento manual:
    {
        "bucket": "aemet-datos-raw",
        "key": "raw/2026-08-14/mediciones.pkl"
    }

    Las mediciones de estaciones que no existan en meteo.estaciones se
    descartan. El inventario lo mantiene lambda_procesamiento_estaciones.py.
    """
    logger.info(f"Evento: {event}")

    try:
        dsn = os.environ["DATABASE_DSN"]
        bucket_default = os.environ["S3_BUCKET_DATOS_RAW"]

        if "Records" in event:
            # Viene del disparador automatico de S3: sacamos bucket y key del evento.
            record = event["Records"][0]["s3"]
            bucket = record["bucket"]["name"]
            # La key llega URL-encoded desde S3 (los espacios vienen como "+", etc.).
            key = unquote_plus(record["object"]["key"])
        else:
            # Invocacion a mano: nos pasan la key directamente.
            bucket = event.get("bucket", bucket_default)
            key = event["key"]

        resultado = procesar(bucket, key, dsn)

        return {
            "statusCode": 200,
            "body": json.dumps({"s3_key": key, **resultado}),
        }

    except Exception as e:
        logger.error(f"Error: {str(e)}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
