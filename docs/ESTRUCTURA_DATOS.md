# Estructura de Datos (Local ↔ S3)

## Espejo Exacto

Local y AWS S3 comparten la **misma estructura plana** para evitar configuraciones divergentes.

```
Local (proyecto/)                S3 (aemet-hab-2026)
├── datos/                       ├── (raiz)
│   ├── estaciones.pkl           │   ├── estaciones.pkl
│   ├── estaciones.csv           │   ├── estaciones.csv
│   ├── ALL_10_YEARS.pkl         │   ├── ALL_10_YEARS
│   ├── ALL_10_YEARS.csv         │   ├── ALL_10_YEARS.csv
│   ├── 2026-08-17 00:00:00_    │   ├── 2026-08-17 00:00:00_
│   │   2026-08-17T00:00:00UTC.pkl  │   2026-08-17T00:00:00UTC
│   ├── ... (diarios)            │   ├── ... (diarios)
│   └── MODELOS_RF/              │   └── MODELOS_RF/
│       ├── modelos_RF/          │       ├── modelos_RF/
│       │   └── *.joblib         │       │   └── *.joblib
│       └── respaldo_RF/         │       └── respaldo_RF/
│           └── *.joblib         │           └── *.joblib
```

## Convenciones

| Tipo | Local | S3 | Formato |
|------|-------|----|---------|
| **Estaciones** | `datos/estaciones.pkl` | `estaciones.pkl` | Pickle único |
| **Histórico** | `datos/ALL_10_YEARS.pkl` | `ALL_10_YEARS` | Pickle único |
| **Diarios** | `datos/2026-08-17 00:00:00_2026-08-17T00:00:00UTC.pkl` | `2026-08-17 00:00:00_2026-08-17T00:00:00UTC` | Pickle por día |
| **Modelos** | `datos/MODELOS_RF/modelos_RF/*.joblib` | `MODELOS_RF/modelos_RF/*.joblib` | Joblib por estación |
| **Respaldo** | `datos/MODELOS_RF/respaldo_RF/*.joblib` | `MODELOS_RF/respaldo_RF/*.joblib` | Joblib por estación |

## Variables de Entorno

```bash
# Bucket único (vacío = solo local)
S3_BUCKET=aemet-hab-2026
AWS_REGION=eu-north-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...

# Rutas (por defecto, no hace falta definir en .env)
RUTA_DATOS=datos
RUTA_MODELOS=datos/MODELOS_RF/modelos_RF
RUTA_MODELOS_HISTORICOS=datos/MODELOS_RF/respaldo_RF
```

## Flujo de Datos

1. **Ingesta** → Guarda en `datos/` (local) o raíz bucket (S3)
2. **Recarga** → Lee de `datos/` (local) o raíz bucket (S3)
3. **Entrenamiento** → Lee modelos de `datos/MODELOS_RF/modelos_RF/`
4. **Respaldo** → Mueve modelos actuales a `datos/MODELOS_RF/respaldo_RF/` antes de subir nuevos

## Docker

- **Volumen**: `./datos:/app/datos` (monta carpeta completa)
- **Dockerfile**: `WORKDIR /app` → rutas relativas a `/app/`
- **`.dockerignore`**: excluye `datos/` (se monta por volumen)
- **`.gitignore`**: excluye `datos/*` (excepto `.gitkeep`)

## Limpieza (YA HECHO)

- ✅ `datos/` está en `.gitignore` (excepto `.gitkeep`)
- ✅ `datos/` está en `.dockerignore` (se monta por volumen)
- ✅ `proyecto_aemet_api/ml/artifacts/` y `ml/artifacts_historicos/` eliminadas
- ✅ `proyecto_aemet_api/resources/raw/` y `resources/estaciones/` eliminadas
- ⚠️ `proyecto_aemet_api/resources/` quedan solo CSV/JSON históricos de referencia (10 años)
