"""Subida y descarga de modelos en S3."""
from __future__ import annotations

import io
import os

import boto3
import joblib
import requests
from botocore.config import Config


class S3Storage:
    # Maneja los modelos (archivos .joblib) en Amazon S3. 
    # S3 es un servicio de AWS para guardar archivos en la nube.
    # Un "bucket" es el nombre que le ponemos a nuestra carpeta dentro de S3.
    
    # Las claves de acceso de Amazon NO estan escritas aqui (seria inseguro).
    # La libreria boto3 las lee sola de la configuracion (el archivo .env).

    def __init__(self, bucket: str, region: str = "eu-west-1", prefix: str = "modelos", prefix_historico: str = "modelos_historicos") -> None:
        # bucket = nombre de nuestra carpeta en la nube.
        # region = zona geografica de Amazon (eu-west-1 es Irlanda).
        # prefix = subcarpeta dentro del bucket donde van los modelos.
        # prefix_historico = subcarpeta de respaldo: aqui se mueven los modelos
        #   actuales justo antes de subir los nuevos (por si algo sale mal).
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._prefix_historico = prefix_historico.strip("/")

        # Configuracion de boto3 pensada para descargas grandes:
        # - max_pool_connections: cuantas descargas en paralelo puede hacer.
        # - retries: si una descarga se atasca, reintenta rapido en vez de esperar.
        # - connect_timeout/read_timeout: si S3 no responde en X segundos, corta y reintenta.
        config = Config(
            max_pool_connections=20,
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=10,
            read_timeout=60,
        )
        self._s3 = boto3.client("s3", region_name=region, config=config)

    def _key(self, indicativo: str) -> str:
        # La "key" es la ruta completa del archivo dentro de S3, por ejemplo "modelos/3195.joblib". Cada estacion tiene su propio archivo.
        return f"{self._prefix}/{indicativo}.joblib"

    def subir_modelo(self, ruta_local: str, indicativo: str) -> str:
        # Sube un modelo del disco a la nube. Devuelve su direccion s3 para saber donde quedo guardado.
        key = self._key(indicativo)
        self._s3.upload_file(ruta_local, self._bucket, key)
        return f"s3://{self._bucket}/{key}"

    def subir_carpeta(self, carpeta_local: str) -> int:
        # Sube TODOS los modelos .joblib de una carpeta. Devuelve cuantos subio.
        #
        # Antes de subir los nuevos, hace respaldo de los actuales:
        #   1) borra lo que haya en la carpeta historica (solo guardamos UNA
        #      version de respaldo, la mas reciente)
        #   2) mueve ahi los modelos actuales del bucket
        #   3) sube los nuevos
        # Asi, si la subida falla a medias, la carpeta historica tiene la
        # version anterior completa para recuperarla.
        self._mover_actuales_a_historico()
        subidos = 0
        for nombre in os.listdir(carpeta_local):
            if nombre.endswith(".joblib"):
                ruta = os.path.join(carpeta_local, nombre)
                # Quitamos la extension ".joblib" para quedarnos con el indicativo.
                self.subir_modelo(ruta, nombre[: -len(".joblib")])
                subidos += 1
        return subidos

    def _mover_actuales_a_historico(self) -> None:
        # Mueve todos los .joblib del prefijo actual al prefijo historico.
        # En S3 no existe "mover": hay que copiar y luego borrar el original.
        paginator = self._s3.get_paginator("list_objects_v2")

        # 1) Borra el respaldo anterior (solo queremos UNA version historica).
        for page in paginator.paginate(Bucket=self._bucket, Prefix=self._prefix_historico):
            for obj in page.get("Contents", []):
                self._s3.delete_object(Bucket=self._bucket, Key=obj["Key"])

        # 2) Copia los modelos actuales al historico y borra los originales.
        for page in paginator.paginate(Bucket=self._bucket, Prefix=self._prefix + "/"):
            for obj in page.get("Contents", []):
                key_vieja = obj["Key"]
                if not key_vieja.endswith(".joblib"):
                    continue
                nombre = key_vieja[len(self._prefix) + 1 :]  # "3195.joblib"
                key_nueva = f"{self._prefix_historico}/{nombre}"
                self._s3.copy_object(
                    Bucket=self._bucket,
                    CopySource={"Bucket": self._bucket, "Key": key_vieja},
                    Key=key_nueva,
                )
                self._s3.delete_object(Bucket=self._bucket, Key=key_vieja)

    def descargar_modelo(self, indicativo: str, ruta_local: str) -> bool:
        # Baja un modelo de la nube al disco. Devuelve True si existia, False si no.
        try:
            self._s3.download_file(self._bucket, self._key(indicativo), ruta_local)
            return True
        except self._s3.exceptions.ClientError:
            return False

    def obtener_modelo_en_memoria(self, indicativo: str):
        # Baja el modelo de la nube DIRECTO A MEMORIA (sin tocar el disco) y lo
        # devuelve ya listo para usar. Si no existe, devuelve None.
        #
        # En vez de get_object de boto3 (que en algunas redes va muy lento),
        # generamos una URL firmada temporal y descargamos con requests, que
        # va a la velocidad normal de la red.
        try:
            url = self._s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": self._key(indicativo)},
                ExpiresIn=300,  # la URL vale 5 minutos
            )
            resp = requests.get(url, timeout=120)
            if resp.status_code == 404:
                return None  # esa estacion no tiene modelo en la nube
            resp.raise_for_status()
            # joblib.load acepta cualquier "archivo" abierto en bytes:
            # BytesIO hace de archivo falso sobre los bytes descargados.
            return joblib.load(io.BytesIO(resp.content))
        except self._s3.exceptions.ClientError:
            return None

    def obtener_tamano_mb(self, indicativo: str) -> float:
        # Pregunta a S3 cuanto pesa el modelo SIN descargarlo (head_object solo
        # pide los metadatos). Si no existe, devuelve 0.
        try:
            resp = self._s3.head_object(Bucket=self._bucket, Key=self._key(indicativo))
            return resp["ContentLength"] / (1024 * 1024)
        except self._s3.exceptions.ClientError:
            return 0.0

    def listar_modelos(self) -> set[str]:
        # Devuelve los indicativos de TODAS las estaciones que tienen modelo
        # en S3. Sirve para filtrar la cache de estaciones: las que no esten
        # aqui no tienen modelo y no se pueden usar para predecir.
        indicativos = set()
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=self._prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith(".joblib"):
                    # Quita el prefijo y la extension para quedarse con el indicativo.
                    indicativos.add(key[len(self._prefix) + 1 : -len(".joblib")])
        return indicativos
