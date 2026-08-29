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
cron -> POST /api/v1/admin/ingestar?estaciones=true&guardar_bd=true
        -> IngestionService.cargar_estaciones()
            -> AemetClient.obtener_estaciones()      (descarga el inventario)
            -> DataTransformer.transform_estaciones  (convierte lat/long de
                                                      formato DMS a decimal)
            -> DataLoader.cargar_estaciones          (upsert en meteo.estaciones)
```

La tabla `estaciones` es la referencia: sin una estación aquí (con sus
coordenadas), la API no la usa para predecir (la cache de estaciones solo carga
las que tienen datos recientes Y modelo entrenado).

**2. Mediciones (todos los días, 3:00 AM)**

```
cron -> POST /api/v1/admin/ingestar?mediciones=true&guardar_bd=true
        -> IngestionService.cargar_mediciones(1)
            -> AemetClient.obtener_mediciones(hoy-5, hoy-5)  (SOLO el último
                                                              día publicado)
            -> DataTransformer.transform  (limpia: "Ip" -> 0.05,
                                            "Acum"/"Varias" -> flags booleanos,
                                            comas -> puntos)
            -> DataLoader.cargar_mediciones  (upsert en meteo.mediciones_diarias)
```

Por qué hoy-5: la AEMET tarda unos 5 días en publicar los datos definitivos de
un día. Cada día se pide el día que acaba de quedar disponible. Si un día falla
la descarga, se puede recuperar después con
`/admin/recargar?incremental_mediciones=true` (lee los pickles guardados y carga
solo los días que falten en la BD), y el upsert (`ON CONFLICT`) evita
duplicados.

**3. Entrenamiento (día 1 de enero y julio, 4:00 AM — cada 6 meses)**

```
cron -> POST /api/v1/admin/reentrenar
        -> TrainingService.reentrenar()
            -> Lee TODO el histórico de meteo.mediciones_diarias
            -> Entrena un RandomForest por estación (solo las que tengan
               al menos 1500 días de datos, unos 4 años)
            -> Guarda cada modelo en ml/artifacts/<indicativo>.joblib
            -> Respaldo: los modelos anteriores se mueven a la carpeta
               histórica (en S3 "modelos_historicos/" o en local
               "artifacts_historicos/"), borrando el respaldo viejo. Solo se
               guarda UNA versión de respaldo.
            -> Si hay S3_BUCKET_MODELOS configurado, sube los nuevos a S3
```

Cómo aprende el modelo: cada ventana de 20 días predice el día que está 6 días
después de su final. Ese salto replica la situación real de producción: cuando
la API predice "mañana", la ventana disponible acaba en hoy-5 por el retraso de
la AEMET. Entrenar con ese desfase hace que el modelo aprenda exactamente lo
que va a tener que hacer.

**4. Predicción (cuando alguien llama a la API)**

```
POST /api/v1/prediccion {municipio: "Zuera, provincia de Zaragoza"}
    -> coordenadas_service: el modelo Gemini de Google convierte el nombre
       del municipio en coordenadas (latitud/longitud de su centro) y
       devuelve además el nombre oficial y la provincia.
    -> CacheEstaciones: busca las estaciones más cercanas (distancia
       Haversine, calculada en memoria sin tocar la BD; las 5 más cercanas
       en un radio de 50 km). Solo estaciones con datos recientes (última
       medición de hace 7 días como mucho) Y modelo.
    -> RegistroModelosLazy: carga el modelo de cada estación la primera vez
       que se pide y lo mantiene en RAM. Si hay S3 configurado, lo descarga
       de la nube DIRECTO A MEMORIA (sin tocar el disco).
    -> ObservationRepository: lee las últimas 20 mediciones de cada estación
    -> construir_features: monta el vector de entrada [sin, cos del día a
       predecir, humedad del último día, temperaturas de los 20 días]
    -> modelo.predict: devuelve la temperatura prevista para MAÑANA (hoy+1)
    -> La respuesta incluye además la temperatura ponderada por distancia
       (1/distancia). Si hay alguna estación a menos de 0.5 km, se devuelve
       directamente la de esa estación, sin mezclar.
