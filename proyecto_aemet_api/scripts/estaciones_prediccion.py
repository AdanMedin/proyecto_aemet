"""
estaciones_prediccion.py
=========================
Selección de estaciones meteorológicas cercanas a un punto dado y
localización de sus modelos predictivos asociados.

Diseñado para 800+ estaciones:
  - Las coordenadas se cachean en memoria (numpy) y se recalculan
    distancias de forma vectorizada, sin ir a la base de datos en
    cada petición del endpoint.
  - Los modelos predictivos se cargan la primera
    vez que se piden y se mantienen en una caché LRU con un tamaño
    máximo configurable, para no agotar la RAM si los modelos pesan mucho.

Dependencias:
    pip install asyncpg numpy
"""

# ---------------------------------------------------------------------
# IMPORTS (las "herramientas" que este archivo necesita para funcionar)
# ---------------------------------------------------------------------

# `from __future__ import annotations` es una linea "magica" que hay que
# poner LA PRIMERA. Hace que Python trate las anotaciones de tipos (los
# `: str`, `: float`, etc.) como simple texto, en vez de intentar
# evaluarlas al importar. Ventaja: puedes escribir tipos que aun no
# existen sin que de error, y la carga del modulo es un poco mas rapida.
from __future__ import annotations

# `asyncio` = libreria estandar de Python para programacion ASINCRONA.
# "Asincrono" significa: mientras una tarea espera algo lento (leer disco,
# la base de datos, la red), el programa puede ir atendiendo otras cosas
# en vez de quedarse parado. Es lo que usa FastAPI por debajo.
import asyncio

# `time` = utilidades de tiempo. Aqui lo usamos para medir cuantos
# segundos han pasado desde que cargamos algo (para saber si esta "caducado").
import time

# `OrderedDict` = un diccionario que RECUERDA el orden en que metiste las
# cosas. Nos sirve para saber que elemento es el "mas viejo" y descartarlo
# primero (esto es la base de la cache LRU que veras mas abajo).
from collections import OrderedDict

# `dataclass` y `field` = ayudas para crear clases que solo guardan datos
# sin tener que escribir codigo repetitivo. Se explican mas abajo.
from dataclasses import dataclass, field

# `typing` = solo sirve para DESCRIBIR que tipo de dato esperamos.
#   - Any     -> "cualquier cosa, no me comprometo a un tipo concreto".
#   - Callable -> "una funcion" (algo que se puede llamar con ()).
#   - Optional[X] -> "puede ser X o puede ser None (vacio)".
from typing import Any, Callable, Optional

# `asyncpg` = libreria para hablar con PostgreSQL de forma ASINCRONA
# (muy rapida). PostgreSQL es la base de datos donde estan las estaciones.
import asyncpg

# `numpy` (se abrevia `np` por convencion) = libreria para hacer calculos
# con muchos numeros a la vez de forma muy eficiente (calculo "vectorizado").
# En vez de calcular la distancia estacion por estacion con un bucle lento,
# numpy calcula las 800+ distancias de golpe.
import numpy as np


# =====================================================================
# 1) RESULTADO DE LA BÚSQUEDA DE ESTACIONES
# =====================================================================
"""
@dataclass

Es un decorador de la librería estándar que te ahorra escribir código repetitivo en clases que solo guardan datos.
Sin él, para tener EstacionCercana tendrías que escribir a mano la clase con un __init__ que acepte todos los campos y los asigne a self.

frozen=True

Este parámetro extra hace que, una vez creado el objeto, no se puedan modificar sus campos.

"""
@dataclass(frozen=True)
class EstacionCercana:
    # Esta clase es simplemente una "ficha" que representa UNA estacion que
    # esta cerca del punto buscado. `frozen=True` la hace de solo lectura:
    # una vez creada la ficha, sus datos no se pueden cambiar (mas seguro).
    # Cada linea de abajo es un "campo" (un dato) con su tipo:
    indicativo: str            # codigo unico de la estacion (ej. "3195")
    nombre: str                # nombre legible (ej. "Madrid, Retiro")
    provincia: Optional[str]   # provincia; puede venir vacia (None)
    latitud: float             # coordenada norte-sur (numero con decimales)
    longitud: float            # coordenada este-oeste (numero con decimales)
    distancia_km: float        # a cuantos km esta del punto que buscamos


