#!/bin/sh
set -e

echo "waiting for postgres at ${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432}..."
i=0
until python -c "
import os, sys, psycopg
try:
    psycopg.connect(
        host=os.environ.get('POSTGRES_HOST', 'db'),
        port=os.environ.get('POSTGRES_PORT', '5432'),
        dbname=os.environ.get('POSTGRES_DB', 'crm'),
        user=os.environ.get('POSTGRES_USER', 'crm'),
        password=os.environ.get('POSTGRES_PASSWORD', ''),
        connect_timeout=2,
    ).close()
except Exception:
    sys.exit(1)
"; do
  i=$((i + 1))
  if [ "$i" -ge 60 ]; then
    echo "postgres did not become ready in time" >&2
    exit 1
  fi
  sleep 1
done
echo "postgres is ready."

# Only one service runs migrate. web and worker share this entrypoint, and
# running migrate from both at once races CREATE TABLE django_migrations
# (Postgres error: duplicate key value violates "pg_type_typname_nsp_index").
# Set RUN_MIGRATIONS=1 on exactly one service (web, in compose.yaml).
if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  python manage.py migrate --noinput

  if [ "${DJANGO_SETTINGS_MODULE}" = "config.settings.prod" ]; then
    python manage.py collectstatic --noinput
  fi
fi

exec "$@"
