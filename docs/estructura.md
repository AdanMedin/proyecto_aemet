# Estructura de Datos y Modelos - AEMET

## Resumen

Un único bucket S3 (`aemet-hab-2026`) contiene TODO: datos crudos, modelos ML, respaldos. Local espeja esa misma estructura plana.

---

## AWS S3 (Producción)

```
aemet-hab-2026/
├── estaciones.pkl                    # Inventario completo de estaciones (crudo)
├── ALL_10_YEARS                      # Histórico completo de mediciones (pickle)
├── 2026-08-17 00:00:00_2026-08-17T00:00:00UTC  # Mediciones diarias (formato fecha UTC)
├── LAST_UPDATE.txt                   # Timestamp última actualización
└── MODELOS_RF/
    ├── modelos_RF/                   # Modelos RandomForest actuales (.joblib)
    │   ├── 3195.joblib
    │   ├── 1690A.joblib
    │   └── ... (1 por estación)
    └── respaldo_RF/                  # Respaldo de modelos anteriores
        ├── 3195.joblib
        └── ... (versión anterior, solo UNA)
```

**Notas S3:**
- **Todo en raíz** (sin subcarpetas `raw/` ni `estaciones/`)
- **Formato fecha diaria**: `YYYY-MM-DD 00:00:00_YYYY-MM-DDT00:00:00UTC`
- **Respaldos automáticos**: Antes de subir modelos nuevos, se mueven los actuales a `respaldo_RF/`
- **Sin históricos acumulados**: Solo se guarda UNA versión de cada cosa

---

## Desarrollo Local (espejo exacto)

```
proyecto_aemet_api/
├── resources/                        # Espejo de la RAIZ del bucket
│   ├── estaciones.pkl                # Inventario estaciones
│   ├── estaciones.csv                # Legible en Excel (solo local)
│   ├── ALL_10_YEARS.pkl              # Histórico completo
│   ├── ALL_10_YEARS.csv              # Histórico en CSV (solo local)
│   ├── 2026-08-17 00:00:00_...UTC.pkl  # Pickle diario
│   ├── 2026-08-17 00:00:00_...UTC.csv  # CSV diario (solo local)
│   ├── raw_data_10_years.csv         # Referencias antiguas (no se generan)
│   ├── example.json                  # Ejemplos (no se ignoran)
│   └── .gitkeep                      # Mantiene carpeta en git
│
├── ml/
│   ├── artifacts/                    # Modelos actuales (.joblib)
│   │   ├── 3195.joblib
│   │   └── .gitkeep
│   └── artifacts_historicos/         # Respaldo local de modelos
│       └── .gitkeep
│
└── (otras carpetas de código)
```

**Notas local:**
- **Estructura plana**: Sin subcarpetas `raw/` o `estaciones/` (borradas)
- **CSV extra**: Solo en local, para poder abrir en Excel sin Python
- **Gitkeep**: Mantiene carpetas vacías en git (datos no se suben)

---

## Flujo de Datos

### 1. Ingesta (descarga de AEMET)

| Endpoint | Qué hace | Dónde guarda |
|----------|----------|-------------|
| `/admin/ingestar?estaciones=true&guardar_bd=true` | Descarga inventario | S3: `estaciones.pkl` raíz<br>Local: `resources/estaciones.pkl` + `.csv` |
| `/admin/ingestar?mediciones=true&guardar_bd=true` | Descarga último día disponible | S3: `2026-08-17 00:00:00_...UTC`<br>Local: `resources/2026-08-17 00:00:00_...UTC.pkl` + `.csv` |
| `/admin/ingestar?historico=true&guardar_bd=true` | Descarga histórico desde 2016 | S3: `ALL_10_YEARS`<br>Local: `resources/ALL_10_YEARS.pkl` + `.csv` |

### 2. Recarga (sin descargar de AEMET)

| Endpoint | Qué hace | Lee de |
|----------|----------|--------|
| `/admin/recargar?historico_mediciones=true&guardar_bd=true` | Carga TODAS las mediciones guardadas | S3: pickles diarios + `ALL_10_YEARS`<br>Local: `resources/*.pkl` |
| `/admin/recargar?incremental_mediciones=true&guardar_bd=true` | Carga solo días nuevos (posteriores a BD) | S3: pickles diarios<br>Local: `resources/20*.pkl` |
| `/admin/recargar?estaciones=true&guardar_bd=true` | Carga inventario de estaciones | S3: `estaciones.pkl`<br>Local: `resources/estaciones.pkl` |