# ===================================================================================================================================
# 2) CÁLCULO DE DISTANCIA VECTORIZADO (Haversine, numpy) te da directamente la distancia en kilómetros sobre la superficie terrestre
# ===================================================================================================================================
"""
El guion bajo delante (_haversine_vectorizado) es convención de Python para "función interna/privada

"a" es un valor intermedio relacionado con el "ángulo" entre los dos puntos vistos desde el centro de la Tierra.
"c" es el ángulo central real entre los dos puntos, en radianes.
"""

_RADIO_TIERRA_KM = 6371.0

def _haversine_vectorizado(
    pred_lat: float, pred_lon: float, sta_lats: np.ndarray, sta_lons: np.ndarray
) -> np.ndarray:
    """Distancia en km entre (pred_lat, pred_lon) y cada punto de (sta_lats, sta_lons)."""
    # "Haversine" es el nombre de la formula matematica estandar para medir
    # la distancia entre dos puntos sobre una esfera (la Tierra) sabiendo su
    # latitud y longitud. Como la Tierra no es plana, no vale el teorema de
    # Pitagoras normal: hay que tener en cuenta la curvatura.

    # La formula trabaja con RADIANES, no con grados. Un radian es otra forma
    # de medir angulos. `np.radians(...)` convierte grados -> radianes.
    # `pred_...` = el punto de PREDiccion (donde el usuario quiere el tiempo).
    # `sta_...`  = las STAtions (estaciones); son ARRAYS (listas) de numpy,
    #             asi calculamos las 800+ estaciones a la vez.
    pred_lat_rad = np.radians(pred_lat)
    pred_lon_rad = np.radians(pred_lon)
    sta_lats_rad = np.radians(sta_lats)
    sta_lons_rad = np.radians(sta_lons)

    # Diferencia de latitud y de longitud entre el punto y cada estacion.
    dif_lat = sta_lats_rad - pred_lat_rad
    dif_lon = sta_lons_rad - pred_lon_rad

    # `a` es un valor intermedio de la formula: mide, mediante senos y cosenos,
    # cuanto se separan los dos puntos teniendo en cuenta la curvatura.
    a = (np.sin(dif_lat / 2.0) ** 2 + np.cos(pred_lat_rad) * np.cos(sta_lats_rad) * np.sin(dif_lon / 2.0) ** 2)

    # `c` es el angulo central real entre los dos puntos (en radianes).
    # `np.clip(a, 0.0, 1.0)` fuerza a que `a` se quede entre 0 y 1 para evitar
    # errores minusculos de redondeo que harian fallar la raiz cuadrada.
    c = 2.0 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))

    # Angulo (c) por el radio de la Tierra = distancia real en kilometros.
    return _RADIO_TIERRA_KM * c


# =====================================================================
# 3) CACHÉ DE ESTACIONES (coordenadas en memoria, refrescadas por TTL)
# =====================================================================

@dataclass
class _EstacionesData:
    # Guarda las columnas de TODAS las estaciones como arrays de numpy
    # (una "tabla" en memoria). El guion bajo del nombre indica que es de
    # uso interno. Tener columnas separadas permite que numpy calcule
    # rapidisimo sobre `lats` y `lons`.
    indicativos: np.ndarray
    nombres: np.ndarray
    provincias: np.ndarray
    lats: np.ndarray
    lons: np.ndarray


