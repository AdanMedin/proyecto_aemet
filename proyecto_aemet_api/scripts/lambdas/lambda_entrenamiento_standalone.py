"""
AWS Lambda: entrenamiento de los modelos de prediccion -> S3.

QUE HACE
--------
Lee TODO el historico de mediciones de la base de datos, entrena un modelo
RandomForest por cada estacion meteorologica y sube los modelos a S3
(un archivo .joblib por estacion, con prefijo "modelos/"). Tambien sube un
metricas_modelos.joblib con la calidad de cada modelo (MAE, RMSE, R2).

EL ENTRENAMIENTO ES EL MISMO QUE EN LOCAL
-----------------------------------------
Replica exacta de ml/trainer.py + services/training_service.py del proyecto:
  - Solo entrena estaciones con al menos 1500 dias de datos (unos 4 años).
  - Entrada del modelo: 23 valores -> [seno y coseno del dia a predecir,
    humedad del ultimo dia, temperaturas de los 20 dias anteriores].
  - Salida: la temperatura media del dia siguiente.
  - RandomForest con 1000 arboles, profundidad 10, min 10 muestras por hoja.
  - El ultimo año de datos se guarda aparte para medir el error con datos
    que el modelo no ha visto.
IMPORTANTE: el .joblib guarda el modelo a pelo (sin dicts ni scalers), que es
como lo espera ml/predictor.py de la API. Si se cambiara el formato, la API
no podria predecir.

COMO LLEGAN LOS MODELOS A LA API
--------------------------------
La API no habla con esta Lambda. Cuando alguien pide una prediccion de una
estacion y el modelo no esta en su disco local, la API lo descarga de S3
(si tiene configurado S3_BUCKET_MODELOS) y lo cachea en memoria.

CUANDO SE EJECUTA
-----------------
EventBridge Scheduler, cada 15 dias. Entrenar con datos nuevos cada dia no
aporta: con dos semanas de datos nuevos el modelo mejora lo suficiente para
que merezca la pena el coste de reentrenar.

VARIABLES DE ENTORNO
--------------------
- DATABASE_DSN: cadena de conexion al RDS.
- S3_BUCKET_MODELOS: bucket donde se suben los modelos.
- AWS_REGION: region del bucket (defecto eu-west-1).
- RUTA_MODELOS: carpeta temporal (defecto /tmp/modelos; en Lambda solo se
  puede escribir en /tmp).

CONFIGURACION EN AWS
--------------------
- Handler: lambda_entrenamiento_standalone.lambda_handler
- Memoria: 3008 MB (el maximo). Timeout: 900s (el maximo, 15 min).
  OJO: entrenar ~900 estaciones con 1000 arboles cada una es mucho trabajo.
  Si no cabe en 15 minutos, toca moverlo a AWS Batch o Fargate con este
  mismo codigo, o bajar n_estimators.
- VPC: SI, misma VPC que el RDS y acceso al puerto 5432.
- Permisos IAM: s3:PutObject sobre el bucket de modelos.
- Dependencias a empaquetar: pandas, numpy, scikit-learn, joblib,
  psycopg2-binary.

QUE DEVUELVE
------------
{"modelos": 850, "sin_datos": 71, "subidos_s3": 851}
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

import boto3
import joblib
import numpy as np
import pandas as pd
import psycopg2
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

logger = logging.getLogger()
logger.setLevel(logging.INFO)


# =====================================================================
# TRAINER (copia exacta de ml/trainer.py del proyecto)
# =====================================================================

# Mínimo ~4 años de datos por estación (igual que trainer.py).
_MIN_FILAS = 1500
# Último año apartado para test (igual que trainer.py).
_DIAS_TEST = 365
# Descarta los primeros días (peor calidad) (igual que trainer.py).
_SKIP_INICIAL = 10
# Ventana de días que mira el modelo (igual que trainer.py).
_WINDOW = 20


@dataclass
class ResultadoEntrenamiento:
    indicativo: str
    mae: float
    rmse: float
    r2: float
    ruta_modelo: str


def _ventanas(df: pd.DataFrame, window: int) -> tuple[np.ndarray, np.ndarray]:
    # Idéntico a _ventanas de trainer.py:
    # X = [sin_dia, cos_dia, hrmedia_ultimo_dia_ventana, tmed_d1..d20]
    # y = tmed del día siguiente a la ventana
    temp = df.sort_values("fecha")[["fecha", "tmed", "hrmedia"]].reset_index(drop=True)

    # El modelo no entiende las fechas como "verano" o "invierno".
    # Convertimos el dia del año en dos numeros (seno y coseno) para que entienda
    # que el año es un ciclo: el 31 de diciembre y el 1 de enero estan pegados.
    temp["dia_sin"] = np.sin(2 * np.pi * temp["fecha"].dt.dayofyear / 365.25)
    temp["dia_cos"] = np.cos(2 * np.pi * temp["fecha"].dt.dayofyear / 365.25)

    tmed = temp["tmed"].to_numpy(dtype=float)
    hr = temp["hrmedia"].to_numpy(dtype=float)
    sin = temp["dia_sin"].to_numpy(dtype=float)
    cos = temp["dia_cos"].to_numpy(dtype=float)

    filas_x: list[np.ndarray] = []
    filas_y: list[float] = []
    # Recorremos el historico moviendo la ventana de 20 dias. En cada paso:
    # entrada = [seno, coseno del dia a predecir, humedad del ultimo dia de la
    # ventana, las 20 temperaturas de la ventana]; respuesta correcta = la
    # temperatura del dia siguiente a la ventana.
    for i in range(len(temp) - window):
        filas_x.append(np.hstack([sin[i + window], cos[i + window], hr[i + window - 1], tmed[i : i + window]]))
        filas_y.append(tmed[i + window])

    return np.array(filas_x, dtype=float), np.array(filas_y, dtype=float)


class ModelTrainer:
    # Idéntico a ModelTrainer de trainer.py del proyecto.

    def __init__(self, ruta_salida: str):
        self._ruta = ruta_salida
        os.makedirs(ruta_salida, exist_ok=True)

    def entrenar_estacion(self, indicativo: str, df_estacion: pd.DataFrame) -> Optional[ResultadoEntrenamiento]:
        # Quitamos las filas con huecos en temperatura o humedad.
        df_estacion = df_estacion.dropna(subset=["tmed", "hrmedia"])
        if len(df_estacion) < _MIN_FILAS:
            # Pocas filas: el modelo no aprenderia nada fiable. La saltamos.
            return None

        # Ordenamos por fecha, descartamos los primeros dias y construimos las ventanas.
        datos = df_estacion.sort_values("fecha").reset_index(drop=True)
        x, y = _ventanas(datos.iloc[_SKIP_INICIAL:], window=_WINDOW)
        if len(x) <= _DIAS_TEST:
            return None

        # Separamos: casi todo para aprender (train) y el ultimo año guardado
        # aparte (test) para medir el error con datos que el modelo no ha visto.
        x_train, x_test = x[:-_DIAS_TEST], x[-_DIAS_TEST:]
        y_train, y_test = y[:-_DIAS_TEST], y[-_DIAS_TEST:]

        # Hiperparámetros EXACTOS de trainer.py del proyecto.
        # RandomForest junta 1000 arboles de decision y promedia sus respuestas.
        modelo = RandomForestRegressor(
            n_estimators=1000,
            max_depth=10,
            random_state=42,
            min_samples_leaf=10,
            bootstrap=True,
            n_jobs=-1,
        )
        # fit = entrenar (el modelo aprende). predict = predecir sobre el test.
        modelo.fit(x_train, y_train)
        pred = modelo.predict(x_test)

        # joblib.dump(modelo) a pelo: predictor.py hace joblib.load() y llama
        # modelo.predict() directamente. Si guardáramos un dict, rompería.
        ruta = os.path.join(self._ruta, f"{indicativo}.joblib")
        joblib.dump(modelo, ruta)

        # Medimos cuanto se equivoca y devolvemos el resumen.
        return ResultadoEntrenamiento(
            indicativo=indicativo,
            mae=float(mean_absolute_error(y_test, pred)),
            rmse=float(np.sqrt(mean_squared_error(y_test, pred))),
            r2=float(r2_score(y_test, pred)),
            ruta_modelo=ruta,
        )


# =====================================================================
# S3 STORAGE (copia exacta de ml/s3_storage.py del proyecto)
# =====================================================================

class S3Storage:
    def __init__(self, bucket: str, region: str = "eu-west-1", prefix: str = "modelos"):
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._s3 = boto3.client("s3", region_name=region)

    def subir_carpeta(self, carpeta_local: str) -> int:
        # Sube todos los .joblib como "modelos/<indicativo>.joblib"
        subidos = 0
        for nombre in os.listdir(carpeta_local):
            if nombre.endswith(".joblib"):
                ruta_local = os.path.join(carpeta_local, nombre)
                key = f"{self._prefix}/{nombre}"
                try:
                    self._s3.upload_file(ruta_local, self._bucket, key)
                    subidos += 1
                    logger.info(f"Subido {key}")
                except Exception as e:
                    logger.error(f"Error subiendo {key}: {str(e)}")
        return subidos


# =====================================================================
# ENTRENAMIENTO COMPLETO (mismo flujo que training_service.py)
# =====================================================================

_QUERY_HISTORICO = """
    SELECT indicativo, fecha, tmed, hrmedia
    FROM meteo.mediciones_diarias
    WHERE tmed IS NOT NULL AND hrmedia IS NOT NULL
    ORDER BY indicativo, fecha
