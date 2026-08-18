"""Subida y descarga de modelos en S3."""
from __future__ import annotations

import os

import boto3


class S3Storage:
    # Maneja los modelos (archivos .joblib) en Amazon S3. 
    # S3 es un servicio de AWS para guardar archivos en la nube.
    # Un "bucket" es el nombre que le ponemos a nuestra carpeta dentro de S3.
    
    # Las claves de acceso de Amazon NO estan escritas aqui (seria inseguro).
    # La libreria boto3 las lee sola de la configuracion (el archivo .env).

    def __init__(self, bucket: str, region: str = "eu-west-1", prefix: str = "modelos") -> None:
        # bucket = nombre de nuestra carpeta en la nube.
        # region = zona geografica de Amazon (eu-west-1 es Irlanda).
        # prefix = subcarpeta dentro del bucket donde van los modelos.
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._s3 = boto3.client("s3", region_name=region)

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
        subidos = 0
        for nombre in os.listdir(carpeta_local):
            if nombre.endswith(".joblib"):
                ruta = os.path.join(carpeta_local, nombre)
                # Quitamos la extension ".joblib" para quedarnos con el indicativo.
                self.subir_modelo(ruta, nombre[: -len(".joblib")])
                subidos += 1
        return subidos

    def descargar_modelo(self, indicativo: str, ruta_local: str) -> bool:
        # Baja un modelo de la nube al disco. Devuelve True si existia, False si no.
        try:
            self._s3.download_file(self._bucket, self._key(indicativo), ruta_local)
            return True
        except self._s3.exceptions.ClientError:
            return False
