# Proyecto AEMET — Predicción de temperatura

Sistema que descarga datos meteorológicos de la AEMET (Agencia Estatal de Meteorología), los persiste en una base de datos y los utiliza para predecir la temperatura del día siguiente.

## Qué hace

A partir de unas coordenadas (latitud y longitud), devuelve la temperatura prevista para el día siguiente en las estaciones meteorológicas más cercanas a ese punto.

## Arquitectura general

El sistema se apoya en cuatro componentes principales:

**1. Base de datos (PostgreSQL)**, que almacena el histórico de mediciones de todas las estaciones de España. Cada estación registra medidas diarias (temperatura, humedad, precipitación...) que se persisten aquí.

**2. Proceso de ingesta automática**, que cada día descarga los datos nuevos publicados por la AEMET (los del último día disponible, publicado con unos 5 días de retraso), los normaliza (ya que la fuente original presenta bastantes inconsistencias de formato) y los inserta en la base de datos.

**3. Modelos de predicción** (uno por estación), entrenados con los datos históricos correspondientes. Se reentrenan cada 6 meses incorporando la información más reciente. El algoritmo utilizado es Random Forest, un modelo de aprendizaje automático que combina múltiples árboles de decisión para mejorar la precisión de las predicciones.

**4. API REST**, que recibe las solicitudes de predicción, localiza las estaciones más cercanas a las coordenadas indicadas, aplica los modelos correspondientes y devuelve la temperatura prevista.

## Carpetas y qué hay en cada una

```
proyecto_aemet_api/
├── app/                    # aquí está la API
│   ├── main.py             # donde arranca todo
│   └── api/v1/endpoints/   # las rutas que responde
│
├── database/               # todo lo relacionado con la base de datos
│   └── repositories/       # las consultas SQL
│
├── ingestion/              # descarga y limpieza de datos de AEMET
│   ├── aemet_client.py     # se comunica con la API de AEMET
│   ├── loader.py           # guarda los datos en la base de datos
│   └── transformer.py      # limpia y normaliza los datos
│
├── ml/                     # modelos de predicción
│   ├── trainer.py          # entrena los modelos
│   ├── predictor.py        # genera las predicciones
│   └── artifacts/          # carpeta donde se guardan los modelos entrenados
│
├── services/               # la lógica que conecta todas las piezas
│   ├── ingestion_service.py    # coordina la descarga y limpieza
│   ├── training_service.py     # coordina el entrenamiento de modelos
│   └── forecast_service.py     # busca estaciones y genera predicciones
│
└── scripts/                # herramientas para pruebas manuales
    └── lambdas/            # funciones Lambda para AWS (producción): ingesta y entrenamiento sin pasar por la API
```

## Por qué está organizado así

El proyecto sigue el principio de separación de responsabilidades: cada módulo tiene una función bien delimitada. Si en el futuro cambia la forma de conectarse a la base de datos, solo hay que modificar la carpeta `database/`. Si cambia el modelo de predicción, solo se toca `ml/`. El resto del sistema permanece inalterado.

La lógica de negocio reside íntegramente en `services/`, y el planificador de tareas (scheduler) se limita a invocar la API mediante llamadas HTTP, sin conocer los detalles de implementación internos. `crontab` es el archivo de configuración del scheduler le dice al sistema qué comandos ejecutar y cuándo, de forma automática y repetida. `entrypoint.sh` arranca crond, proceso en segundo plano que lee el archivo `crontab` y ejecuta las tareas programadas en él.

## La base de datos

Se utiliza PostgreSQL, con dos tablas principales:

- **estaciones**: listado de las aproximadamente 900 estaciones meteorológicas (nombre, provincia, coordenadas).
- **mediciones_diarias**: mediciones registradas cada día (temperatura, humedad, precipitación, viento...).

Los datos de `mediciones_diarias` están particionados por año, lo que optimiza el rendimiento de las consultas. Al solicitar datos de 2024, PostgreSQL accede únicamente a esa partición, sin necesidad de recorrer el resto del histórico.

## El proceso de ingesta

