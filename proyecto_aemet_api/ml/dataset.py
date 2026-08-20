"""Construcción de las features de entrada de los modelos por estación.

Este modulo es la fuente de verdad sobre el formato de entrada del
modelo: tanto el entrenamiento como la prediccion deben usar construir_features
para garantizar que las columnas van en el mismo orden.

Contrato de entrada de cada modelo (uno por estacion), el MISMO que usa
ml/trainer.py al entrenar:
  - sin y cos del dia a predecir (codificacion ciclica de la fecha)
  - hrmedia del ultimo dia registrado
  - tmed de los DIAS_HISTORICO dias registrados anteriores (orden cronologico)
Total = 2 + 1 + DIAS_HISTORICO valores (23 columnas).
"""
from __future__ import annotations

from datetime import date

import numpy as np

# Numero de dias previos registrados que consume el modelo. 
# Es parte del contrato del modelo: si se cambia aqui, hay que reentrenar.
DIAS_HISTORICO = 20

# Periodo anual usado en la codificacion ciclica (365.25 tiene en cuenta los bisiestos).
_DIAS_ANIO = 365.25

def _sin_cos_dia(fecha_objetivo: date) -> tuple[float, float]:
    # Convierte la fecha en dos numeros (seno y coseno) para que el modelo entienda que el 31 de diciembre y el 1 de enero estan pegados en el ciclo anual.
    dia_del_anio = fecha_objetivo.timetuple().tm_yday
    angulo = 2.0 * np.pi * dia_del_anio / _DIAS_ANIO
    return float(np.sin(angulo)), float(np.cos(angulo))


def construir_features(
    tmed: list[float], hrmedia: list[float], fecha_objetivo: date
) -> np.ndarray:
    """Monta el vector de entrada (1 fila) que espera "modelo.predict".
    tmed y hrmedia deben traer exactamente DIAS_HISTORICO valores en
    orden cronologico (del mas antiguo al mas reciente).

    El orden de columnas replica el de trainer._ventanas:
    [sin_dia, cos_dia, hrmedia_mas_reciente, tmed_1 ... tmed_20]
    """
    if len(tmed) != DIAS_HISTORICO or len(hrmedia) != DIAS_HISTORICO:
        raise ValueError(
            f"Se esperaban {DIAS_HISTORICO} valores de tmed y hrmedia; "
            f"recibidos tmed={len(tmed)}, hrmedia={len(hrmedia)}."
        )

    sin_dia, cos_dia = _sin_cos_dia(fecha_objetivo)
    fila = [sin_dia, cos_dia, hrmedia[-1], *tmed]
    # reshape(1, -1): una sola muestra con todas las columnas (lo que espera sklearn).
    return np.array(fila, dtype=np.float64).reshape(1, -1)
