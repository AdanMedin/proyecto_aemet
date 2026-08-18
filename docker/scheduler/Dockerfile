# Este Dockerfile crea la imagen para el scheduler (el servicio que ejecuta tareas automáticas).
# Uso Alpine Linux porque es muy pequeño y ligero (solo necesito ejecutar curl, nada más).
FROM alpine:3.20

# Alpine usa "apk" para instalar paquetes (en lugar de "apt" de Debian).
# Solo necesito:
# - cron: la herramienta que ejecuta tareas en horarios programados
# - curl: para hacer peticiones HTTP a mi API
RUN apk add --no-cache curl

# Copia el archivo con el horario de ejecución (qué tareas ejecutar y cuándo).
COPY docker/scheduler/crontab /etc/crontabs/root
# Copia el script que inicia el scheduler.
COPY docker/scheduler/entrypoint.sh /entrypoint.sh
# Da permisos de ejecución al script (sin esto, no puede ejecutarse).
RUN chmod +x /entrypoint.sh

# Cuando Docker inicia el contenedor, ejecuta este script automáticamente.
ENTRYPOINT ["/entrypoint.sh"]