class CacheEstaciones:
    """
    Mantiene en memoria las coordenadas de todas las estaciones y
    resuelve búsquedas de cercanía sin consultar la base de datos en
    cada petición. Se refresca automáticamente si pasa `ttl_segundos`
    desde la última carga (por si se añaden/borran estaciones).
    """

    # `_QUERY` es la consulta SQL que pide a la base de datos las estaciones
    # con coordenadas validas. SELECT = "dame estas columnas";
    # FROM = "de esta tabla"; WHERE = "solo las filas que cumplan esto".
    _QUERY = """
        SELECT indicativo, nombre, provincia, latitud, longitud
        FROM meteo.estaciones
        WHERE latitud IS NOT NULL AND longitud IS NOT NULL
    """

    def __init__(self, pool: asyncpg.Pool, ttl_segundos: float = 3600.0) -> None:
        # `__init__` es el "constructor": se ejecuta al crear el objeto y
        # guarda su configuracion inicial en `self` (el propio objeto).
        self._pool = pool                 # "pool" = grupo de conexiones ya
        #                                   abiertas a la BD, reutilizables.
        self._ttl = ttl_segundos          # TTL = "Time To Live": segundos que
        #                                   los datos son validos antes de
        #                                   recargarlos (3600 s = 1 hora).
        self._data: Optional[_EstacionesData] = None  # aun no hay datos cargados
        self._cargado_en = 0.0            # momento de la ultima carga
        self._lock = asyncio.Lock()       # "cerrojo": evita que dos peticiones
        #                                   a la vez recarguen los datos a la
        #                                   par y se pisen entre ellas.

    async def _cargar(self) -> None:
        # `async def` = funcion asincrona: puede "esperar" (await) sin
        # bloquear al resto del programa. Aqui pide una conexion del pool,
        # lanza la consulta y recoge todas las filas.
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(self._QUERY)

        # Convertimos las filas de la BD en arrays de numpy (columna a columna).
        # `dtype=object` = texto/valores variados; `dtype=np.float64` = numeros
        # decimales de alta precision (para las coordenadas).
        self._data = _EstacionesData(
            indicativos=np.array([r["indicativo"] for r in rows], dtype=object),
            nombres=np.array([r["nombre"] for r in rows], dtype=object),
            provincias=np.array([r["provincia"] for r in rows], dtype=object),
            lats=np.array([float(r["latitud"]) for r in rows], dtype=np.float64),
            lons=np.array([float(r["longitud"]) for r in rows], dtype=np.float64),
        )
        self._cargado_en = time.monotonic()

    async def _asegurar_cargado(self) -> None:
        # Comprueba si los datos han "caducado" (ha pasado mas tiempo que el TTL).
        # `time.monotonic()` es un reloj que solo avanza y nunca retrocede,
        # ideal para medir tiempos transcurridos.
        vencido = (time.monotonic() - self._cargado_en) > self._ttl
        if self._data is None or vencido:
            # Pedimos el cerrojo para recargar sin que dos peticiones lo hagan a la vez.
            async with self._lock:
                # "Doble comprobacion": mientras esperabamos el cerrojo, quiza
                # otra peticion ya recargo los datos; volvemos a mirar para no
                # recargar por gusto. (Una "corrutina" es una tarea asincrona.)
                vencido = (time.monotonic() - self._cargado_en) > self._ttl
                if self._data is None or vencido:
                    await self._cargar()

    async def refrescar(self) -> None:
        """Fuerza una recarga inmediata (por ejemplo, tras añadir una estación nueva)."""
        async with self._lock:
            await self._cargar()

    async def buscar_cercanas(
        self,
        latitud: float,
        longitud: float,
        k: int = 5,                    # cuantas estaciones devolver como maximo
        max_distancia_km: float = 50.0, # radio de busqueda: ignora las mas lejanas
    ) -> list[EstacionCercana]:
        # Nos aseguramos de tener datos frescos antes de calcular nada.
        await self._asegurar_cargado()
        d = self._data
        # `assert` es una comprobacion de seguridad: si `d` fuera None aqui
        # (no deberia), el programa avisa en vez de fallar de forma rara.
        assert d is not None

        # Calcula de golpe la distancia del punto a TODAS las estaciones.
        distancias = _haversine_vectorizado(latitud, longitud, d.lats, d.lons)

        # `np.where(...)` nos da las POSICIONES de las estaciones que estan
        # dentro del radio permitido. Si no hay ninguna, devolvemos lista vacia.
        idx_dentro_radio = np.where(distancias <= max_distancia_km)[0]
        if idx_dentro_radio.size == 0:
            return []

        # `np.argsort` ordena de menor a mayor distancia y `[:k]` se queda con
        # las `k` mas cercanas. `orden` son las posiciones de esas ganadoras.
        orden = idx_dentro_radio[np.argsort(distancias[idx_dentro_radio])][:k]

        # Construimos una "ficha" EstacionCercana por cada estacion ganadora.
        # `round(..., 3)` redondea la distancia a 3 decimales para que quede limpia.
        return [
            EstacionCercana(
                indicativo=d.indicativos[i],
                nombre=d.nombres[i],
                provincia=d.provincias[i],
                latitud=float(d.lats[i]),
                longitud=float(d.lons[i]),
                distancia_km=round(float(distancias[i]), 3),
            )
            for i in orden
        ]


