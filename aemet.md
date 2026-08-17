# Propuesta de Proyecto: Pipeline y API de Forecasting con AEMET

## 👥 Roles y Responsabilidades

### 🏗️ Data Architect
- **Responsable del Diseño en AWS:** 
    - Definir y configurar la arquitectura en la nube, incluyendo el bucket S3, la base de datos **PostgreSQL en RDS** y los permisos de los servicios.

### ⚙️ Data Engineer
- **Responsable del Pipeline de Datos:**
    - Implementar las funciones AWS Lambda para la extracción masiva inicial y diaria de datos de la API de AEMET, y para la carga y transformación de estos datos desde S3 a la base de datos.

### 🔬 Data Scientist
- **Responsable del Análisis y Modelado:**
  - Diseñar la lógica para interpretar las preguntas en lenguaje natural del endpoint de Q&A.
  - Entrenar un modelo de forecasting utilizando arquitectura decoder-only de transformers para predecir la temperatura de una ubicación específica.

### 🚀 ML Engineer
- **Responsable del Despliegue y la API:**
  - Envolver la lógica de consulta y el modelo de forecasting en una API robusta utilizando FastAPI.
  - Desplegar la aplicación FastAPI en una instancia de AWS EC2, asegurando que todos los endpoints sean funcionales.

---

## 📝 Fases del Proyecto

### **Fase 01: Infraestructura y Pipeline de Datos**
**Objetivo:** Construir un sistema automatizado que extraiga datos de observación de la AEMET y los almacene de forma estructurada en una base de datos **PostgreSQL**.

**Tareas Clave:**
1.  **Extracción de Datos (Data Engineer, Architect):**
    - Obtener la API Key de AEMET.
    - Crear una pipeline para realizar una extracción masiva de datos históricos de observación y guardarlos en un **bucket S3**.
    - Configurar una **AWS Lambda** con **EventBridge** para que se ejecute diariamente y obtenga las observaciones de las últimas 24 horas.

2.  **Procesamiento y Carga (Data Engineer, Architect):**
    - Desarrollar una segunda **AWS Lambda** que se active con un **trigger de S3** al recibir nuevos datos.
    - Esta función procesará los ficheros JSON y los cargará de forma estructurada en la base de datos **PostgreSQL**.

3.  **Exploratory Data Analysis (EDA) Preliminar (Data Scientist)**:
    - Conectarse a la base de datos PostgreSQL (o analizar directamente los crudos en S3) usando Jupyter Notebooks.
    - Analizar la calidad de los datos históricos: detección de valores nulos, outliers (ej. lecturas erróneas de sensores), y consistencia temporal.
    - Identificar patrones estacionales, correlaciones iniciales entre variables meteorológicas y seleccionar las características (features) más relevantes. Este análisis servirá como base tanto para el entrenamiento del modelo como para definir qué gráficos se mostrarán en la futura web de Streamlit.

**Entregables de esta fase:**
- Infraestructura en AWS (S3, RDS) configurada.
- Pipelines de datos automáticos (masivo y diario) funcionando.
- Base de datos poblada y actualizada con los datos de la AEMET.

### **Fase 02: Modelado, API y Despliegue**
**Objetivo:** Desarrollar una API que permita consultar datos meteorológicos históricos y predecir temperaturas futuras.

**Tareas Clave:**
1.  **Desarrollo del Modelo (Data Scientist):**
    - Utilizar los datos de una estación meteorológica para entrenar un modelo de forecasting que prediga la temperatura.

2.  **Desarrollo de la API (ML Engineer, Data Scientist):**
    - Crear una aplicación con **FastAPI** que incluya los siguientes endpoints:
      - `/ask`: Recibe una pregunta en texto (ej. *"Temperatura máxima en Madrid la semana pasada"*), la interpreta, construye una consulta a la base de datos y devuelve la respuesta.
      - `/forecast`: Recibe una ubicación y un número de días, y devuelve la predicción de temperatura generada por el modelo.

3.  **Despliegue (ML Engineer):**
    - Desplegar la aplicación FastAPI completa en una instancia **AWS EC2** para que sea accesible públicamente.

4.  **Desarrollo de la Interfaz Web interactiva (Data Scientist, ML Engineer)**:
    - **Exploratory Data Analysis (EDA)**: Construir una aplicación en Streamlit que muestre un dashboard interactivo con visualizaciones de los datos históricos (ej. series temporales de temperatura, distribuciones, mapas de estaciones de la AEMET, correlaciones)
    - **Integración de la API**: Conectar la web de Streamlit con los endpoints de FastAPI para:
        - Proveer una barra de búsqueda/chat que permita al usuario hacer preguntas en lenguaje natural (consumiendo `/ask`).
        - Crear un panel de control donde el usuario seleccione una estación meteorológica y un horizonte temporal para visualizar gráficamente las predicciones futuras (consumiendo `/forecast`).


**Entregables de esta fase:**
- Un modelo de forecasting entrenado y evaluado.
- API funcional desplegada con los dos endpoints implementados.
- Documentación de la API (generada por FastAPI/Swagger).
- Dashboard interactivo en Streamlit accesible públicamente para explorar el EDA y realizar predicciones.