"""


def entrenar() -> dict:
    dsn = os.environ["DATABASE_DSN"]
    bucket = os.environ.get("S3_BUCKET_MODELOS", "")
    region = os.environ.get("AWS_REGION", "eu-west-1")
    # En Lambda solo se puede escribir en /tmp; de ahi se suben a S3.
    ruta_modelos = os.environ.get("RUTA_MODELOS", "/tmp/modelos")

    logger.info("Iniciando entrenamiento...")

    # 1) Lee histórico completo (una sola conexión síncrona)
    conn = psycopg2.connect(dsn)
    try:
        df = pd.read_sql(_QUERY_HISTORICO, conn)
    finally:
        conn.close()

    logger.info(f"Histórico: {len(df)} mediciones")
    if df.empty:
        # Sin datos no se puede entrenar nada. Avisamos con un resumen a cero.
        return {"modelos": 0, "sin_datos": 0, "subidos_s3": 0}

    df["fecha"] = pd.to_datetime(df["fecha"])

    # 2) Entrena un modelo por estación (mismo bucle que training_service.py)
    trainer = ModelTrainer(ruta_modelos)
    metricas: dict[str, dict] = {}
    sin_datos = 0

    for indicativo, df_est in df.groupby("indicativo"):
        resultado = trainer.entrenar_estacion(str(indicativo), df_est)
        if resultado is None:
            # Esta estacion no tiene historico suficiente. La saltamos.
            sin_datos += 1
            continue
        # Mismas claves que training_service.py: MAE, RMSE, R2
        metricas[resultado.indicativo] = {
            "MAE": resultado.mae,
            "RMSE": resultado.rmse,
            "R2": resultado.r2,
        }
        logger.info(f"Estación {indicativo}: R2={resultado.r2:.3f}")

    # 3) Guarda métricas (igual que training_service.py)
    joblib.dump(metricas, os.path.join(ruta_modelos, "metricas_modelos.joblib"))

    # 4) Sube a S3 si hay bucket configurado
    subidos = 0
    if bucket:
        subidos = S3Storage(bucket, region).subir_carpeta(ruta_modelos)
        logger.info(f"Subidos {subidos} archivos a S3")

    return {
        "modelos": len(metricas),
        "sin_datos": sin_datos,
        "subidos_s3": subidos,
    }


# =====================================================================
# LAMBDA HANDLER
# =====================================================================

def lambda_handler(event: dict, context) -> dict:
    """
    Evento: {} (vacío). Se programa con EventBridge, ej. cada 15 días.
    """
    logger.info("Iniciando Lambda de entrenamiento")

    try:
        resultado = entrenar()
        return {
            "statusCode": 200,
            "body": json.dumps(resultado),
        }
    except Exception as e:
        logger.error(f"Fallo: {str(e)}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