### 3. Modelos ML

| Operación | Dónde |
|-----------|-------|
| Entrenamiento (`/admin/reentrenar`) | Sube nuevos a `MODELOS_RF/modelos_RF/`<br>Backup anterior a `MODELOS_RF/respaldo_RF/` |
| Descarga local | `ml/artifacts/` |
| Respaldo local | `ml/artifacts_historicos/` |

---

## Configuración Clave

### Variables de Entorno (.env)

```bash
# Bucket único (S3 o vacío para local-only)
S3_BUCKET=aemet-hab-2026          # Vacío = desarrollo local
AWS_REGION=eu-north-1             # Región AWS

# Rutas locales (sin S3)
RUTA_MODELOS=proyecto_aemet_api/ml/artifacts
RUTA_DATOS=proyecto_aemet_api/resources
```

### Docker Volumes (docker-compose.yml)

```yaml
volumes:
  # Modelos ML actuales
  - ./proyecto_aemet_api/ml/artifacts:/app/proyecto_aemet_api/ml/artifacts
  # Respaldo de modelos
  - ./proyecto_aemet_api/ml/artifacts_historicos:/app/proyecto_aemet_api/ml/artifacts_historicos
  # Datos (espejo de raíz S3, TODO plano)
  - ./proyecto_aemet_api/resources:/app/proyecto_aemet_api/resources
```

---

## Limpieza de Datos

### Qué se borra automáticamente

| Operación | Acción |
|-----------|--------|
| Nueva estaciones.pkl | Borra anterior (solo 1) |
| Nuevo histórico | Borra `ALL_10_YEARS` anterior (solo 1) |
| Reentrenamiento | Mueve modelos actuales a `respaldo_RF/`, sube nuevos |

### Qué NO se borra

- **Pickles diarios** (`2026-08-17 00:00:00_...UTC`): Se acumulan en S3/local
- **CSV en local**: Solo útiles para desarrollo, no se suben a S3

---

## Archivos Ignorados (git)

```
# Datos dinámicos (resources/)
proyecto_aemet_api/resources/raw_data*        # Referencias antiguas
proyecto_aemet_api/resources/*.pkl           # Pickles (diarios + histórico + estaciones)
proyecto_aemet_api/resources/estaciones.csv  # CSV estaciones
proyecto_aemet_api/resources/ALL_10_YEARS.csv
proyecto_aemet_api/resources/20*-*-* 00:00:00_*.csv

# Modelos entrenados
proyecto_aemet_api/ml/artifacts/*
proyecto_aemet_api/ml/artifacts_historicos/*

# Pero permitir estructura
!**/.gitkeep
```

---

## Tamaños Aproximados

| Elemento | Tamaño | Notas |
|----------|--------|-------|
| Pickle diario | ~10 MB | 1 día, todas las estaciones |
| ALL_10_YEARS | ~500 MB | Histórico completo desde 2016 |
| Modelo ML (.joblib) | ~18 MB | Por estación (RandomForest) |
| CSV estaciones | ~200 KB | Legible en Excel |
| CSV histórico | ~2 GB | Solo local, para análisis |

---

## Migración desde Estructura Anterior

Si tienes datos en carpetas viejas (`resources/raw/`, `resources/estaciones/`):

```bash
# Mover todo a raíz de resources/
mv proyecto_aemet_api/resources/raw/*/* proyecto_aemet_api/resources/
mv proyecto_aemet_api/resources/estaciones/* proyecto_aemet_api/resources/

# Borrar carpetas vacías
rmdir proyecto_aemet_api/resources/raw
rmdir proyecto_aemet_api/resources/estaciones
```

---

## Notas de Diseño

1. **Bucket único**: Simplifica gestión, reduce costos, evita duplicados
2. **Raíz plana**: Facilita búsqueda, evita complejidad de subcarpetas
3. **Formato UTC**: Nombres de archivo son legibles y sortables
4. **CSV solo local**: S3 no necesita CSV (pickle más eficiente), local sí para debugging
5. **Gitkeep**: Git trackea carpetas vacías, no archivos de datos (tamaño)