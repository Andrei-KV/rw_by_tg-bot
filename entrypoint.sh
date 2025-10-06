#!/usr/bin/env bash
set -euo pipefail

DB_HOST="${DB_HOST:-rw-by-db}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-postgres}"
TIMEOUT=${DB_WAIT_TIMEOUT:-60}  # сек

echo "[entrypoint] Waiting for database ${DB_HOST}:${DB_PORT} (user ${DB_USER}) up to ${TIMEOUT}s..."

i=0
until pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" >/dev/null 2>&1; do
  i=$((i+1))
  if [ $i -ge $TIMEOUT ]; then
    echo "[entrypoint] ERROR: Postgres did not become available after ${TIMEOUT}s"
    exit 1
  fi
  sleep 1
done

echo "[entrypoint] Database is ready. Starting application..."
exec "$@"
