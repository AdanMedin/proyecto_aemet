#!/bin/sh
set -e

echo "Scheduler AEMET arrancado (solo llama a la API)."
# crond de Alpine en primer plano.
crond -f -l 2
