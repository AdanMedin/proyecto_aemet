"""Entrenamiento del modelo de forecasting.

Logica adaptada del boceto scripts/MODEL.ipynb a la BD del proyecto
(bd_meteo_v2.sql manda). Un modelo RandomForest por estacion.

Contrato de features (debe coincidir con ml/dataset.construir_features):
    X = [sin_dia, cos_dia, hrmedia_ultimo, tmed_d1..tmed_d20]  (23 columnas)
    y = tmed del dia objetivo

IMPORTANTE: el dia objetivo NO es el siguiente a la ventana, sino 6 dias
despues. Por que: la AEMET publica los datos con unos 5 dias de retraso, asi
que cuando la API predice "mañana", la ventana de datos que tiene disponible
acaba en hoy-5. Entrenar con este desfase hace que el modelo aprenda
exactamente la situacion en la que va a trabajar en produccion.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

# Una estacion necesita un minimo de dias con datos para que el modelo pueda
# aprender algo con sentido. Con menos de 1500 filas (unos 4 años) no entrenamos.
_MIN_FILAS = 1500
# Guardamos el ultimo año (365 dias) aparte para probar si el modelo acierta
# con datos que no ha visto durante el entrenamiento.
_DIAS_TEST = 365
# Descartamos los primeros dias porque a veces tienen datos de peor calidad.
_SKIP_INICIAL = 10

# Cuantos dias despues del final de la ventana esta el dia a predecir.
# En produccion la ventana acaba en hoy-5 (retraso de la AEMET) y se predice
# mañana (hoy+1): 6 dias de salto. El modelo se entrena con ese mismo salto.
_DIAS_ADELANTE = 6

# Las columnas de la base de datos que el modelo usa (nombres de bd_meteo_v2.sql).
COLUMNAS_ENTRENAMIENTO = ["fecha", "tmed", "hrmedia"]


@dataclass
class ResultadoEntrenamiento:
    # Ficha con el resultado de entrenar una estacion: cuanto se equivoca el
    # modelo (las metricas) y donde quedo guardado el archivo.
    indicativo: str
    mae: float          # error medio en grados (cuanto mas bajo, mejor)
    rmse: float         # otro error medio, castiga mas los fallos grandes
    r2: float           # calidad del 0 al 1 (1 = prediccion perfecta)
    ruta_modelo: str    # carpeta y nombre del archivo .joblib guardado


def _ventanas(df: pd.DataFrame, window: int) -> tuple[np.ndarray, np.ndarray]:
    # El modelo aprende mirando "ventanas" de dias (window). 
    # Por ejemplo, con una ventana de 20 dias: le ensenhamos los 20 dias anteriores y le pedimos que adivine la temperatura del dia siguiente.
    # Repetimos esto miles de veces moviendo la ventana por todo el historico, y asi el modelo aprende el patron.
    
    # Devuelve dos cosas:
    #   X = las entradas (lo que el modelo ve para pensar)
    #   y = la respuesta correcta (lo que deberia predecir)
    temp = df.sort_values("fecha")[COLUMNAS_ENTRENAMIENTO].reset_index(drop=True)

    # El modelo no entiende las fechas como "verano" o "invierno". 
    # Convertimos el dia del año en dos numeros (seno y coseno) para que entienda que el año es un ciclo: el 31 de diciembre y el 1 de enero estan pegados.
    temp["dia_sin"] = np.sin(2 * np.pi * temp["fecha"].dt.dayofyear / 365.25)
    temp["dia_cos"] = np.cos(2 * np.pi * temp["fecha"].dt.dayofyear / 365.25)

    # Sacamos las columnas como listas de numeros para ir mas rapido.
    tmed = temp["tmed"].to_numpy(dtype=float)
    hr = temp["hrmedia"].to_numpy(dtype=float)
    sin = temp["dia_sin"].to_numpy(dtype=float)
    cos = temp["dia_cos"].to_numpy(dtype=float)

    filas_x: list[np.ndarray] = []
    filas_y: list[float] = []
    # Recorremos el historico moviendo la ventana. En cada paso, la entrada es
    # [seno, coseno del dia a predecir, humedad del ultimo dia de la ventana,
    # 20 temperaturas de la ventana] y la respuesta correcta es la temperatura
    # del dia objetivo, que esta _DIAS_ADELANTE dias despues del final de la
    # ventana (replicando el desfase real de la AEMET en produccion).
    for i in range(len(temp) - window - _DIAS_ADELANTE + 1):
        i_ultimo_ventana = i + window - 1     # ultimo dia que ve el modelo
        i_objetivo = i + window - 1 + _DIAS_ADELANTE  # dia a predecir
        filas_x.append(np.hstack([sin[i_objetivo], cos[i_objetivo], hr[i_ultimo_ventana], tmed[i : i + window]]))
        filas_y.append(tmed[i_objetivo])

    return np.array(filas_x, dtype=float), np.array(filas_y, dtype=float)


class ModelTrainer:
    # Entrena un modelo por estacion usando su historico ya limpio.

    def __init__(self, ruta_salida: str) -> None:
        self._ruta_salida = ruta_salida
        # Crea la carpeta de salida si no existe, para poder guardar ahi.
        os.makedirs(ruta_salida, exist_ok=True)

    def entrenar_estacion(self, indicativo: str, df_estacion: pd.DataFrame) -> ResultadoEntrenamiento | None:
        # Quitamos las filas que tengan huecos en temperatura o humedad.
        df_estacion = df_estacion.dropna(subset=["tmed", "hrmedia"])
        if len(df_estacion) < _MIN_FILAS:
            # Pocas filas: el modelo no aprenderia bien. No entrenamos esta.
            return None

        # Ordenamos por fecha, descartamos los primeros dias y construimos las ventanas.
        datos = df_estacion.sort_values("fecha").reset_index(drop=True)
        x, y = _ventanas(datos.iloc[_SKIP_INICIAL:], window=20)
        if len(x) <= _DIAS_TEST:
            return None

        # Separamos los datos: casi todos para que el modelo aprenda (train), y el ultimo año guardado aparte para probar si acierta (test).
        x_train, x_test = x[:-_DIAS_TEST], x[-_DIAS_TEST:]
        y_train, y_test = y[:-_DIAS_TEST], y[-_DIAS_TEST:]

        # Creamos el modelo Random Forest: junta 1000 "arboles de decision" y promedia sus respuestas. 
        # Los numeros de aqui (profundidad, etc.) son ajustes que probamos y que dan buen resultado.
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

        # Guardamos el modelo entrenado en un archivo .joblib, uno por estacion.
        ruta = os.path.join(self._ruta_salida, f"{indicativo}.joblib")
        joblib.dump(modelo, ruta)

        # Medimos cuanto se equivoca y devolvemos el resumen.
        return ResultadoEntrenamiento(
            indicativo=indicativo,
            mae=float(mean_absolute_error(y_test, pred)),
            rmse=float(np.sqrt(mean_squared_error(y_test, pred))),
            r2=float(r2_score(y_test, pred)),
            ruta_modelo=ruta,
        )
