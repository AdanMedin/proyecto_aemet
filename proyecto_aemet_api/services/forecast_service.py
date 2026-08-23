"""Servicio de predicción de temperatura: estaciones cercanas + sus modelos."""

from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional
import numpy as np
from proyecto_aemet_api.database.repositories.observation_repository import (
    ObservationRepository,
)
from proyecto_aemet_api.database.repositories.station_repository import StationRepository
from proyecto_aemet_api.ml.dataset import DIAS_HISTORICO, construir_features
from proyecto_aemet_api.ml.predictor import ModeloEstacion, RegistroModelosLazy

# =====================================================================
# 1) RESULTADO DE LA BUSQUEDA DE ESTACIONES
# =====================================================================

@dataclass(frozen=True) # dataclass: clase que solo guarda datos, frozen=True implica solo lectura.
class EstacionCercana:
    # Ficha de una estacion cercana.
    indicativo: str
    nombre: str
    provincia: Optional[str]
    latitud: float
    longitud: float
    distancia_km: float

# =====================================================================
# 2) CALCULO DE DISTANCIA (Haversine, vectorizado con numpy)
# =====================================================================

_RADIO_TIERRA_KM = 6371.0

# Distancia en km entre (pred_lat, pred_lon) y cada punto (sta_lats(array), sta_lons(array)).
# Haversine: formula estandar para medir distancias sobre una esfera.
# Trabaja en radianes, por eso convertimos los grados con np.radians. pred_ = punto pedido, sta_ = estaciones.
def _haversine_vectorizado(pred_lat: float, pred_lon: float, sta_lats: np.ndarray, sta_lons: np.ndarray) -> np.ndarray:
    pred_lat_rad = np.radians(pred_lat)
    pred_lon_rad = np.radians(pred_lon)
    sta_lats_rad = np.radians(sta_lats)
    sta_lons_rad = np.radians(sta_lons)

    dif_lat = sta_lats_rad - pred_lat_rad
    dif_lon = sta_lons_rad - pred_lon_rad

    # a = valor intermedio (mide la separacion teniendo en cuenta la curvatura).
    a = (np.sin(dif_lat / 2.0) ** 2 + np.cos(pred_lat_rad) * np.cos(sta_lats_rad) * np.sin(dif_lon / 2.0) ** 2)

    # c = angulo central entre los puntos. clip evita errores de redondeo.
    c = 2.0 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))

    return _RADIO_TIERRA_KM * c   # angulo por radio de la Tierra = km reales

# =====================================================================
# 3) CACHE DE ESTACIONES (coordenadas en memoria, refrescadas por TTL).
# =====================================================================

# TTL es "Time To Live", el tiempo durante el cual un dato guardado se considera válido antes de tener que renovarlo.

@dataclass
class _EstacionesData:
    # Columnas de TODAS las estaciones como arrays de numpy (una "tabla" en RAM).
    indicativos: np.ndarray
    nombres: np.ndarray
    provincias: np.ndarray
    lats: np.ndarray
    lons: np.ndarray