```

**5. Consulta histórica en lenguaje natural (endpoint /eda)**

```
POST /api/v1/eda {consulta: "temperatura media de Barajas de marzo a junio de 2020"}
    -> EDAService: Gemini extrae de la frase el municipio y las fechas de
       inicio y fin. Después se validan: el inicio no puede ser posterior al
       fin, y el rango tiene que estar dentro de lo disponible (últimos 10
       años, hasta hoy-5).
    -> coordenadas_service: convierte el municipio en coordenadas.
    -> CacheEstaciones en modo EDA: aquí se usan TODAS las estaciones con
       datos recientes, tengan o no modelo (las 5 más cercanas en un radio
       de 100 km).
    -> ObservationRepository: lee las temperaturas medias diarias de esas
       estaciones en el periodo pedido.
    -> Para cada día se mezclan las estaciones ponderando por 1/distancia.
    -> Devuelve la serie fecha -> temperatura media, que es la que la web
       dibuja como gráfica.
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

Los pickles diarios se guardan SIEMPRE (a modo de copia de seguridad: si algún
día falla la carga en la BD, el dato sigue en S3 y se puede recuperar con el
endpoint `/admin/recargar` de la API).

### Piezas

- **RDS PostgreSQL**: la base de datos. Se crea con el mismo `bd_meteo_v2.sql`
  que en local.
- **S3 (bucket de datos crudos)**: aquí las Lambdas de descarga dejan los
  pickles. Organizado en dos prefijos: `raw/` (mediciones diarias) y
  `estaciones/` (inventario mensual).
- **S3 (bucket de modelos)**: aquí la Lambda de entrenamiento sube los
  `.joblib`. Además de la carpeta de modelos actual, hay una carpeta
  histórica con la versión anterior (respaldo por si algo sale mal).
- **EventBridge**: el despertador. Dispara las Lambdas de descarga en los
  horarios programados (equivalente al cron local).
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
        -> descarga las mediciones del último día publicado (hoy-5)
        -> guarda pickle crudo en s3://<bucket>/raw/<fecha>/mediciones.pkl

S3 detecta el archivo nuevo (evento ObjectCreated, prefijo "raw/")
    -> lambda_procesamiento_ingesta
        -> lee el pickle
        -> limpia los datos (mismo transformer que en local)
        -> DESCARTA las mediciones de estaciones que no existan en
           meteo.estaciones (la clave foránea lo exige; por eso hay que
           cargar primero el inventario)
        -> upsert en meteo.mediciones_diarias
```

**3. Entrenamiento (cada 6 meses)**

```
EventBridge (cron 1 de enero y 1 de julio)
    -> lambda_entrenamiento_standalone
        -> lee el histórico de RDS
        -> entrena un RandomForest por estación (mismos parámetros y mismo
           desfase de 6 días que en local)
        -> mueve los modelos actuales del bucket a la carpeta histórica
           (borrando el respaldo anterior)
        -> sube los .joblib nuevos a s3://<bucket-modelos>/<prefijo>/
```

**4. Predicción (cuando alguien llama a la API)**

Igual que en local: como los modelos los sube la Lambda a S3, la API (con
`S3_BUCKET_MODELOS` configurado) descarga de la nube los modelos que se piden,
DIRECTO A MEMORIA (sin escribir en el disco del servidor), y los cachea en RAM
con un presupuesto máximo.

### Orden de arranque la primera vez

1. Crear el RDS y ejecutar `docker/postgres/bd_meteo_v2.sql`.
2. Crear los dos buckets S3 (datos crudos y modelos).
3. Crear las 5 Lambdas con sus variables de entorno y permisos (las de
   procesamiento necesitan llegar al RDS: misma VPC y security group con el
   puerto 5432 abierto; las que escriben en S3 necesitan permisos sobre el
   bucket, y la de entrenamiento además s3:DeleteObject para el respaldo).
4. Configurar en el bucket de datos crudos dos notificaciones de eventos:
   prefijo `raw/` hacia `lambda_procesamiento_ingesta` y prefijo
   `estaciones/` hacia `lambda_procesamiento_estaciones`.
5. Invocar a mano `lambda_ingesta_estaciones` (evento `{}`) para llenar la
   tabla de estaciones. Sin este paso, las mediciones se descartarían todas.
6. Activar los schedulers de EventBridge (diario mediciones, mensual
   estaciones, cada 6 meses entrenamiento).

### Configuración de los recursos AWS

#### Bucket S3 de datos crudos

Aquí van los pickles que descargan las Lambdas de ingesta. Estructura de
carpetas (los "prefijos" de S3):

```
s3://<bucket-datos>/
├── raw/                          # mediciones diarias (SE GUARDAN SIEMPRE:
│   │                             #  copia de seguridad por si falla la BD)
│   ├── 2026-08-18/
│   │   └── mediciones.pkl
│   ├── 2026-08-19/
│   │   └── mediciones.pkl
│   ├── historico_2016-01-01_a_2026-08-23.pkl   # historico completo (solo UNO:
│   │                                           #  al descargar uno nuevo se
│   │                                           #  borra el anterior)
│   └── ...
└── estaciones/
    └── estaciones.pkl            # inventario (solo UNO: se reemplaza cada mes)
