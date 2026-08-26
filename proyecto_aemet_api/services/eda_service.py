"""Servicio para interpretar consultas EDA."""

from collections import defaultdict
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
import os

from dotenv import load_dotenv
from google import genai

from proyecto_aemet_api.schemas.eda import (
    ConsultaEDA,
    ConsultaEDACompleta,
)
from proyecto_aemet_api.services.coordenadas_service import obtener_coordenadas
from proyecto_aemet_api.database.repositories.observation_repository import (
    ObservationRepository,
)
from proyecto_aemet_api.services.forecast_service import CacheEstaciones


load_dotenv()

MODEL = "gemini-3.1-flash-lite"

client = genai.Client(
    api_key=os.environ["GEMINI_API_KEY"]
)


def interpretar_consulta_eda(
    consulta: str,
) -> ConsultaEDA:

    fecha_hoy = date.today()
    fecha_maxima = fecha_hoy - timedelta(days=5)
    fecha_minima = fecha_hoy - timedelta(days=365 * 10)

    prompt = f"""
    Interpreta una consulta meteorológica histórica escrita en lenguaje natural.

    Consulta del usuario:
    "{consulta}"

    Debes extraer únicamente:

    - municipio
    - fecha_inicio
    - fecha_fin

    La fecha actual es:
    {fecha_hoy.isoformat()}

    El rango aproximado disponible de datos es:

    Fecha mínima:
    {fecha_minima.isoformat()}

    Fecha máxima:
    {fecha_maxima.isoformat()}

    Reglas:

    1. Extrae el municipio, localidad o distrito indicado por el usuario.

    2. No inventes otro lugar diferente al solicitado.

    3. Las fechas deben estar en formato YYYY-MM-DD.

    4. Si se indica un mes completo, utiliza el primer y último día del mes.

    5. Si se indican varios meses, utiliza el primer día del primer mes
       y el último día del último mes.

    6. Si se indica únicamente un año, utiliza desde el 1 de enero
       hasta el 31 de diciembre.

    7. Si las fechas están fuera del rango disponible, conserva las
       fechas solicitadas. La validación se realizará posteriormente.

    8. No generes SQL.

    9. No añadas explicaciones.

    Ejemplo:

    Consulta:
    "temperatura media de Barajas desde marzo a junio de 2020"

    Resultado:
    {{
        "municipio": "Barajas",
        "fecha_inicio": "2020-03-01",
        "fecha_fin": "2020-06-30"
    }}
    """

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_schema": ConsultaEDA,
        },
    )

    return ConsultaEDA.model_validate_json(
        response.text
    )


def validar_fechas_eda(
    consulta: ConsultaEDA,
) -> ConsultaEDA:

    fecha_hoy = date.today()

    fecha_minima = fecha_hoy - relativedelta(years=10)
    fecha_maxima = fecha_hoy - relativedelta(days=5)

    if consulta.fecha_inicio > consulta.fecha_fin:
        raise ValueError(
            "La fecha de inicio no puede ser posterior a la fecha de fin."
        )

    if consulta.fecha_inicio < fecha_minima:
        raise ValueError(
            f"No existen datos anteriores al {fecha_minima}."
        )

    if consulta.fecha_fin > fecha_maxima:
        raise ValueError(
            f"Los datos más recientes disponibles son del {fecha_maxima}."
        )

    return consulta


def obtener_consulta_eda_completa(
    consulta: str,
) -> ConsultaEDACompleta:

    consulta_interpretada = interpretar_consulta_eda(
        consulta
    )

    consulta_validada = validar_fechas_eda(
        consulta_interpretada
    )

    coordenadas = obtener_coordenadas(
        consulta_validada.municipio
    )

    return ConsultaEDACompleta(
        municipio=coordenadas.municipio,
        provincia=coordenadas.provincia,
        latitud=coordenadas.latitud,
        longitud=coordenadas.longitud,
        fecha_inicio=consulta_validada.fecha_inicio,
        fecha_fin=consulta_validada.fecha_fin,
    )


class EDAService:
    def __init__(
        self,
        observation_repository: ObservationRepository,
        cache_estaciones: CacheEstaciones,
    ) -> None:

        self._observation_repository = observation_repository
        self._cache_estaciones = cache_estaciones

    async def obtener_temperaturas_historicas(
        self,
        consulta_usuario: str,
        k: int = 5,
        max_distancia_km: float = 100.0,
    ) -> dict:

        consulta = obtener_consulta_eda_completa(
            consulta_usuario
        )

        estaciones = await self._cache_estaciones.buscar_estaciones_cercanas(
            latitud=consulta.latitud,
            longitud=consulta.longitud,
            k=k,
            max_distancia_km=max_distancia_km,
        )

        if not estaciones:
            raise ValueError(
                "No se han encontrado estaciones cercanas."
            )

        indicativos = [
            estacion.indicativo
            for estacion in estaciones
        ]

        mediciones = await self._observation_repository.obtener_temperaturas_periodo(
            indicativos=indicativos,
            fecha_inicio=consulta.fecha_inicio,
            fecha_fin=consulta.fecha_fin,
        )

        if not mediciones:
            raise ValueError(
                "No se han encontrado mediciones para el periodo solicitado."
            )

        temperaturas = self.calcular_temperatura_ponderada(
            estaciones=estaciones,
            mediciones=mediciones,
        )

        return {
            "consulta": consulta,
            "estaciones": estaciones,
            "temperaturas": temperaturas,
        }

    def calcular_temperatura_ponderada(
        self,
        estaciones,
        mediciones,
    ) -> list[dict]:

        distancias = {
            estacion.indicativo: estacion.distancia_km
            for estacion in estaciones
        }

        mediciones_por_fecha = defaultdict(list)

        for medicion in mediciones:
            fecha = medicion["fecha"]
            indicativo = medicion["indicativo"]
            tmed = float(medicion["tmed"])

            distancia = distancias.get(indicativo)

            if distancia is None:
                continue

            mediciones_por_fecha[fecha].append(
                {
                    "tmed": tmed,
                    "distancia": distancia,
                }
            )

        resultado = []

        for fecha, valores in sorted(
            mediciones_por_fecha.items()
        ):

            suma_ponderada = 0.0
            suma_pesos = 0.0

            temperatura_ponderada = None

            for valor in valores:
                distancia = valor["distancia"]
                tmed = valor["tmed"]

                if distancia == 0:
                    temperatura_ponderada = tmed
                    break

                peso = 1 / distancia

                suma_ponderada += tmed * peso
                suma_pesos += peso

            if temperatura_ponderada is None:
                temperatura_ponderada = (
                    suma_ponderada / suma_pesos
                )

            resultado.append(
                {
                    "fecha": fecha,
                    "temperatura_media": round(
                        temperatura_ponderada,
                        2,
                    ),
                }
            )

        return resultado