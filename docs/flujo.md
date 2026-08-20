# Flujo de funcionamiento del sistema

Este documento explica cómo funciona el sistema de principio a fin, por separado
en los dos entornos donde vive: **local** (Docker, para desarrollo) y
**producción** (AWS). La idea es que cualquiera pueda entender qué pasa con un
dato desde que nace en la AEMET hasta que se convierte en una predicción.

---

## Local (Docker, desarrollo)

En local todo vive dentro de los contenedores que levanta `docker compose up`.
La API es el centro: la ingesta, el entrenamiento y las predicciones pasan por
ella. El scheduler no piensa, solo llama a la API por HTTP cuando toca.

### Piezas

- **postgres**: la base de datos. Al arrancar por primera vez ejecuta
  `docker/postgres/bd_meteo_v2.sql`, que crea el esquema `meteo` con las tablas
  `estaciones` y `mediciones_diarias`.
- **api**: la FastAPI. Tiene toda la lógica: habla con AEMET, limpia datos,
  entrena modelos y responde predicciones.
- **scheduler**: un contenedor con cron. No ejecuta lógica propia, solo hace
  `curl` a los endpoints de administración de la API en los horarios definidos
  en `docker/scheduler/crontab`.
- **pgadmin**: interfaz web para mirar la base de datos. Opcional, solo para
  depurar.

### Flujo de datos

**1. Estaciones (día 1 de cada mes, 2:00 AM)**

```
cron -> POST /api/v1/admin/ingestar?dias=1&estaciones=true
        -> IngestionService.cargar_estaciones()
            -> AemetClient.obtener_estaciones()      (descarga el inventario)
            -> DataTransformer.transform_estaciones  (convierte lat/long de formato DMS a decimal)
            -> DataLoader.cargar_estaciones          (upsert en meteo.estaciones)
```

La tabla `estaciones` es la referencia: sin una estación aquí (con sus
coordenadas), sus mediciones no se pueden guardar y la API no la puede usar
para predecir.

**2. Mediciones (todos los días, 3:00 AM)**

```
cron -> POST /api/v1/admin/ingestar?diario=true
        -> IngestionService.cargar_mediciones_dia()
            -> AemetClient.obtener_mediciones(dia, dia)  (SOLO el día de hoy-5)
            -> DataTransformer.transform                 (limpia: "Ip" -> 0.05, "Acum"/"Varias" -> flags booleanos, comas -> puntos)
            -> DataLoader.cargar_mediciones              (upsert en meteo.mediciones_diarias)
```

Por qué hoy-5: la AEMET tarda unos 5 días en publicar los datos definitivos de
un día. Cada día se pide el día que acaba de quedar disponible. Si un día falla
la descarga, se puede recuperar llamando a mano con `dias=N` (modo rango) y el
upsert (`ON CONFLICT`) evita duplicados.

**3. Entrenamiento (cada 15 días, 4:00 AM)**

```
cron -> POST /api/v1/admin/reentrenar
        -> TrainingService.reentrenar()
            -> Lee TODO el histórico de meteo.mediciones_diarias
            -> Entrena un RandomForest por estación (solo las que tengan al menos 1500 días de datos, unos 4 años)
            -> Guarda cada modelo en ml/artifacts/<indicativo>.joblib
            -> Si hay S3_BUCKET_MODELOS configurado, los sube a S3
```

**4. Predicción (cuando alguien llama a la API)**

```
POST /api/v1/prediccion {latitud, longitud}
    -> CacheEstaciones: busca las estaciones más cercanas (distancia Haversine, calculada en memoria sin tocar la BD)
    -> RegistroModelosLazy: carga el modelo de cada estación la primera vez que se pide y lo mantiene en RAM (si no está en disco y hay S3 configurado, lo descarga de la nube)
    -> ObservationRepository: lee las últimas 20 mediciones de cada estación
    -> construir_features: monta el vector de entrada [sin, cos, humedad del último día, temperaturas de los 20 días anteriores]
    -> modelo.predict: devuelve la temperatura prevista para el día siguiente
```

---

## Producción (AWS)

En AWS la API solo sirve predicciones. La ingesta y el entrenamiento se han
sacado de la API y viven como funciones Lambda independientes (código en
`proyecto_aemet_api/scripts/lambdas/`). Los datos crudos y los modelos se
guardan en S3, y la base de datos es un RDS PostgreSQL con el mismo esquema
que en local.