```

Notas:
- Los pickles diarios (`raw/<fecha>/mediciones.pkl`) nunca se borran
  automáticamente. Si se quieren limpiar los muy viejos, se puede configurar
  una regla de ciclo de vida en el bucket (ej. pasar a Glacier al año).
- Los históricos y el inventario se reemplazan: solo existe la versión más
  reciente de cada uno.
- En el bucket hay que crear **dos notificaciones de eventos**
  (Properties → Event notifications, evento `s3:ObjectCreated:*`):
  - prefijo `raw/` → destino `lambda_procesamiento_ingesta`
  - prefijo `estaciones/` → destino `lambda_procesamiento_estaciones`

#### Bucket S3 de modelos

Aquí van los modelos entrenados. Estructura:

```
s3://<bucket-modelos>/
├── MODELOS_RF/
│   └── modelos_RF/               # modelos ACTUALES (los que usa la API)
│       ├── 0009X.joblib
│       ├── 0016A.joblib
│       ├── metricas_modelos.joblib
│       └── ...                   # un .joblib por estación
└── MODELOS_RF/
    └── modelos_historicos/       # respaldo: la versión ANTERIOR (solo UNA)
        ├── 0009X.joblib
        └── ...
```

Notas:
- El prefijo exacto de los modelos actuales se configura con
  `S3_PREFIJO_MODELOS` (en nuestro caso `MODELOS_RF/modelos_RF`). OJO: S3
  distingue mayúsculas de minúsculas.
- El respaldo va en `S3_PREFIJO_MODELOS_HISTORICOS` (por defecto
  `modelos_historicos`). Al reentrenar: se borra el respaldo viejo, se mueven
  los actuales al histórico y se suben los nuevos.
- La API descarga los modelos a memoria bajo demanda; no necesita nada montado.

#### RDS PostgreSQL

- **Motor**: PostgreSQL 16 (la misma versión que el contenedor local).
- **Esquema**: se crea ejecutando `docker/postgres/bd_meteo_v2.sql` (crea el
  esquema `meteo`, las tablas `estaciones` y `mediciones_diarias` particionada
  por año, los índices y la vista `ultimos_10_dias`).
- **Conexión**: la app usa el esquema de URL `postgresql://usuario:pass@endpoint:5432/aemet`
  en la variable `DATABASE_DSN`.
- **Red**:
  - Las Lambdas de procesamiento y entrenamiento deben estar en la **misma VPC**
    que el RDS.
  - El security group del RDS debe aceptar conexiones al puerto **5432** desde
    el security group de las Lambdas.
  - Para desarrollo local contra el RDS (como hacemos ahora), el security group
    debe aceptar también tu IP pública en el 5432, o usar un túnel/bastión.
- **Particiones**: la tabla `mediciones_diarias` está particionada por año hasta
  2026 con una partición `default` de seguridad. Cuando llegue un año nuevo sin
  partición propia, los datos caen en la default; conviene crear la partición
  del año nuevo antes de enero (añadiendo una línea como las del script SQL).

### Variables de entorno en AWS

Cada Lambda lleva las suyas (detalladas en sus cabeceras). Resumen:

| Lambda | Variables |
|---|---|
| `lambda_ingesta_aemet` | `AEMET_API_KEY`, `S3_BUCKET_DATOS_RAW` |
| `lambda_ingesta_estaciones` | `AEMET_API_KEY`, `S3_BUCKET_DATOS_RAW` |
| `lambda_procesamiento_ingesta` | `DATABASE_DSN`, `S3_BUCKET_DATOS_RAW` |
| `lambda_procesamiento_estaciones` | `DATABASE_DSN`, `S3_BUCKET_DATOS_RAW` |
| `lambda_entrenamiento_standalone` | `DATABASE_DSN`, `S3_BUCKET_MODELOS`, `S3_PREFIJO_MODELOS`, `S3_PREFIJO_MODELOS_HISTORICOS`, `AWS_REGION` |

Y la API (donde esté desplegada): `DATABASE_DSN`, `S3_BUCKET_MODELOS`,
`S3_PREFIJO_MODELOS`, `AWS_REGION` y las credenciales AWS (en producción lo
normal es un rol IAM en vez de claves).

