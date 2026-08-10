FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY proyecto_aemet_api ./proyecto_aemet_api

EXPOSE 8000

CMD ["uvicorn", "proyecto_aemet_api.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
