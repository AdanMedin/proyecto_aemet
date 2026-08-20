"""Servicio de ingesta: descarga AEMET, limpia y carga en PostgreSQL."""
from __future__ import annotations

from datetime import date, timedelta

from proyecto_aemet_api.ingestion.aemet_client import AemetClient
from proyecto_aemet_api.ingestion.loader import DataLoader
from proyecto_aemet_api.ingestion.transformer import DataTransformer


class IngestionService:
    # Este servicio es el "coordinador" de la descarga de datos. No descarga ni limpia ni guarda el mismo: eso lo hacen tres ayudantes especializados.
    # Este solo los llama en el orden correcto. Asi, si un dia cambia la forma de limpiar, solo tocamos el transformer y aqui no hay que tocar nada.
    
    # El flujo es siempre el mismo:
    #   1) AemetClient  -> descarga los datos de la web de la AEMET
    #   2) DataTransformer -> los deja limpios y con el formato correcto
    #   3) DataLoader   -> los mete en la base de datos

    def __init__(self, loader: DataLoader) -> None:
        # Aqui se preparan las tres piezas. `self.` guarda cada una "dentro" del servicio para poder usarla despues en los metodos.
        self._loader = loader # sabe hablar con la base de datos
        self._cliente = AemetClient()  # sabe hablar con la web de la AEMET
        self._transformer = DataTransformer()  # sabe limpiar los datos

    async def cargar_estaciones(self) -> int:
        # Descarga la lista de TODAS las estaciones de Espana (nombre, provincia, coordenadas...) y la guarda. Devuelve cuantas se guardaron.
        crudo = self._cliente.obtener_estaciones() # 1) descargar
        limpio = self._transformer.transform_estaciones(crudo) # 2) limpiar
        return await self._loader.cargar_estaciones(limpio) # 3) guardar

    async def cargar_mediciones(self, dias: int) -> int:
        # Descarga las mediciones de los ultimos `dias` dias y las guarda.
        # Por ejemplo con dias=5 trae los datos de los ultimos 5 dias.
        fin = date.today() # hoy
        inicio = fin - timedelta(days=dias) # hace `dias` dias

        crudo = self._cliente.obtener_mediciones(inicio, fin)
        if crudo.empty:
            # Si la AEMET no devolvio nada (raro, pero puede pasar), no hay nada que guardar. Devolvemos 0 para indicar "cero filas cargadas".
            return 0

        limpio = self._transformer.transform(crudo)
        return await self._loader.cargar_mediciones(limpio)

    async def cargar_mediciones_dia(self, retraso_dias: int = 5) -> int:
        # Descarga las mediciones de UN SOLO dia: el de hoy menos `retraso_dias`.
        # Es la ingesta diaria normal: la AEMET tarda unos 5 dias en publicar los
        # datos definitivos de un dia, asi que cada dia pedimos el de hace 5.
        # (Es lo mismo que hace la Lambda diaria de AWS.)
        dia = date.today() - timedelta(days=retraso_dias)

        crudo = self._cliente.obtener_mediciones(dia, dia)
        if crudo.empty:
            return 0

        limpio = self._transformer.transform(crudo)
        return await self._loader.cargar_mediciones(limpio)