### Equivalencias local <-> AWS

| Tarea | Local | AWS |
|---|---|---|
| Mediciones | cron diario -> API (`mediciones=true`) | lambda_ingesta_aemet + lambda_procesamiento_ingesta |
| Estaciones | cron mensual -> API (`estaciones=true`) | lambda_ingesta_estaciones + lambda_procesamiento_estaciones |
| Entrenamiento | cron 6 meses -> API | lambda_entrenamiento_standalone |
| Modelos | disco (ml/artifacts) con respaldo en artifacts_historicos | S3, la API los descarga a memoria bajo demanda; respaldo en carpeta histórica |
| Base de datos | contenedor postgres | RDS PostgreSQL |

---

## Cómo probar los endpoints

Todas las pruebas se pueden hacer con `curl` (o Postman, o el propio Swagger en
`http://localhost:8000/docs`). Con la API levantada:

### Predicción

```bash
curl -X POST http://localhost:8000/api/v1/prediccion \
  -H "Content-Type: application/json" \
  -d '{"municipio": "Zuera, provincia de Zaragoza"}'
```

Devuelve el municipio y la provincia interpretados, la fecha predicha (mañana),
la temperatura ponderada por distancia, y el detalle de cada estación usada. Si
el punto está a menos de 0.5 km de una estación, la ponderada es directamente la
de esa estación.

### Consulta histórica (lenguaje natural)

```bash
curl -X POST http://localhost:8000/api/v1/eda \
  -H "Content-Type: application/json" \
  -d '{"consulta": "temperatura media de Barajas de marzo a junio de 2020"}'
```

Devuelve el municipio, la provincia y el rango de fechas interpretados, y la
serie diaria de temperatura media (ponderada entre las estaciones cercanas).
Si la consulta no se puede interpretar o las fechas están fuera de rango,
responde 400 con el motivo.

### Ingesta (modos, combinables)

Todos los modos aceptan `guardar_bd` (por defecto `false`: no escriben nada en
la base de datos, solo descargan y devuelven cuánto habrían cargado — útil para
probar sin riesgo).

```bash
# Solo estaciones (inventario), escribiendo en la BD
curl -X POST "http://localhost:8000/api/v1/admin/ingestar?estaciones=true&guardar_bd=true"

# Último día disponible (hoy-5), escribiendo en la BD — lo que hace el cron diario
curl -X POST "http://localhost:8000/api/v1/admin/ingestar?mediciones=true&guardar_bd=true"

# Histórico completo desde 2016: descarga y guarda pickle (S3 o disco+CSV)
curl -X POST "http://localhost:8000/api/v1/admin/ingestar?historico=true"

# Histórico completo + carga en la BD (tarda mucho: son millones de filas)
curl -X POST "http://localhost:8000/api/v1/admin/ingestar?historico=true&guardar_bd=true"

# Combinado: estaciones + último día, todo escribiendo
curl -X POST "http://localhost:8000/api/v1/admin/ingestar?estaciones=true&mediciones=true&guardar_bd=true"
```

### Recarga desde el almacenamiento

`/admin/recargar` NO descarga nada de la AEMET: carga en la BD lo que ya hay
guardado en S3 o en la carpeta local. Sirve para la primera puesta en marcha
o para reconstruir la BD si se ha perdido. Como el guardado es upsert
(`ON CONFLICT`), no duplica nada aunque la BD ya tenga datos.

```bash
# Primera puesta en marcha: estaciones + TODO el historico de mediciones
curl -X POST "http://localhost:8000/api/v1/admin/recargar?estaciones=true&historico_mediciones=true&guardar_bd=true"

# Recuperar dias que fallaron: carga solo los dias posteriores al ultimo de la BD
curl -X POST "http://localhost:8000/api/v1/admin/recargar?incremental_mediciones=true&guardar_bd=true"

# Prueba sin escribir nada (default guardar_bd=false)
curl -X POST "http://localhost:8000/api/v1/admin/recargar?historico_mediciones=true"
```

### Reentrenamiento

```bash
curl -X POST http://localhost:8000/api/v1/admin/reentrenar
```

Entrena un modelo por estación (solo las que tengan ~4 años de datos), hace
respaldo de la versión anterior (carpeta histórica, solo una versión) y, si hay
S3 configurado, sube los nuevos. Devuelve cuántos modelos entrenó, cuántas
estaciones se saltó por falta de datos y cuántos subió a S3.

### Salud

```bash
curl http://localhost:8000/api/v1/health
```
