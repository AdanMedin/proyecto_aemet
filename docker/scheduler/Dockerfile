FROM alpine:3.20

# Solo necesitamos cron y curl para llamar a los endpoints de la API.
RUN apk add --no-cache curl

COPY docker/scheduler/crontab /etc/crontabs/root
COPY docker/scheduler/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]