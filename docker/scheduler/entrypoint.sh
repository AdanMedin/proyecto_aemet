#!/bin/sh
# Este script es el punto de entrada del contenedor del scheduler.
# Se ejecuta automáticamente cuando Docker inicia el contenedor.

set -e  # Si hay algún error, detiene el script inmediatamente.

echo "Scheduler AEMET arrancado (solo llama a la API)."
# Inicia cron (crond) en primer plano (no como background)
# Flags: -f = foreground (primer plano), -l 2 = nivel de log (2 = info/errores)
crond -f -l 2
