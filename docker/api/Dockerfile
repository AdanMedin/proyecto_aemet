FROM python:3.12-slim

# No escribir .pyc y sin buffer de logs (se ven en tiempo real).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY proyecto_aemet_api ./proyecto_aemet_api

# Usuario sin privilegios (seguridad).
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "proyecto_aemet_api.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