Todos los días, el sistema ejecuta automáticamente el siguiente flujo:

1. Se conecta con la API de AEMET.
2. Descarga los datos del último día publicado (el de hoy menos 5 días).
3. Los normaliza (la AEMET devuelve valores como "Acum", "Varias" o números con coma decimal, que se transforman a un formato consistente antes de persistirlos).
4. Los inserta en la base de datos.

**Por qué el día de hoy menos 5**: la AEMET tarda unos 5 días en publicar los datos definitivos de un día. Cada día se descarga el día que acaba de quedar disponible. Si algún día falla la descarga, se puede recuperar después llamando a la API con un rango de días (los insert son idempotentes: no duplican).

## Los modelos de predicción

Existe un modelo independiente por estación, entrenado con su propio histórico de datos.

Cada modelo toma como entrada los últimos 20 días de temperatura y humedad. Como la AEMET publica con 5 días de retraso, cuando se pide la temperatura de mañana la ventana disponible acaba en hoy-5: el modelo se entrena precisamente para eso (cada ventana de 20 días aprende a predecir el día que está 6 días después de su final).

El reentrenamiento se ejecuta cada 6 meses (el día 1 de enero y de julio), incorporando los datos más recientes disponibles. También se puede lanzar a mano cuando se quiera. Antes de guardar los modelos nuevos, los anteriores se mueven a una carpeta histórica de respaldo (solo se conserva una versión anterior, por si algo sale mal).

## Despliegue del proyecto

Hay dos formas de desplegar el sistema: en local con Docker (desarrollo) y en AWS (producción). Ambas comparten la misma base de código, pero la ingesta y el entrenamiento funcionan distinto en cada una.

El flujo completo de cada entorno (qué pasa con un dato desde que nace en la AEMET hasta que se convierte en predicción) está explicado paso a paso en [docs/flujo.md](docs/flujo.md).

### Local (Docker, desarrollo)

El proyecto está dockerizado, con los contenedores ya configurados. Solo es necesario definir las variables de entorno en el archivo `.env`:

```bash
docker compose up --build
```

Esto levanta cuatro servicios:

- **api** (puerto ****): la API. La documentación interactiva está disponible en `http://localhost:****/docs`.
- **postgres** (puerto ****): la base de datos.
- **scheduler**: el proceso encargado de programar las descargas automáticas. Llama a la API por HTTP todos los días (ingesta del último día publicado), el día 1 de cada mes (inventario de estaciones) y cada 6 meses (reentrenamiento).
- **pgadmin** (puerto ****): interfaz web para consultar la base de datos (opcional, útil para depuración).

En local, toda la lógica (descarga AEMET, limpieza, guardado, entrenamiento) vive dentro de la API, en `proyecto_aemet_api/ingestion/`, `services/` y `ml/`.

### Producción (AWS)

En AWS la API solo sirve predicciones. La ingesta y el entrenamiento se hacen con funciones Lambda independientes (no pasan por la API), y los modelos se guardan en S3:

- **RDS PostgreSQL**: la base de datos, mismo esquema que en local (`docker/postgres/bd_meteo_v2.sql`).
- **S3**: dos buckets, uno para los datos crudos (pickles) y otro para los modelos entrenados (.joblib).
- **Lambdas** (código en `proyecto_aemet_api/scripts/lambdas/`, listas para pegar en la consola de AWS):

| Lambda | Frecuencia | Qué hace |
|---|---|---|
| `lambda_ingesta_aemet` | Diaria (EventBridge) | Descarga mediciones del último día disponible (hoy - 5 días) y guarda el pickle crudo en S3 |
| `lambda_procesamiento_ingesta` | Automática (trigger S3 `raw/`) | Lee el pickle, limpia los datos y los inserta en RDS |
| `lambda_ingesta_estaciones` | Mensual (EventBridge) | Descarga el inventario de estaciones y guarda el pickle en S3 |
| `lambda_procesamiento_estaciones` | Automática (trigger S3 `estaciones/`) | Lee el pickle, convierte las coordenadas y actualiza la tabla de estaciones |
| `lambda_entrenamiento_standalone` | Cada 6 meses (EventBridge) | Lee el histórico de RDS, entrena los modelos y los sube a S3 (con respaldo de la versión anterior en la carpeta histórica) |

