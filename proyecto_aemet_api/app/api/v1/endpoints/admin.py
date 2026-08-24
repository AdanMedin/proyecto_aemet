"""Endpoints de administración: ingesta y reentrenamiento bajo demanda."""
from __future__ import annotations

from fastapi import APIRouter, Request

# Un "router" agrupa rutas relacionadas. Estas son las rutas de administracion:
# no las usa el usuario normal, sino el sistema (o nosotros) para mantener los
# datos y los modelos al dia.
router = APIRouter()


@router.post("/admin/ingestar")
async def ingestar(
    request: Request,
    estaciones: bool = False,
    mediciones: bool = False,
    historico: bool = False,
    guardar_bd: bool = False,
):
    # Lanza la descarga de datos de la AEMET.
    #
    # Los modos se pueden COMBINAR: si activas varios, se ejecutan todos en
    # orden y el resultado trae una entrada por cada uno. Ejemplo:
    #   ?estaciones=true&mediciones=true  -> actualiza estaciones Y el ultimo dia
    #
    # Modos:
    #   - estaciones=true: descarga el inventario de estaciones y guarda el
    #     pickle crudo (S3 o disco) como copia de seguridad.
    #   - mediciones=true: descarga el ultimo dia DISPONIBLE (hoy-5; la AEMET
    #     tarda unos 5 dias en subir los datos definitivos) y lo guarda en
    #     pickle diario (S3 o disco local + CSV).
    #   - historico=true: descarga TODO el historico disponible (desde 2016)
    #     y lo guarda en pickle (S3 si hay bucket, o disco local + CSV).
    #
    #   - guardar_bd=true: los modos escriben en la base de datos.
    #     guardar_bd=false (por defecto): ningun modo escribe en la BD (solo
    #     descargan y devuelven cuantos registros habrian cargado).
    #     Asi por defecto el endpoint es seguro: no toca la BD sin pedirlo.
    #
    # Para cargar en la BD lo que ya hay en el almacenamiento (sin descargar
    # nada de la AEMET), usa POST /admin/recargar.

    # request.app.state.ingestion recupera el servicio de ingesta que se creo una sola vez al arrancar la API (no se crea de nuevo en cada llamada).
    servicio = request.app.state.ingestion
    settings = request.app.state.settings

    resultado = {}

    # Cada modo activado se ejecuta y deja su entrada en el resultado.
    if estaciones:
        # Descarga el inventario y guarda el pickle crudo en la raiz (S3 o disco) y,
        # si guardar_bd=true, escribe en la tabla meteo.estaciones.
        resultado["estaciones"] = await servicio.cargar_estaciones(
            guardar=guardar_bd,
            ruta_local=settings.ruta_datos,
            s3_bucket=settings.s3_bucket,
            s3_region=settings.aws_region,
        )

    if mediciones:
        # Descarga el ultimo dia disponible (hoy-5) y lo guarda en la raiz
        # con nombre "YYYY-MM-DD 00:00:00_YYYY-MM-DDT00:00:00UTC".
        resultado["mediciones"] = await servicio.cargar_mediciones(
            1, 
            guardar=guardar_bd,
            ruta_local=settings.ruta_datos,
            s3_bucket=settings.s3_bucket,
            s3_region=settings.aws_region,
        )

    if historico:
        # Descarga todo el historico y lo guarda como ALL_10_YEARS (pickle + CSV en local).
        resultado["historico"] = await servicio.cargar_historico_completo(
            ruta_local=settings.ruta_datos,
            s3_bucket=settings.s3_bucket,
            s3_region=settings.aws_region,
            guardar=guardar_bd,
        )

    if not resultado:
        # Ningun modo activado: avisamos en vez de no hacer nada sin decir nada.
        return {
            "aviso": "No se activo ningun modo",
            "modos": ["estaciones", "mediciones", "historico"],
        }

    return resultado


@router.post("/admin/reentrenar")
async def reentrenar(request: Request):
    # Reentrena todos los modelos con los datos mas recientes de la base de datos y los sube a la nube si esta configurada. 
    # El scheduler llama aqui cada 6 meses de forma automatica, y tambien se puede llamar a mano.
    return await request.app.state.training.reentrenar()


@router.post("/admin/recargar")
async def recargar(
    request: Request,
    historico_mediciones: bool = False,
    estaciones: bool = False,
    incremental_mediciones: bool = False,
    guardar_bd: bool = False,
):
    # Recarga la base de datos con lo que YA hay en el almacenamiento (S3 o
    # disco local), SIN descargar nada nuevo de la AEMET.
    #
    # Sirve para la primera puesta en marcha (cargar el historico descargado)
    # o para reconstruir la BD entera si se ha perdido. Como el guardado es
    # upsert (ON CONFLICT), no duplica nada aunque la BD ya tenga datos.
    #
    # Modos (combinables):
    #   - incremental_mediciones=true: carga SOLO los dias posteriores al
    #     ultimo que ya este en la BD (lee los pickles diarios). Util si la
    #     ingesta diaria fallo unos dias y hay que recuperarlos.
    #   - historico_mediciones=true: carga TODO lo que haya (todos los pickles
    #     diarios + el historico completo). Para la primera carga o una
    #     reconstruccion completa.
    #   - estaciones=true: lee el pickle del inventario de estaciones y lo
    #     carga en meteo.estaciones.
    #   - guardar_bd=true: escribe en la base de datos. Por defecto es false:
    #     solo lee y devuelve cuantos registros cargaria (prueba sin riesgo).
    servicio = request.app.state.ingestion
    settings = request.app.state.settings

    resultado = {}

    if estaciones:
        resultado["estaciones"] = await servicio.recargar_estaciones_desde_almacenamiento(
            ruta_local=settings.ruta_datos,
            s3_bucket=settings.s3_bucket,
            s3_region=settings.aws_region,
            guardar=guardar_bd,
        )

    if incremental_mediciones:
        resultado["incremental_mediciones"] = await servicio.cargar_mediciones_desde_almacenamiento(
            ruta_local=settings.ruta_datos,
            s3_bucket=settings.s3_bucket,
            s3_region=settings.aws_region,
            guardar=guardar_bd,
        )

    if historico_mediciones:
        resultado["historico_mediciones"] = await servicio.recargar_mediciones_desde_almacenamiento(
            ruta_local=settings.ruta_datos,
            s3_bucket=settings.s3_bucket,
            s3_region=settings.aws_region,
            guardar=guardar_bd,
        )

    if not resultado:
        return {
            "aviso": "No se activo ningun modo",
            "modos": ["incremental_mediciones", "historico_mediciones", "estaciones"],
        }

    return resultado