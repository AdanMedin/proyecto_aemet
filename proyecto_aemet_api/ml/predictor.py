"""Predicción con el modelo entrenado: registro y caché de modelos por estación."""

from __future__ import annotations
import asyncio
import os
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
import joblib

@dataclass
class ModeloEstacion:
    # Ficha que junta una estacion con su modelo de prediccion ya cargado.
    indicativo: str
    latitud: float
    longitud: float
    modelo: Any # el modelo entrenado (objeto de sklearn, etc.)
    metadata: dict[str, Any] = field(default_factory=dict) # metadata = datos extra opcionales (fecha de entrenamiento, metricas...).

# Firma (la forma) que debe tener la funcion real de carga: recibe el indicativo (texto) y devuelve el modelo ya listo, o None si no existe.
CargadorModelo = Callable[[str], Any]

class RegistroModelosLazy:
    """
    Carga los modelos bajo demanda ("lazy" = perezosa: solo cuando se piden) y
    mantiene en memoria los mas usados hasta un presupuesto maximo en MB (memoria maxima).
    Politica LRU ("Least Recently Used" = "el menos usado recientemente"): al
    superar el presupuesto, descarta primero los que llevan mas tiempo sin usarse.
    """

    def __init__(
        self,
        cargador: CargadorModelo,
        max_memoria_mb: float,
        obtener_tamano_mb: Callable[[str], float],
        factor_overhead: float = 1.3,
    ) -> None:
        self._cargador = cargador # se lee un modelo de disco
        self._max_memoria_mb = max_memoria_mb # limite de RAM para modelos
        self._obtener_tamano_mb = obtener_tamano_mb  # cuanto pesa cada modelo
        self._factor_overhead = factor_overhead # margen de seguridad sobre el peso teorico del modelo (para que no se pase del presupuesto por metadatos, etc.)
        self._cache: "OrderedDict[str, tuple[ModeloEstacion, float]]" = OrderedDict() # OrderedDict recuerda el orden de uso. Clave = indicativo, valor = (modelo, tamano en MB).
        self._memoria_actual_mb: float = 0.0
        self._lock = asyncio.Lock() # para no corromper la cache

    async def obtener(
        self, indicativo: str, latitud: float, longitud: float
    ) -> Optional[ModeloEstacion]:
        
        # PASO 1: si ya esta en cache, lo marcamos como "usado ahora" y lo devolvemos.
        async with self._lock:
            if indicativo in self._cache:
                self._cache.move_to_end(indicativo)
                return self._cache[indicativo][0]

        # PASO 2: cargarlo puede ser lento (disco), asi que se hace FUERA del cerrojo. 
        # to_thread ejecuta una funcion normal (sincrona) en un hilo aparte para no congelar el programa asincrono.
        modelo_obj = await asyncio.to_thread(self._cargador, indicativo)
        if modelo_obj is None:
            return None

        tamano_mb = await asyncio.to_thread(self._obtener_tamano_mb, indicativo)
        tamano_mb *= self._factor_overhead

        modelo = ModeloEstacion(
            indicativo=indicativo, latitud=latitud, longitud=longitud, modelo=modelo_obj
        )

        # PASO 3: guardar en cache.
        async with self._lock:
            # Si otra peticion cargo el mismo mientras tanto, no duplicamos.
            if indicativo in self._cache:
                self._cache.move_to_end(indicativo)
                return self._cache[indicativo][0]

            self._cache[indicativo] = (modelo, tamano_mb)
            self._cache.move_to_end(indicativo)
            self._memoria_actual_mb += tamano_mb

            # Si nos pasamos del presupuesto, tiramos los menos usados (popitem(last=False) saca el mas antiguo), dejando al menos 1.
            while self._memoria_actual_mb > self._max_memoria_mb and len(self._cache) > 1:
                _, (_, tamano_desalojado) = self._cache.popitem(last=False)
                self._memoria_actual_mb -= tamano_desalojado

        return modelo

    async def precargar(self, estaciones: list[Any]) -> None:
        """Opcional: calienta la caché al arrancar con unas estaciones dadas."""
        # gather() lanza muchas cargas EN PARALELO y espera a que acaben todas.
        await asyncio.gather(
            *(self.obtener(e.indicativo, e.latitud, e.longitud) for e in estaciones)
        )

    def __len__(self) -> int:
        return len(self._cache)

    @property
    def memoria_actual_mb(self) -> float:
        return round(self._memoria_actual_mb, 1)


# ---------------------------------------------------------------------
# Funciones que saben leer los modelos .joblib de la carpeta de artefactos.
# Cada una devuelve otra funcion ya "configurada" con la ruta (patron fabrica),
# que es justo lo que RegistroModelosLazy espera recibir.
# ---------------------------------------------------------------------

def crear_cargador_desde_disco(ruta_modelos: str) -> CargadorModelo:
    def cargar(indicativo: str) -> Any:
        ruta = os.path.join(ruta_modelos, f"{indicativo}.joblib")
        try:
            return joblib.load(ruta) # deserializa el modelo guardado en el archivo
        except FileNotFoundError:
            return None # esa estacion todavia no tiene modelo
    return cargar


def crear_cargador_con_s3(bucket: str, region: str, prefix: str = "modelos") -> CargadorModelo:
    # Carga los modelos desde S3 DIRECTO A MEMORIA, sin tocar el disco.
    # Los que mas se usan se quedan en la cache en RAM (RegistroModelosLazy se
    # encarga de eso, con su limite de memoria); si la cache expulsa uno, la
    # proxima vez se vuelve a descargar de S3. El disco del servidor no se
    # llena nunca, por muchos modelos que haya en la nube.
    from proyecto_aemet_api.ml.s3_storage import S3Storage

    s3 = S3Storage(bucket, region, prefix)
    return s3.obtener_modelo_en_memoria


def crear_medidor_tamano_s3(bucket: str, region: str, prefix: str, tamano_defecto_mb: float) -> Callable[[str], float]:
    # Mide lo que pesa cada modelo preguntando a S3 (sin descargarlo).
    # Lo usa RegistroModelosLazy para saber cuando va a superar su presupuesto de RAM.
    from proyecto_aemet_api.ml.s3_storage import S3Storage

    s3 = S3Storage(bucket, region, prefix)

    def tamano(indicativo: str) -> float:
        medido = s3.obtener_tamano_mb(indicativo)
        return medido if medido > 0 else tamano_defecto_mb
    return tamano


def crear_medidor_tamano(ruta_modelos: str, tamano_defecto_mb: float) -> Callable[[str], float]:
    def tamano(indicativo: str) -> float:
        ruta = os.path.join(ruta_modelos, f"{indicativo}.joblib")
        try:
            return os.path.getsize(ruta) / (1024 * 1024)
        except FileNotFoundError:
            return tamano_defecto_mb
    return tamano
