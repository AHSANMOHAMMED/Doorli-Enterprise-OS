#!/bin/bash
set -e

# Setup .env if it doesn't exist
if [ ! -f .env ]; then
  echo "Creating .env from .env.example..."
  cp .env.example .env
fi

echo "Starting Doorli Enterprise OS..."
docker compose up -d

echo "Waiting for backend services to initialize..."
sleep 15

echo "Creating the initial site and permanently disabling telemetry..."
# We run the create-site container which is specially defined in docker-compose.yml 
# to run bench new-site and IMMEDIATELY disable telemetry.
docker compose up create-site

echo "Restarting containers to apply configurations..."
docker compose restart backend queue-default queue-short queue-long scheduler websocket

echo "=============================================="
echo "Doorli Enterprise OS initialized successfully!"
echo "Telemetry tracking is PERMANENTLY DISABLED."
echo "=============================================="