class CacheEstaciones:
    """
    Mantiene en memoria las coordenadas de todas las estaciones para resolver
    busquedas de cercania sin ir a la base de datos en cada peticion. Se
    refresca sola si pasan "ttl_segundos" desde la ultima carga.

    Solo carga estaciones que tengan:
      - datos recientes (la ultima medicion no puede tener mas de 7 dias)
      - modelo entrenado (el repositorio de modelos dice cuales existen)
    """

    def __init__(
        self,
        repositorio: StationRepository,
        ttl_segundos: float = 3600.0,
        listar_modelos: Callable[[], set[str]] | None = None,
    ) -> None:
        self._repositorio = repositorio # de aqui saca las estaciones (capa BD)
        self._ttl = ttl_segundos
        # Funcion que devuelve los indicativos de las estaciones con modelo.
        # Si no se pasa, no filtra por modelo (todas las estaciones con datos).
        self._listar_modelos = listar_modelos
        self._data: Optional[_EstacionesData] = None
        self._cargado_en = 0.0
        self._lock = asyncio.Lock() # evita recargas simultaneas que se pisen

    async def _cargar(self) -> None:
        # Pide las estaciones al repositorio y las convierte en arrays de numpy.
        rows = await self._repositorio.obtener_estaciones_con_coordenadas()

        # Filtra: solo estaciones que tengan modelo entrenado.
        if self._listar_modelos is not None:
            con_modelo = self._listar_modelos()
            rows = [r for r in rows if r["indicativo"] in con_modelo]

        self._data = _EstacionesData(
            indicativos=np.array([r["indicativo"] for r in rows], dtype=object),
            nombres=np.array([r["nombre"] for r in rows], dtype=object),
            provincias=np.array([r["provincia"] for r in rows], dtype=object),
            lats=np.array([float(r["latitud"]) for r in rows], dtype=np.float64),
            lons=np.array([float(r["longitud"]) for r in rows], dtype=np.float64),
        )
        self._cargado_en = time.monotonic() # reloj que solo avanza (para medir TTL)

    async def _asegurar_cargado(self) -> None:
        vencido = (time.monotonic() - self._cargado_en) > self._ttl
        if self._data is None or vencido:
            async with self._lock:
                # doble comprobacion: quiza otra peticion ya recargo mientras esperabamos el lock. Para no recargar si no es necesario.
                vencido = (time.monotonic() - self._cargado_en) > self._ttl
                if self._data is None or vencido:
                    await self._cargar()

    async def refrescar(self) -> None:
        """Fuerza una recarga inmediata (p. ej. tras añadir una estación)."""
        async with self._lock:
            await self._cargar()

    async def buscar_estaciones_cercanas(
        self,
        latitud: float,
        longitud: float,
        k: int = 5, # cuantas estaciones devolver como maximo
        max_distancia_km: float = 50.0, # radio: ignora las mas lejanas
    ) -> list[EstacionCercana]:
        await self._asegurar_cargado()
        d = self._data
        assert d is not None # comprobacion de seguridad; aqui nunca sera None

        # distancia del punto a TODAS las estaciones de golpe (vectorizado).
        distancias = _haversine_vectorizado(latitud, longitud, d.lats, d.lons)

        # posiciones de las estaciones dentro del radio permitido.
        idx_dentro_radio = np.where(distancias <= max_distancia_km)[0]
        if idx_dentro_radio.size == 0:
            return []

        # ordena por distancia (argsort) y coge las k mas cercanas ([:k]).
        orden = idx_dentro_radio[np.argsort(distancias[idx_dentro_radio])][:k]

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
# 4) SERVICIO: une cache de estaciones + registro de modelos
# =====================================================================

@dataclass
class EstacionConModelo:
    # Empareja una estacion con su modelo (que puede ser None si no tiene).
    estacion: EstacionCercana
    modelo: Optional[ModeloEstacion]

@dataclass(frozen=True)
class PrediccionTemperatura:
    # Resultado final: una estacion con la temperatura media prevista para fecha (el dia siguiente a su ultima medicion registrada).
    estacion: EstacionCercana
    fecha: date
    temperatura: float

