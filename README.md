# Proyecto AEMET — Predicción de temperatura

Sistema que descarga datos meteorológicos de la AEMET (Agencia Estatal de Meteorología), los persiste en una base de datos y los utiliza para predecir la temperatura del día siguiente.

## Qué hace

A partir de unas coordenadas (latitud y longitud), devuelve la temperatura prevista para el día siguiente en las estaciones meteorológicas más cercanas a ese punto.

## Arquitectura general

El sistema se apoya en cuatro componentes principales:

**1. Base de datos (PostgreSQL)**, que almacena el histórico de mediciones de todas las estaciones de España. Cada estación registra medidas diarias (temperatura, humedad, precipitación...) que se persisten aquí.

**2. Proceso de ingesta automática**, que cada 5 días descarga los datos nuevos publicados por la AEMET, los normaliza (ya que la fuente original presenta bastantes inconsistencias de formato) y los inserta en la base de datos.

**3. Modelos de predicción** (uno por estación), entrenados con los datos históricos correspondientes. Se reentrenan cada 15 días incorporando la información más reciente. El algoritmo utilizado es Random Forest, un modelo de aprendizaje automático que combina múltiples árboles de decisión para mejorar la precisión de las predicciones.

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

Cada 5 días, el sistema ejecuta automáticamente el siguiente flujo:

1. Se conecta con la API de AEMET.
2. Descarga los datos de los últimos 5 días.
3. Los normaliza (la AEMET devuelve valores como "Acum", "Varias" o números con coma decimal, que se transforman a un formato consistente antes de persistirlos).
4. Los inserta en la base de datos.

**Por qué un intervalo de 5 días**: la AEMET tarda varios días en publicar los datos definitivos, por lo que este intervalo garantiza que la información esté disponible en el momento de la descarga.

## Los modelos de predicción

Existe un modelo independiente por estación, entrenado con su propio histórico de datos.

Cada modelo toma como entrada los últimos 20 días de temperatura y humedad para predecir la temperatura del día siguiente.

El reentrenamiento se ejecuta cada 15 días, incorporando los datos más recientes disponibles.

## Despliegue del proyecto

El proyecto está dockerizado, con los contenedores ya configurados. Solo es necesario definir las variables de entorno en el archivo `.env`:

```bash
docker compose up --build
```

Esto levanta cuatro servicios:

- **api** (puerto ****): la API. La documentación interactiva está disponible en `http://localhost:****/docs`.
- **postgres** (puerto ****): la base de datos.
- **scheduler**: el proceso encargado de programar las descargas automáticas.
- **pgadmin** (puerto ****): interfaz web para consultar la base de datos (opcional, útil para depuración).

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
[
  {
    "indicativo": "3195",
    "nombre": "Madrid, Retiro",
    "provincia": "Madrid",
    "distancia_km": 2.1,
    "fecha": "2026-08-18",
    "temperatura_prevista": 31.4
  }
]
```

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
- Descarga automática de datos de AEMET (probado con 921 estaciones y miles de mediciones).
- API respondiendo correctamente.
- Scheduler programando las tareas automáticas.

**Pendiente:**
- Acumular varios años de historial en la base de datos (los modelos necesitan bastantes datos para entrenar bien).
- Entrenar y guardar los modelos definitivos (archivos `.joblib`) para que las predicciones devuelvan valores reales.
- Desarrollar la interfaz web (Streamlit) para poder pedir predicciones sin necesidad de tocar código.

---

*Proyecto forecast*