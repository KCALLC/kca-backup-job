# Nightly encrypted Postgres backup job (DO App Platform scheduled job).
# pg_dump major version must be >= server major (server: PostgreSQL 18).
FROM postgres:18-alpine
RUN apk add --no-cache age python3 py3-pip \
 && pip3 install --no-cache-dir --break-system-packages b2sdk==2.*
WORKDIR /app
COPY ops/backup/backup.py /app/backup.py
COPY keys/age-backup.pub /app/age-backup.pub
ENTRYPOINT ["python3", "/app/backup.py"]
