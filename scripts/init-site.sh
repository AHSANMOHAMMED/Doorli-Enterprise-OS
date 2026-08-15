#!/bin/bash
set -euo pipefail

# Production startup must never silently use placeholder credentials.
if [ ! -f .env ]; then
  echo "Missing .env. Copy .env.example, set production values, then rerun." >&2
  exit 1
fi
set -a
# shellcheck disable=SC1091
. ./.env
set +a
: "${DOORLI_WEBHOOK_SECRET:?DOORLI_WEBHOOK_SECRET must be set in .env}"
: "${DB_ROOT_PASSWORD:?DB_ROOT_PASSWORD must be set in .env}"
: "${ADMIN_PASSWORD:?ADMIN_PASSWORD must be set in .env}"
case "$DB_ROOT_PASSWORD:$ADMIN_PASSWORD:$DOORLI_WEBHOOK_SECRET" in
  *CHANGE_ME*) echo "Placeholder credentials are not allowed in production .env" >&2; exit 1 ;;
esac

echo "Starting core infra (db/redis)..."
docker compose up -d db redis-cache redis-queue

echo "Waiting for database..."
sleep 10

echo "Running configurator..."
docker compose up configurator

echo "Creating/installing site + doorli_core..."
docker compose up create-site

echo "Starting application services..."
docker compose up -d backend frontend websocket scheduler queue-default queue-short queue-long traefik

echo "Restarting workers to pick up doorli_core..."
docker compose restart backend queue-default queue-short queue-long scheduler websocket

echo "=============================================="
echo "Doorli Enterprise OS initialized."
echo "Ensure FRAPPE_SITE_NAME_HEADER DNS points here and ports 80/443 are open."
echo "=============================================="