# =====================================================================
# 4) REGISTRO DE MODELOS CON CARGA PEREZOSA + LRU
# =====================================================================

@dataclass
class ModeloEstacion:
    # "Ficha" que junta una estacion con su modelo de prediccion ya cargado.
    indicativo: str
    latitud: float
    longitud: float
    modelo: Any                       # el modelo entrenado (objeto de sklearn, etc.)
    metadata: dict[str, Any] = field(default_factory=dict)
    # `metadata` = datos extra opcionales (fecha de entrenamiento, metricas...).
    # `field(default_factory=dict)` significa "si no me das nada, empieza con un
    # diccionario vacio {}". No se puede poner `= {}` directamente en un dataclass
    # porque todos los objetos compartirian el mismo diccionario (un fallo tipico).


# "Firma" (la forma que debe tener) de vuestra funcion real de carga: recibe
# el indicativo (texto) y devuelve el modelo ya listo, o None si no existe.
# `Callable[[str], Any]` se lee: "algo llamable que recibe un str y devuelve Any".
CargadorModelo = Callable[[str], Any]


class RegistroModelosLazy:
    """
    Carga los modelos bajo demanda y mantiene los modelos "calientes"
    en memoria hasta un presupuesto máximo en MB (política LRU: al
    superar el presupuesto, se descartan primero los menos usados
    recientemente hasta volver a estar por debajo del límite).

    Parámetros:
        cargador: función síncrona que recibe el indicativo y devuelve
            el objeto del modelo ya deserializado (p.ej. joblib.load).
            Se ejecuta en un hilo aparte (asyncio.to_thread) para no
            bloquear el event loop de FastAPI mientras carga.
        max_memoria_mb: presupuesto total de memoria para modelos en
            caché. P.ej. con modelos de 18 MB reales, en un servidor
            con 16 GB de RAM reservando 8 GB para modelos.
        obtener_tamano_mb: función síncrona que, dado un indicativo,
            devuelve el tamaño estimado en MB (normalmente el tamaño
            del archivo .joblib en disco, vía os.path.getsize). Se usa
            para llevar la cuenta del presupuesto sin tener que medir
            el objeto Python ya cargado (poco fiable con sklearn/numpy).
        factor_overhead: multiplicador de seguridad sobre el tamaño en
            disco, porque el objeto deserializado en memoria suele
            pesar algo más que el archivo (overhead de estructuras
            Python). 1.3 (30% extra) es un margen razonable por defecto.
    """

    def __init__(
        self,
        cargador: CargadorModelo,
        max_memoria_mb: float,
        obtener_tamano_mb: Callable[[str], float],
        factor_overhead: float = 1.3,
    ) -> None:
        self._cargador = cargador                 # funcion que sabe leer un modelo de disco
        self._max_memoria_mb = max_memoria_mb     # limite de RAM que dedicamos a modelos
        self._obtener_tamano_mb = obtener_tamano_mb  # funcion que dice cuanto pesa cada modelo
        self._factor_overhead = factor_overhead   # margen de seguridad sobre ese peso
        # `_cache` es un OrderedDict: recuerda el orden de uso. La clave es el
        # indicativo y el valor es una pareja (modelo, cuantos MB ocupa).
        # LRU = "Least Recently Used" = "el menos usado recientemente": cuando
        # falta sitio, se tira el que lleva mas tiempo sin usarse.
        self._cache: "OrderedDict[str, tuple[ModeloEstacion, float]]" = OrderedDict()
        self._memoria_actual_mb: float = 0.0      # cuanta RAM llevamos ocupada ahora mismo
        self._lock = asyncio.Lock()               # cerrojo para no corromper la cache

    async def obtener(
        self, indicativo: str, latitud: float, longitud: float
    ) -> Optional[ModeloEstacion]:
        # PASO 1: mira si el modelo ya esta en cache (rapido). Si esta,
        # lo marca como "usado ahora mismo" (move_to_end) y lo devuelve.
        async with self._lock:
            if indicativo in self._cache:
                self._cache.move_to_end(indicativo)
                return self._cache[indicativo][0]

        # PASO 2: no estaba, hay que cargarlo. Esto puede ser LENTO (leer disco
        # o S3), asi que lo hacemos FUERA del cerrojo para no bloquear a otras
        # peticiones. `asyncio.to_thread(...)` ejecuta una funcion normal
        # (sincrona) en un hilo aparte para no congelar el programa asincrono.
        modelo_obj = await asyncio.to_thread(self._cargador, indicativo)
        if modelo_obj is None:
            return None   # no existe modelo para esa estacion

        # Averiguamos cuanto ocupa y le sumamos el margen de seguridad.
        tamano_mb = await asyncio.to_thread(self._obtener_tamano_mb, indicativo)
        tamano_mb *= self._factor_overhead

        modelo = ModeloEstacion(
            indicativo=indicativo, latitud=latitud, longitud=longitud, modelo=modelo_obj
        )

        # PASO 3: guardarlo en cache. Volvemos a coger el cerrojo.
        async with self._lock:
            # Puede que otra peticion cargara el mismo modelo mientras tanto;
            # si es asi, usamos el suyo y no duplicamos trabajo ni memoria.
            if indicativo in self._cache:
                self._cache.move_to_end(indicativo)
                return self._cache[indicativo][0]

            self._cache[indicativo] = (modelo, tamano_mb)
            self._cache.move_to_end(indicativo)          # marcarlo como el mas reciente
            self._memoria_actual_mb += tamano_mb

            # Si nos pasamos del presupuesto de RAM, vamos tirando los modelos
            # MENOS usados (los del principio del OrderedDict) hasta volver por
            # debajo del limite. `popitem(last=False)` saca justo el mas antiguo.
            # Siempre dejamos al menos 1 en cache (len > 1).
            while self._memoria_actual_mb > self._max_memoria_mb and len(self._cache) > 1:
                _, (_, tamano_desalojado) = self._cache.popitem(last=False)
                self._memoria_actual_mb -= tamano_desalojado

        return modelo

    async def precargar(self, estaciones: list[EstacionCercana]) -> None:
        """Opcional: 'calienta' la caché al arrancar con un subconjunto de estaciones."""
        # `asyncio.gather(*...)` lanza muchas cargas EN PARALELO y espera a que
        # todas terminen. Asi al arrancar el servidor ya hay modelos listos.
        await asyncio.gather(
            *(self.obtener(e.indicativo, e.latitud, e.longitud) for e in estaciones)
        )

    def __len__(self) -> int:
        # Permite usar len(registro) para saber cuantos modelos hay en cache.
        return len(self._cache)

    @property
    def memoria_actual_mb(self) -> float:
        # `@property` deja consultar esto como si fuera un atributo
        # (registro.memoria_actual_mb) en vez de una funcion con parentesis.
        return round(self._memoria_actual_mb, 1)


