#!/usr/bin/env bash
# Dumps the CRM Postgres database via `docker compose exec db pg_dump`.
# Intended to run from Windows Task Scheduler / cron calling this script
# from the repo root, or manually before a risky operation.
#
# Usage: scripts/backup.sh [output_dir]   (default: ./backups)
set -euo pipefail

cd "$(dirname "$0")/.."

OUT_DIR="${1:-backups}"
mkdir -p "$OUT_DIR"

POSTGRES_USER="$(grep -E '^POSTGRES_USER=' .env 2>/dev/null | cut -d= -f2-)"
POSTGRES_DB="$(grep -E '^POSTGRES_DB=' .env 2>/dev/null | cut -d= -f2-)"
POSTGRES_USER="${POSTGRES_USER:-crm}"
POSTGRES_DB="${POSTGRES_DB:-crm}"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
OUT_FILE="$OUT_DIR/crm_${TIMESTAMP}.sql.gz"

echo "Backing up database '${POSTGRES_DB}' to ${OUT_FILE} ..."
docker compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges | gzip > "$OUT_FILE"

echo "Done: $(du -h "$OUT_FILE" | cut -f1)  ->  $OUT_FILE"