class PredictorMeteo:
    # Clase "de alto nivel": la que se usa desde fuera (los endpoints de la web). 
    # Junta la cache de estaciones, el registro de modelos y las mediciones de la
    # BD para que, dando unas coordenadas, devuelva la temperatura prevista.
    def __init__(
        self,
        cache_estaciones: CacheEstaciones,
        registro: RegistroModelosLazy,
        observaciones: ObservationRepository,
    ) -> None:
        self._cache_estaciones = cache_estaciones
        self._registro = registro
        self._observaciones = observaciones

    async def estaciones_para_prediccion(
        self,
        latitud: float,
        longitud: float,
        k: int = 5, # cuantas estaciones devolver como maximo
        max_distancia_km: float = 50.0, # radio: para ignorar las mas lejanas
        solo_con_modelo: bool = True, # si True, descarta estaciones sin modelo
    ) -> list[EstacionConModelo]:
        
        # 1) estaciones mas cercanas al punto.
        estaciones_cercanas = await self._cache_estaciones.buscar_estaciones_cercanas(
            latitud, longitud, k=k, max_distancia_km=max_distancia_km
        )

        if not estaciones_cercanas:
            return []

        # 2) cargar/recuperar el modelo de cada una de las estaciones en paralelo (mas rapido).
        modelos = await asyncio.gather(
            *(self._registro.obtener(estacion.indicativo, estacion.latitud, estacion.longitud) for estacion in estaciones_cercanas)
        )

        # 3) emparejar cada estacion con su modelo (zip recorre ambas a la vez).
        resultado: list[EstacionConModelo] = []

        for estacion, modelo in zip(estaciones_cercanas, modelos):
            if modelo is None and solo_con_modelo:
                continue
            resultado.append(EstacionConModelo(estacion=estacion, modelo=modelo))

        return resultado

    async def predecir_temperatura(
        self,
        latitud: float,
        longitud: float,
        k: int = 5,
        max_distancia_km: float = 50.0,
    ) -> list[PrediccionTemperatura]:
        # Solo tiene sentido predecir en estaciones que tengan modelo.
        con_modelo = await self.estaciones_para_prediccion(
            latitud, longitud, k=k, max_distancia_km=max_distancia_km, solo_con_modelo=True
        )
        if not con_modelo:
            return []

        # Predice cada estacion en paralelo y descarta las que no tienen datos suficientes.
        predicciones = await asyncio.gather(
            *(self._predecir_estacion(cm) for cm in con_modelo)
        )
        return [p for p in predicciones if p is not None]

    async def _predecir_estacion(
        self, con_modelo: EstacionConModelo
    ) -> Optional[PrediccionTemperatura]:
        assert con_modelo.modelo is not None # ya venia filtrado por solo_con_modelo

        # 1) ultimas mediciones registradas de esta estacion (orden cronologico).
        mediciones = await self._observaciones.obtener_ultimas_mediciones(
            con_modelo.estacion.indicativo, DIAS_HISTORICO
        )
        if len(mediciones) < DIAS_HISTORICO:
            return None   # no hay historico suficiente para predecir

        # La ultima medicion de la ventana no puede ser demasiado vieja:
        # si tiene mas de 7 dias, la ventana no vale para predecir mañana
        # (seria como adivinar el tiempo de mañana mirando el de hace un mes).
        ultima_fecha = mediciones[-1]["fecha"]
        if (date.today() - ultima_fecha).days > 7:
            return None   # datos demasiado antiguos

        # 2) separar las columnas que necesita el modelo.
        tmed = [float(m["tmed"]) for m in mediciones]
        hrmedia = [float(m["hrmedia"]) for m in mediciones]

        # 3) el dia a predecir es MAÑANA (hoy + 1), no el dia siguiente a la
        # ultima medicion. La AEMET publica los datos con unos 5 dias de
        # retraso, asi que la ultima medicion es de hace 5 dias, pero el modelo
        # se entreno asi: con los ultimos 20 dias disponibles predice el dia
        # siguiente (que en la practica es mañana).
        fecha_objetivo = date.today() + timedelta(days=1)

        # 4) construir el vector de entrada y pedir la prediccion al modelo.
        features = construir_features(tmed, hrmedia, fecha_objetivo)
        # predict es sincrono (sklearn) lo lanzamos en un hilo para no bloquear.
        prediccion = await asyncio.to_thread(con_modelo.modelo.modelo.predict, features)

        return PrediccionTemperatura(
            estacion=con_modelo.estacion,
            fecha=fecha_objetivo,
            temperatura=round(float(prediccion[0]), 1),
        )