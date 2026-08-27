"""Servicio de ingesta: descarga AEMET, limpia y carga en PostgreSQL."""
from __future__ import annotations
from io import BytesIO
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

    async def cargar_estaciones(
        self,
        guardar: bool = True,
        ruta_local: str | None = None,
        s3_bucket: str = "",
        s3_region: str = "eu-west-1",
    ) -> dict:
        # Descarga el inventario de TODAS las estaciones de Espana y lo guarda.
        #
        # Siempre guarda el pickle crudo (S3 si hay bucket, o disco local): es
        # la copia de seguridad por si hay que recargar la tabla sin volver a
        # pedirselo a la AEMET. Solo un pickle de estaciones a la vez: antes de
        # guardar el nuevo borra el anterior (igual que los historicos).
        #
        # Con guardar=True ademas escribe en la BD (upsert en meteo.estaciones).
        # Con guardar=False solo descarga y guarda el pickle.
        import os

        import boto3

        crudo = self._cliente.obtener_estaciones() # 1) descargar
        limpio = self._transformer.transform_estaciones(crudo) # 2) limpiar

        resultado: dict = {"estaciones": len(limpio)}

        # 3) guarda el pickle crudo en la RAIZ del almacenamiento: estaciones.pkl
        nombre = "estaciones.pkl"
        if s3_bucket:
            s3 = boto3.client("s3", region_name=s3_region)
            # Borra el pickle de estaciones anterior (solo guardamos uno).
            try:
                s3.delete_object(Bucket=s3_bucket, Key=nombre)
            except Exception:
                pass  # no existia: nada que borrar
            # s3.put_object(
            #     Bucket=s3_bucket,
            #     Key=nombre,
            #     Body=crudo.to_pickle(None),
            #     ContentType="application/octet-stream",
            # )
            buffer = BytesIO()
            crudo.to_pickle(buffer)
            s3.put_object(
                Bucket=s3_bucket,
                Key=nombre,
                Body=buffer.getvalue(),
                ContentType="application/octet-stream",
            )




            resultado["s3_key"] = nombre
        elif ruta_local:
            os.makedirs(ruta_local, exist_ok=True)
            # Borra el pickle/csv de estaciones anterior (solo guardamos uno).
            for f in os.listdir(ruta_local):
                if f in (nombre, "estaciones.csv"):
                    os.remove(os.path.join(ruta_local, f))
            ruta_pickle = os.path.join(ruta_local, nombre)
            ruta_csv = os.path.join(ruta_local, "estaciones.csv")
            crudo.to_pickle(ruta_pickle)
            crudo.to_csv(ruta_csv, index=False)  # EN LOCAL GUARDAMOS CSV TAMBIEN
            resultado["ruta_pickle"] = ruta_pickle
            resultado["ruta_csv"] = ruta_csv

        # 4) escribe en la BD si se pidio.
        if guardar:
            resultado["estaciones_bd"] = await self._loader.cargar_estaciones(limpio)

        return resultado

    async def cargar_mediciones(
        self, 
        dias: int, 
        guardar: bool = True,
        ruta_local: str | None = None,
        s3_bucket: str = "",
        s3_region: str = "eu-west-1",
    ) -> dict:
        # Descarga las mediciones de los ultimos `dias` dias DISPONIBLES.
        # La AEMET publica con unos 5 dias de retraso, asi que el rango acaba
        # en hoy-5 (el ultimo dia publicado), no en hoy: pedir dias mas
        # recientes no devolveria nada.
        #
        # Siempre guarda el pickle crudo en raw/<fecha>/mediciones.pkl (S3 o disco).
        # Con guardar=True ademas escribe en la BD (upsert en meteo.mediciones_diarias).
        import os
        import boto3

        fin = date.today() - timedelta(days=5)  # ultimo dia publicado
        inicio = fin - timedelta(days=dias - 1)

        crudo = self._cliente.obtener_mediciones(inicio, fin)
        resultado = {"mediciones": len(crudo)}
        
        if crudo.empty:
            return resultado

        limpio = self._transformer.transform(crudo)
        resultado["mediciones_limpias"] = len(limpio)

        # Nombre del pickle diario (raiz del bucket, sin subcarpetas):
        # "2026-08-17 00:00:00_2026-08-17T00:00:00UTC"
        nombre_diario = f"{fin.isoformat()} 00:00:00_{fin.isoformat()}T00:00:00UTC"

        if s3_bucket:
            s3 = boto3.client("s3", region_name=s3_region)
            # s3.put_object(
            #     Bucket=s3_bucket,
            #     Key=nombre_diario,
            #     Body=crudo.to_pickle(None),
            #     ContentType="application/octet-stream",
            # )
            buffer = BytesIO()
            crudo.to_pickle(buffer)
            s3.put_object(
                Bucket=s3_bucket,
                Key=nombre_diario,
                Body=buffer.getvalue(),
                ContentType="application/octet-stream",
            )




            resultado["s3_key"] = nombre_diario
        elif ruta_local:
            os.makedirs(ruta_local, exist_ok=True)
            ruta_pickle = os.path.join(ruta_local, nombre_diario + ".pkl")
            ruta_csv = os.path.join(ruta_local, nombre_diario + ".csv")
            crudo.to_pickle(ruta_pickle)
            crudo.to_csv(ruta_csv, index=False)  # EN LOCAL GUARDAMOS CSV TAMBIEN
            resultado["ruta_pickle"] = ruta_pickle
            resultado["ruta_csv"] = ruta_csv

        # Escribir en BD si se pidio
        if guardar:
            resultado["mediciones_bd"] = await self._loader.cargar_mediciones(limpio)
        
        return resultado

    async def cargar_historico_completo(
        self,
        ruta_local: str,
        s3_bucket: str = "",
        s3_region: str = "eu-west-1",
        guardar: bool = True,
    ) -> dict:
        # Descarga TODO el historico que da la AEMET (de 15 en 15 dias, por el
        # limite de su API) y lo guarda en formato pickle:
        #   - Si hay bucket S3 configurado: sube el pickle a la nube.
        #   - Si no: lo deja en la carpeta local (y ademas en CSV, para poder
        #     abrirlo y verlo sin Python).
        #
        # ANTES de guardar, borra los historicos viejos que hubiera (en S3 o en
        # local), para que no se acumulen versiones antiguas. Los pickles
        # diarios (raw/2026-08-23/mediciones.pkl) NO se tocan: esos se guardan
        # siempre como copia de seguridad por si falla la carga en la BD.
        #
        # Despues, habria que procesar esos pickles para meterlos en la BD
        # (igual que hacen las Lambdas de AWS con los pickles de S3).
        #
        # Devuelve un resumen: cuantos registros, en que archivos quedo, etc.
        import os

        import boto3

        # La AEMET tiene datos desde 2016 mas o menos (depende de la estacion).
        # Pedimos desde 2016 hasta hoy - 5 dias.
        inicio = date(2016, 1, 1)
        fin = date.today()- timedelta(days=5)

        crudo = self._cliente.obtener_mediciones(inicio, fin)
        if crudo.empty:
            return {"registros": 0, "guardado": False}

        resultado = {"registros": len(crudo)}

        # El historico SIEMPRE se llama ALL_10_YEARS (raiz del bucket / carpeta local).
        nombre = "ALL_10_YEARS"

        if s3_bucket:
            # Borra el historico anterior de S3 antes de subir el nuevo
            # (solo guardamos uno; los pickles diarios NO se tocan).
            s3 = boto3.client("s3", region_name=s3_region)
            try:
                s3.delete_object(Bucket=s3_bucket, Key=nombre)
            except Exception:
                pass  # no existia: nada que borrar

            # Sube el pickle nuevo a la raiz del bucket.
            # s3.put_object(
            #     Bucket=s3_bucket,
            #     Key=nombre,
            #     Body=crudo.to_pickle(None),
            #     ContentType="application/octet-stream",
            # )
            buffer = BytesIO()
            crudo.to_pickle(buffer)
            s3.put_object(
                Bucket=s3_bucket,
                Key=nombre,
                Body=buffer.getvalue(),
            )



            resultado["s3_key"] = nombre
            resultado["guardado"] = True
        else:
            # Borra historicos viejos de la carpeta local.
            os.makedirs(ruta_local, exist_ok=True)
            for f in os.listdir(ruta_local):
                if f.startswith(nombre) and (f.endswith(".pkl") or f.endswith(".csv")):
                    os.remove(os.path.join(ruta_local, f))

            # Guarda en disco local: pickle (para procesar) y CSV (para ver).
            ruta_pickle = os.path.join(ruta_local, nombre + ".pkl")
            ruta_csv = os.path.join(ruta_local, nombre + ".csv")
            crudo.to_pickle(ruta_pickle)
            crudo.to_csv(ruta_csv, index=False)
            resultado["ruta_pickle"] = ruta_pickle
            resultado["ruta_csv"] = ruta_csv
            resultado["guardado"] = True

        # Si ademas se pide guardar en la BD, limpia e inserta todo el historico.
        # OJO: esto tarda bastante (son millones de filas).
        if guardar:
            limpio = self._transformer.transform(crudo)
            resultado["mediciones_bd"] = await self._loader.cargar_mediciones(limpio)

        return resultado

    async def cargar_mediciones_desde_almacenamiento(
        self,
        ruta_local: str,
        s3_bucket: str = "",
        s3_region: str = "eu-west-1",
        guardar: bool = True,
    ) -> dict:
        # Lee los pickles diarios guardados en el almacenamiento (S3 o disco
        # local) y carga en la BD SOLO los dias que sean posteriores al ultimo
        # que ya tengamos guardado. Asi no se repiten cargas ni se pisan datos.
        #
        # Devuelve cuantos dias nuevos se cargaron y cuantas mediciones.
        import os

        import boto3
        import pandas as pd

        # 1) Averigua cual es el ultimo dia que ya esta en la BD.
        pool = self._loader._pool  # el loader tiene el pool de conexiones
        async with pool.acquire() as conn:
            ultima_bd = await conn.fetchval(
                "SELECT MAX(fecha) FROM meteo.mediciones_diarias"
            )

        # 2) Lista los pickles diarios que hay en el almacenamiento.
        #    Cada pickle es de UN dia, en la RAIZ, con nombre:
        #    "2026-08-17 00:00:00_2026-08-17T00:00:00UTC"
        dias_disponibles = []
        if s3_bucket:
            s3 = boto3.client("s3", region_name=s3_region)
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=s3_bucket):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    # Solo objetos de la RAIZ (sin "/") con nombre de fecha.
                    if "/" in key:
                        continue
                    try:
                        dia = date.fromisoformat(key[:10])
                        dias_disponibles.append((dia, key))
                    except ValueError:
                        pass  # no empieza por fecha: ALL_10_YEARS, estaciones.pkl...
        else:
            # Local: archivos .pkl en la raiz cuyo nombre empieza por fecha ISO.
            if os.path.isdir(ruta_local):
                for nombre in os.listdir(ruta_local):
                    ruta = os.path.join(ruta_local, nombre)
                    if not (os.path.isfile(ruta) and nombre.endswith(".pkl")):
                        continue
                    try:
                        dia = date.fromisoformat(nombre[:10])
                        dias_disponibles.append((dia, ruta))
                    except ValueError:
                        pass

        # 3) Filtra: solo los dias posteriores al ultimo que ya esta en la BD.
        if ultima_bd is not None:
            dias_nuevos = [(d, k) for d, k in dias_disponibles if d > ultima_bd]
        else:
            dias_nuevos = dias_disponibles  # la BD esta vacia: carga todo

        dias_nuevos.sort()  # de mas antiguo a mas nuevo

        # 4) Carga cada dia nuevo en la BD (si se pidio guardar).
        total_mediciones = 0
        for dia, origen in dias_nuevos:
            if s3_bucket:
                # Descarga el pickle de S3 a memoria.
                s3 = boto3.client("s3", region_name=s3_region)
                resp = s3.get_object(Bucket=s3_bucket, Key=origen)
                df = pd.read_pickle(resp["Body"])
            else:
                # Lee el pickle del disco local.
                df = pd.read_pickle(origen)

            limpio = self._transformer.transform(df)
            if guardar:
                total_mediciones += await self._loader.cargar_mediciones(limpio)
            else:
                # Sin guardar: solo cuenta lo que se habria cargado.
                total_mediciones += len(limpio)

        return {
            "ultimo_dia_en_bd": ultima_bd.isoformat() if ultima_bd else None,
            "dias_cargados": len(dias_nuevos),
            "mediciones_cargadas": total_mediciones,
        }

    # =====================================================================
    # RECARGA DESDE ALMACENAMIENTO (todo el historico, sin descargar de AEMET)
    # =====================================================================

    async def recargar_mediciones_desde_almacenamiento(
        self,
        ruta_local: str,
        s3_bucket: str = "",
        s3_region: str = "eu-west-1",
        guardar: bool = True,
    ) -> dict:
        # Carga en la BD TODO el historico de mediciones que haya guardado en
        # el almacenamiento (S3 o disco local), SIN descargar nada de la AEMET.
        #
        # A diferencia de cargar_mediciones_desde_almacenamiento (que solo carga
        # los dias posteriores al ultimo de la BD), aqui se carga TODO lo que
        # haya: sirve para la primera puesta en marcha o para recuperar la BD
        # entera si se ha perdido. Como el guardado es upsert (ON CONFLICT),
        # no duplica nada aunque la BD ya tenga parte de los datos.
        #
        # Con guardar=False solo lee y cuenta lo que cargaria.
        import os

        import boto3
        import pandas as pd

        # 1) Lista TODOS los pickles de mediciones (diarios + ALL_10_YEARS).
        #    Todo esta en la RAIZ: keys sin "/" excepto las carpetas MODELOS_RF/.
        origenes: list[str] = []
        if s3_bucket:
            s3 = boto3.client("s3", region_name=s3_region)
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=s3_bucket):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if "/" in key:
                        continue  # carpetas (MODELOS_RF/...): no son datos
                    if key in ("estaciones.pkl", "estaciones.csv", "LAST_UPDATE.txt"):
                        continue  # no son mediciones
                    if key == "ALL_10_YEARS" or key[:10].count("-") == 2:
                        origenes.append(key)  # historico o pickle diario
        else:
            # Local: pickles en la raiz (diarios + ALL_10_YEARS.pkl).
            if os.path.isdir(ruta_local):
                for nombre in os.listdir(ruta_local):
                    ruta = os.path.join(ruta_local, nombre)
                    if not (os.path.isfile(ruta) and nombre.endswith(".pkl")):
                        continue
                    if nombre == "estaciones.pkl":
                        continue
                    origenes.append(ruta)

        # 2) Lee cada pickle, limpia y carga en la BD (si se pidio guardar).
        total = 0
        for origen in origenes:
            if s3_bucket:
                resp = s3.get_object(Bucket=s3_bucket, Key=origen)
                df = pd.read_pickle(resp["Body"])
            else:
                df = pd.read_pickle(origen)

            limpio = self._transformer.transform(df)
            if guardar:
                total += await self._loader.cargar_mediciones(limpio)
            else:
                total += len(limpio)

        return {
            "pickles_procesados": len(origenes),
            "mediciones_cargadas": total,
        }

    async def recargar_estaciones_desde_almacenamiento(
        self,
        ruta_local: str,
        s3_bucket: str = "",
        s3_region: str = "eu-west-1",
        guardar: bool = True,
    ) -> dict:
        # Carga en la BD el inventario de estaciones que haya guardado en el
        # almacenamiento (S3 o disco local), SIN descargar nada de la AEMET.
        # Sirve para la primera puesta en marcha o para reconstruir la tabla.
        #
        # El pickle es el CRUDO tal como lo da la AEMET (coordenadas en formato
        # DMS): aqui se transforma a decimal antes de guardar, igual que en la
        # ingesta normal.
        import os

        import boto3
        import pandas as pd

        # 1) Localiza el pickle de estaciones (raiz del bucket: estaciones.pkl).
        if s3_bucket:
            s3 = boto3.client("s3", region_name=s3_region)
            key = "estaciones.pkl"
            try:
                resp = s3.get_object(Bucket=s3_bucket, Key=key)
            except Exception:
                return {"estaciones_cargadas": 0, "aviso": "no hay pickle de estaciones en S3"}
            crudo = pd.read_pickle(resp["Body"])
        else:
            ruta_pickle = os.path.join(ruta_local, "estaciones.pkl")
            if not os.path.isfile(ruta_pickle):
                return {"estaciones_cargadas": 0, "aviso": "no hay pickle de estaciones en local"}
            crudo = pd.read_pickle(ruta_pickle)

        # 2) Transforma y carga en la BD (si se pidio guardar).
        limpio = self._transformer.transform_estaciones(crudo)
        if guardar:
            cargadas = await self._loader.cargar_estaciones(limpio)
        else:
            cargadas = len(limpio)

        return {"estaciones_cargadas": cargadas}