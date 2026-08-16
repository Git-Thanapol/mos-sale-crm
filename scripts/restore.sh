#!/usr/bin/env bash
# Restores a backup produced by scripts/backup.sh. DESTRUCTIVE: drops and
# recreates the `public` schema in the target database first, then loads
# the dump. Requires interactive confirmation unless --yes is passed
# (for the drill / CI use; never alias this away in a shell profile).
#
# Usage: scripts/restore.sh <path/to/backup.sql.gz> [--yes]
set -euo pipefail

cd "$(dirname "$0")/.."

if [ $# -lt 1 ]; then
  echo "Usage: $0 <path/to/backup.sql.gz> [--yes]" >&2
  exit 1
fi

BACKUP_FILE="$1"
SKIP_CONFIRM="${2:-}"

if [ ! -f "$BACKUP_FILE" ]; then
  echo "File not found: $BACKUP_FILE" >&2
  exit 1
fi

POSTGRES_USER="$(grep -E '^POSTGRES_USER=' .env 2>/dev/null | cut -d= -f2-)"
POSTGRES_DB="$(grep -E '^POSTGRES_DB=' .env 2>/dev/null | cut -d= -f2-)"
POSTGRES_USER="${POSTGRES_USER:-crm}"
POSTGRES_DB="${POSTGRES_DB:-crm}"

echo "About to DROP and restore database '${POSTGRES_DB}' from ${BACKUP_FILE}."
if [ "$SKIP_CONFIRM" != "--yes" ]; then
  read -r -p "Type the database name (${POSTGRES_DB}) to confirm: " CONFIRM
  if [ "$CONFIRM" != "$POSTGRES_DB" ]; then
    echo "Confirmation did not match. Aborting." >&2
    exit 1
  fi
fi

echo "Dropping and recreating schema public..."
docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

echo "Restoring from ${BACKUP_FILE} ..."
gunzip -c "$BACKUP_FILE" | docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"

echo "Restore complete."
