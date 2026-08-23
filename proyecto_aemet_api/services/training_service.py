"""Servicio de reentrenamiento: entrena modelos y los sube a S3."""
from __future__ import annotations

import joblib
import pandas as pd

from proyecto_aemet_api.ml.trainer import COLUMNAS_ENTRENAMIENTO, ModelTrainer

# Consulta SQL: trae de la base de datos el historico completo de todas las estaciones, pero solo las 4 columnas que el modelo necesita para entrenar (el codigo de la estacion, la fecha, la temperatura media y la humedad).
# WHERE filtra las filas que tengan esos datos rellenos (sin valores vacios).
# ORDER BY las ordena por estacion y por fecha, que es el orden que necesitamos.
_QUERY_HISTORICO = """
    SELECT indicativo, fecha, tmed, hrmedia
    FROM meteo.mediciones_diarias
    WHERE tmed IS NOT NULL AND hrmedia IS NOT NULL
    ORDER BY indicativo, fecha
"""


class TrainingService:
    # Este servicio entrena UN modelo por cada estacion meteorologica, usando todo el historico que hay guardado en la base de datos. 
    # Al final, si hay configurado un almacen en la nube (Amazon S3), sube los modelos ahi.

    def __init__(
        self,
        pool,
        ruta_salida: str,
        s3_bucket: str,
        aws_region: str,
        s3_prefijo: str = "modelos",
        s3_prefijo_historico: str = "modelos_historicos",
        ruta_historicos: str = "proyecto_aemet_api/ml/artifacts_historicos",
    ) -> None:
        # pool = grupo de conexiones a la base de datos ya abiertas.
        # ruta_salida = carpeta donde se guardan los modelos entrenados.
        # s3_bucket = nombre del almacen en la nube (vacio = no subir a la nube).
        # aws_region = zona geografica de Amazon donde esta ese almacen.
        # s3_prefijo = subcarpeta dentro del bucket donde van los modelos.
        # s3_prefijo_historico = subcarpeta de respaldo en la nube.
        # ruta_historicos = carpeta local de respaldo (cuando no hay S3).
        self._pool = pool
        self._ruta_salida = ruta_salida
        self._s3_bucket = s3_bucket
        self._aws_region = aws_region
        self._s3_prefijo = s3_prefijo
        self._s3_prefijo_historico = s3_prefijo_historico
        self._ruta_historicos = ruta_historicos

    async def reentrenar(self) -> dict:
        # PASO 1: leer el historico completo de la base de datos.
        async with self._pool.acquire() as conn:
            filas = await conn.fetch(_QUERY_HISTORICO)

        # Convertimos las filas en una tabla de pandas para trabajar comodo.
        df = pd.DataFrame(filas, columns=["indicativo", *COLUMNAS_ENTRENAMIENTO])
        if df.empty:
            # Sin datos no se puede entrenar nada. Avisamos con un resumen a cero.
            return {"modelos": 0, "sin_datos": 0, "subidos_s3": 0}

        df["fecha"] = pd.to_datetime(df["fecha"])
        trainer = ModelTrainer(self._ruta_salida)

        # RESPALDO LOCAL: si NO hay nube, los modelos nuevos se escriben en la
        # misma carpeta que los actuales (artifacts/) y los sobrescribirian.
        # Por eso el respaldo en disco se hace ANTES de entrenar: movemos los
        # modelos actuales a la carpeta historica (borrando el respaldo viejo).
        # Con nube no hace falta: los actuales estan en S3 y el respaldo lo
        # hace S3Storage al subir.
        if not self._s3_bucket:
            self._respaldar_modelos_locales()

        metricas: dict[str, dict] = {}  # aqui guardamos como de bueno es cada modelo
        sin_datos = 0                   # estaciones que no tienen historico suficiente

        # PASO 2: recorrer las estaciones una a una y entrenar su modelo.
        # groupby("indicativo") agrupa las filas por estacion.
        for indicativo, df_est in df.groupby("indicativo"):
            resultado = trainer.entrenar_estacion(str(indicativo), df_est)
            if resultado is None:
                # Esta estacion no tiene suficientes datos para aprender. La saltamos.
                sin_datos += 1
                continue
            # Guardamos las metricas de calidad (cuanto se equivoca el modelo).
            metricas[resultado.indicativo] = {
                "MAE": resultado.mae,
                "RMSE": resultado.rmse,
                "R2": resultado.r2,
            }

        # Guardamos todas las metricas en un archivo para consultarlas luego.
        joblib.dump(metricas, f"{self._ruta_salida}/metricas_modelos.joblib")

        # PASO 3: si hay nube configurada, subir los modelos. Si no, se quedan solo en el disco local. El import esta aqui dentro a proposito: asi la libreria de Amazon solo se carga si de verdad se va a usar.
        #
        # Respaldo de la version anterior:
        #   - Con nube: S3Storage mueve los modelos actuales del bucket a la
        #     carpeta historica antes de subir los nuevos (borrando el respaldo viejo).
        #   - Sin nube: ya se hizo antes de entrenar (ver arriba).
        subidos = 0
        if self._s3_bucket:
            from proyecto_aemet_api.ml.s3_storage import S3Storage

            subidos = S3Storage(
                self._s3_bucket, self._aws_region, self._s3_prefijo, self._s3_prefijo_historico
            ).subir_carpeta(self._ruta_salida)

        # Devolvemos un resumen de lo que se hizo (cuantos modelos, etc.).
        return {
            "modelos": len(metricas),
            "sin_datos": sin_datos,
            "subidos_s3": subidos,
        }

    def _respaldar_modelos_locales(self) -> None:
        # Mueve los .joblib de la carpeta de modelos a la carpeta historica.
        # Solo se guarda UNA version de respaldo: primero borra la historica vieja.
        import os
        import shutil

        os.makedirs(self._ruta_historicos, exist_ok=True)
        for nombre in os.listdir(self._ruta_historicos):
            if nombre.endswith(".joblib"):
                os.remove(os.path.join(self._ruta_historicos, nombre))
        for nombre in os.listdir(self._ruta_salida):
            if nombre.endswith(".joblib"):
                shutil.move(
                    os.path.join(self._ruta_salida, nombre),
                    os.path.join(self._ruta_historicos, nombre),
                )