# =====================================================================
# 5) CLASE DE ALTO NIVEL: une caché de estaciones + registro de modelos
# =====================================================================

@dataclass
class EstacionConModelo:
    # Empareja una estacion cercana con su modelo (que puede ser None si esa
    # estacion todavia no tiene modelo entrenado).
    estacion: EstacionCercana
    modelo: Optional[ModeloEstacion]


class PredictorMeteo:
    # Clase "de alto nivel": es la que usaras desde fuera. Junta las dos piezas
    # anteriores (la cache de estaciones y el registro de modelos) para que,
    # dando solo unas coordenadas, te devuelva las estaciones cercanas con su modelo.
    def __init__(self, cache_estaciones: CacheEstaciones, registro: RegistroModelosLazy) -> None:
        self._cache_estaciones = cache_estaciones
        self._registro = registro

    async def estaciones_para_prediccion(
        self,
        latitud: float,
        longitud: float,
        k: int = 5,
        max_distancia_km: float = 50.0,
        solo_con_modelo: bool = True,   # si True, descarta estaciones sin modelo
    ) -> list[EstacionConModelo]:
        # 1) Buscar las estaciones fisicamente mas cercanas al punto.
        cercanas = await self._cache_estaciones.buscar_cercanas(
            latitud, longitud, k=k, max_distancia_km=max_distancia_km
        )
        if not cercanas:
            return []   # no hay ninguna estacion dentro del radio

        # 2) Cargar/recuperar el modelo de cada estacion EN PARALELO (mas rapido
        #    que hacerlo de una en una). `gather` espera a que terminen todos.
        modelos = await asyncio.gather(
            *(
                self._registro.obtener(e.indicativo, e.latitud, e.longitud)
                for e in cercanas
            )
        )

        # 3) Emparejar cada estacion con su modelo. `zip` recorre las dos listas
        #    a la vez. Si pedimos solo_con_modelo, saltamos las que no tienen.
        resultado = []
        for est, modelo in zip(cercanas, modelos):
            if modelo is None and solo_con_modelo:
                continue
            resultado.append(EstacionConModelo(estacion=est, modelo=modelo))

        return resultado


