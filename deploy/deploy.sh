#!/bin/bash
# Deploy the latest backend code to the production VM.
#
# What it does:
#   1. SSHes into the VM
#   2. Pulls the latest code from the backend production repo
#   3. Rebuilds and restarts the Docker containers
#
# The rebuild step re-runs pip install, so any dependency changes in
# requirements.txt are automatically picked up. Docker layer caching means
# this is fast when only application code changed (the pip layer is cached).
#
# Usage:
#   ./deploy/deploy.sh [user@host]
#
# Examples:
#   ./deploy/deploy.sh nicholas@34.123.45.67
#   ./deploy/deploy.sh nicholas@events.nicholasbosiacki.com
#
# Defaults to nicholas@events.nicholasbosiacki.com if no argument given.
set -e

HOST=${1:-"nicholas@events.nicholasbosiacki.com"}
APP_DIR="/opt/events_app_backend"

echo "=== Deploying backend to $HOST ==="
ssh "$HOST" "cd $APP_DIR && git pull && sudo docker compose -f docker-compose.prod.yml up -d --build"
echo "=== Backend deploy complete ==="
echo "Note: there will be a brief (~10s) downtime while the container restarts."