El flujo es: EventBridge despierta a la Lambda de descarga, que deja el pickle en S3; S3 dispara automáticamente la Lambda de procesamiento, que escribe en RDS. Las mediciones de estaciones que no existan en la tabla `estaciones` se descartan (la clave foránea lo exige), así que la carga inicial del inventario debe hacerse antes que la primera ingesta de mediciones.

**Orden de arranque en AWS (la primera vez):**

1. Crear el RDS y ejecutar `docker/postgres/bd_meteo_v2.sql` para crear las tablas.
2. Invocar manualmente `lambda_ingesta_estaciones` (con evento `{}`) para llenar la tabla de estaciones.
3. Activar el scheduler diario de mediciones.
4. Activar el scheduler mensual de estaciones y el de entrenamiento.

## Configuración

Es necesario un archivo `.env` con:

- Las credenciales de conexión a la base de datos.
- La clave de la API de AEMET (`AEMET_API_KEY`).
- Opcionalmente, las credenciales de Amazon S3 para el almacenamiento de los modelos entrenados en la nube.

Basta con copiar el archivo `.env.example` y completar los valores correspondientes.

## Probar una predicción

Con todo el proyecto en marcha, se puede enviar una petición HTTP de la siguiente forma:

```bash
curl -X POST http://localhost:****/api/v1/prediccion \
  -H "Content-Type: application/json" \
  -d '{"latitud": 40.4168, "longitud": -3.7038, "k": 3, "max_distancia_km": 50}'
```

Y la respuesta será algo así:

```json
{
  "fecha": "2026-08-24",
  "temperatura_ponderada": 30.8,
  "estaciones": [
    {
      "indicativo": "3195",
      "nombre": "Madrid, Retiro",
      "provincia": "Madrid",
      "latitud": 40.411111,
      "longitud": -3.678056,
      "distancia_km": 2.1,
      "fecha": "2026-08-24",
      "temperatura_prevista": 31.4
    }
  ]
}
```

La fecha predicha es siempre mañana. `temperatura_ponderada` mezcla las estaciones según lo cerca que estén del punto pedido (si alguna está a menos de 0.5 km, se devuelve directamente la de esa estación). Todas las formas de probar los endpoints están en [docs/flujo.md](docs/flujo.md#cómo-probar-los-endpoints).

## Decisiones de diseño

**¿Por qué no hay un servicio de scheduler independiente con su propia lógica?**

En una versión inicial sí lo había, pero generaba duplicación de código: los mismos pasos (descarga, limpieza, persistencia) estaban implementados tanto en la API como en los scripts. Actualmente el scheduler se limita a realizar llamadas HTTP a la API, centralizando toda la lógica en un único punto.

**¿Por qué el caché de estaciones está en memoria y no en Redis?**

Redis ofrece un rendimiento muy alto, pero añade una dependencia y complejidad operativa adicionales. Al tratarse de un despliegue de un único servidor, un caché en memoria resulta suficiente y evita mantener un servicio adicional.

**¿Por qué las credenciales no están en el código fuente?**

Todas las credenciales se gestionan mediante el archivo `.env`, que está excluido de git.

## Estado actual

**Funcionando:**
- Base de datos con el esquema completo.
- Descarga automática diaria de datos de AEMET (probado con 921 estaciones y millones de mediciones).
- API respondiendo predicciones para mañana, con temperatura ponderada por distancia.
- Modelos entrenados y servidos desde disco local o desde S3 (se descargan a memoria bajo demanda).
- Respaldo automático de la versión anterior de los modelos en cada reentrenamiento.
- Lambdas de AWS listas en `proyecto_aemet_api/scripts/lambdas/` para el despliegue en producción.
- Scheduler programando las tareas automáticas (diario, mensual y semestral).

**Pendiente:**
- Desarrollar la interfaz web (Streamlit) para poder pedir predicciones sin necesidad de tocar código.

---

*Proyecto forecast*