La idea del diseño: las Lambdas de descarga no tocan la base de datos (solo
descargan y dejan el dato crudo en S3), y las de procesamiento no hablan con
AEMET (solo leen de S3, limpian y guardan en RDS). S3 hace de intermediario y
de disparador: cuando cae un pickle nuevo, se ejecuta sola la Lambda que lo
procesa. Así, si la limpieza falla, el dato crudo sigue guardado y se puede
reprocesar sin volver a pedirlo a la AEMET.

### Piezas

- **RDS PostgreSQL**: la base de datos. Se crea con el mismo `bd_meteo_v2.sql` que en local.
- **S3 (bucket de datos crudos)**: aquí las Lambdas de descarga dejan los pickles. Organizado en dos prefijos: `raw/` (mediciones diarias) y `estaciones/` (inventario mensual).
- **S3 (bucket de modelos)**: aquí la Lambda de entrenamiento sube los `.joblib`, con prefijo `modelos/`.
- **EventBridge**: el despertador. Dispara las Lambdas de descarga en los horarios programados (equivalente al cron local).
- **5 Lambdas**: descritas en detalle en sus propios archivos.

### Flujo de datos

**1. Estaciones (mensual)**

```
EventBridge (cron mensual)
    -> lambda_ingesta_estaciones
        -> descarga el inventario de AEMET
        -> guarda pickle crudo en s3://<bucket>/estaciones/<fecha>/estaciones.pkl

S3 detecta el archivo nuevo (evento ObjectCreated, prefijo "estaciones/")
    -> lambda_procesamiento_estaciones
        -> lee el pickle
        -> convierte coordenadas DMS a decimal
        -> upsert en meteo.estaciones
```

**2. Mediciones (diaria)**

```
EventBridge (cron diario)
    -> lambda_ingesta_aemet
        -> descarga las mediciones del día de hoy-5
        -> guarda pickle crudo en s3://<bucket>/raw/<fecha>/mediciones.pkl

S3 detecta el archivo nuevo (evento ObjectCreated, prefijo "raw/")
    -> lambda_procesamiento_ingesta
        -> lee el pickle
        -> limpia los datos (mismo transformer que en local)
        -> DESCARTA las mediciones de estaciones que no existan en meteo.estaciones (la clave foránea lo exige; por eso hay que cargar primero el inventario)
        -> upsert en meteo.mediciones_diarias
```

**3. Entrenamiento (cada 15 días)**

```
EventBridge (cron cada 15 días)
    -> lambda_entrenamiento_standalone
        -> lee el histórico de RDS
        -> entrena un RandomForest por estación (mismos parámetros que en local)
        -> sube los .joblib a s3://<bucket-modelos>/modelos/
```

**4. Predicción (cuando alguien llama a la API)**

Igual que en local, con una diferencia: como los modelos los sube la Lambda a
S3, la API (con `S3_BUCKET_MODELOS` configurado) descarga de la nube los
modelos que no tenga en disco la primera vez que se piden, y los cachea en RAM
como siempre.

### Orden de arranque la primera vez

1. Crear el RDS y ejecutar `docker/postgres/bd_meteo_v2.sql`.
2. Crear los dos buckets S3 (datos crudos y modelos).
3. Crear las 5 Lambdas con sus variables de entorno y permisos (las de
   procesamiento necesitan llegar al RDS: misma VPC y security group con el
   puerto 5432 abierto; las que escriben en S3 necesitan permisos sobre el
   bucket).
4. Configurar en el bucket de datos crudos dos notificaciones de eventos:
   prefijo `raw/` hacia `lambda_procesamiento_ingesta` y prefijo
   `estaciones/` hacia `lambda_procesamiento_estaciones`.
5. Invocar a mano `lambda_ingesta_estaciones` (evento `{}`) para llenar la
   tabla de estaciones. Sin este paso, las mediciones se descartarían todas.
6. Activar los schedulers de EventBridge (diario mediciones, mensual
   estaciones, 15 días entrenamiento).

### Equivalencias local <-> AWS

| Tarea | Local | AWS |
|---|---|---|
| Mediciones | cron diario -> API | lambda_ingesta_aemet + lambda_procesamiento_ingesta |
| Estaciones | cron mensual -> API | lambda_ingesta_estaciones + lambda_procesamiento_estaciones |
| Entrenamiento | cron 15 días -> API | lambda_entrenamiento_standalone |
| Modelos | disco (ml/artifacts) | S3, la API los descarga bajo demanda |
| Base de datos | contenedor postgres | RDS PostgreSQL |
