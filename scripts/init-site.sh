#!/bin/bash
set -euo pipefail

# Setup .env if it doesn't exist
if [ ! -f .env ]; then
  echo "Creating .env from .env.example..."
  cp .env.example .env
  echo "WARNING: Review .env and change default passwords/secrets before production."
fi

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
