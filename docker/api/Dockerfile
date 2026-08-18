# Este Dockerfile crea la imagen Docker para la API.
# Le dice a Docker exactamente cómo preparar un contenedor con Python y mi código.

# Empiezo con Python 3.12 slim (versión pequeña, para que el contenedor sea rápido).
FROM python:3.12-slim

# Configurar Python para que funcione mejor en Docker:
# - PYTHONDONTWRITEBYTECODE=1: No crear archivos .pyc (no los necesito en Docker)
# - PYTHONUNBUFFERED=1: Ver los logs en tiempo real
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Creo la carpeta /app dentro del contenedor (aquí va el código).
WORKDIR /app

# Copia el archivo requirements.txt desde mi máquina al contenedor.
COPY requirements.txt .
# Instala todas las librerías. --no-cache-dir = no guarda archivos temporales (ahorra espacio).
RUN pip install --no-cache-dir -r requirements.txt

# Copia mi código Python (la carpeta proyecto_aemet_api) al contenedor.
COPY proyecto_aemet_api ./proyecto_aemet_api

# Por seguridad, creo un usuario sin privilegios especiales ("appuser") y ejecuto la app con ese usuario.
# Así si alguien logra entrar al contenedor, tiene muy pocos permisos.
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

# Le digo a Docker que este contenedor va a escuchar en puerto 8000 (es informativo).
EXPOSE 8000

# Comando que ejecuta Docker cuando inicia el contenedor: lanza uvicorn con la app FastAPI.
CMD ["uvicorn", "proyecto_aemet_api.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