# =====================================================================
# 6) EJEMPLO DE INTEGRACIÓN CON FASTAPI
# =====================================================================
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
import joblib

DB_DSN = "postgresql://usuario:password@localhost:5432/tu_bd"
RUTA_MODELOS = "/ruta/a/modelos"

# Adapatar a almacenamiento real de modelos (joblib, pickle, S3, etc.)
def cargar_modelo_desde_disco(indicativo: str):
    ruta = f"{RUTA_MODELOS}/{indicativo}.joblib"
    try:
        return joblib.load(ruta)
    except FileNotFoundError:
        return None

# Tamaño en disco del .joblib
def tamano_modelo_mb(indicativo: str) -> float:
    ruta = f"{RUTA_MODELOS}/{indicativo}.joblib"
    try:
        return os.path.getsize(ruta) / (1024 * 1024)
    except FileNotFoundError:
        return 18.0  # valor por defecto si no se puede medir


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await asyncpg.create_pool(DB_DSN, min_size=2, max_size=10)

    cache_estaciones = CacheEstaciones(pool, ttl_segundos=3600)
    registro = RegistroModelosLazy(
        cargador=cargar_modelo_desde_disco,
        obtener_tamano_mb=tamano_modelo_mb,
        max_memoria_mb=8000,   # ajustar según RAM real
    )
    predictor = PredictorMeteo(cache_estaciones, registro)

    app.state.pool = pool
    app.state.predictor = predictor

    yield

    await pool.close()


app = FastAPI(lifespan=lifespan)


class PrediccionRequest(BaseModel):
    latitud: float
    longitud: float
    k: int = 5
    max_distancia_km: float = 50.0


@app.post("/prediccion")
async def prediccion(req: PrediccionRequest):
    resultados = await app.state.predictor.estaciones_para_prediccion(
        latitud=req.latitud,
        longitud=req.longitud,
        k=req.k,
        max_distancia_km=req.max_distancia_km,
    )

    if not resultados:
        raise HTTPException(
            status_code=404,
            detail="No hay estaciones con modelo disponible dentro del radio indicado",
        )

    return [
        {"indicativo": r.estacion.indicativo, "distancia_km": r.estacion.distancia_km}
        for r in resultados
    ]
"""
