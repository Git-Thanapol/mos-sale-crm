#!/usr/bin/env bash
# Deploys the CRM to a remote host over SSH: backup -> git pull -> build ->
# up -d (migrate/collectstatic happen automatically inside docker/entrypoint.sh
# on the web container's boot, gated by RUN_MIGRATIONS=1) -> nginx reload ->
# health check.
#
# Config comes from environment variables, or scripts/deploy.env if present
# (gitignored — never commit real host/path values):
#   DEPLOY_HOST   remote hostname or IP                (required)
#   DEPLOY_USER   ssh user                              (required)
#   DEPLOY_PATH   absolute path to the repo on remote   (required)
#   DEPLOY_PORT   ssh port                               (default: 22)
#   DEPLOY_SSH_KEY path to a private key for -i          (optional)
#
# Usage: scripts/deploy.sh
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f scripts/deploy.env ]; then
  # shellcheck disable=SC1091
  source scripts/deploy.env
fi

: "${DEPLOY_HOST:?Set DEPLOY_HOST (env var or scripts/deploy.env)}"
: "${DEPLOY_USER:?Set DEPLOY_USER (env var or scripts/deploy.env)}"
: "${DEPLOY_PATH:?Set DEPLOY_PATH (env var or scripts/deploy.env)}"
DEPLOY_PORT="${DEPLOY_PORT:-22}"

SSH_OPTS=(-p "$DEPLOY_PORT")
if [ -n "${DEPLOY_SSH_KEY:-}" ]; then
  SSH_OPTS+=(-i "$DEPLOY_SSH_KEY")
fi
REMOTE="${DEPLOY_USER}@${DEPLOY_HOST}"
COMPOSE="docker compose -f compose.yaml -f compose.prod.yaml"

remote() {
  # shellcheck disable=SC2029
  ssh "${SSH_OPTS[@]}" "$REMOTE" "cd '$DEPLOY_PATH' && $1"
}

# Refuse to deploy local commits nobody pushed yet — remote only ever sees
# what's on the git remote, so an ahead-of-origin local tree would silently
# deploy stale code and look like it succeeded.
AHEAD="$(git rev-list --count '@{u}..HEAD' 2>/dev/null || echo 0)"
if [ "$AHEAD" != "0" ]; then
  echo "Local branch is $AHEAD commit(s) ahead of its upstream — push first." >&2
  exit 1
fi

echo "==> [1/6] Backing up remote database..."
remote "scripts/backup.sh"

echo "==> [2/6] Pulling latest code on $DEPLOY_HOST..."
remote "git fetch --all --prune && git pull --ff-only"

echo "==> [3/6] Building images..."
remote "$COMPOSE build"

echo "==> [4/6] Bringing up containers (migrate + collectstatic run automatically on web boot)..."
remote "$COMPOSE up -d"

echo "==> [5/6] Waiting for web, then reloading nginx..."
# nginx resolves the "web" upstream only when it (re)loads, and a recreated web
# container can get a new IP: without this reload nginx answers 502 until restarted.
# Up to 3 minutes because migrations run before gunicorn starts listening.
WEB_READY=0
for _ in $(seq 1 90); do
  if remote "$COMPOSE exec -T web python -c 'import socket; socket.create_connection((\"localhost\", 8000), 2)'" 2>/dev/null; then
    WEB_READY=1
    break
  fi
  sleep 2
done
if [ "$WEB_READY" != "1" ]; then
  echo "web did not start listening on port 8000 — check 'docker compose logs web' on remote." >&2
  exit 1
fi
remote "$COMPOSE exec -T nginx nginx -s reload"

echo "==> [6/6] Health check..."
sleep 5
STATUS="$(remote "$COMPOSE ps --format '{{.Service}} {{.State}}'")"
echo "$STATUS"
if echo "$STATUS" | grep -vE "running|healthy" | grep -q .; then
  echo "One or more containers are not running/healthy — check 'docker compose logs' on remote." >&2
  exit 1
fi

HTTP_CODE="$(remote "curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://localhost:8001/" || echo "000")"
if [ "$HTTP_CODE" != "200" ] && [ "$HTTP_CODE" != "302" ]; then
  echo "App did not respond as expected (HTTP $HTTP_CODE) — check 'docker compose logs web' on remote." >&2
  exit 1
fi

echo "Deploy OK — app responded HTTP $HTTP_CODE."
