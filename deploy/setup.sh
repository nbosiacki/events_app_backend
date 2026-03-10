#!/bin/bash
# VM bootstrap script for events.nicholasbosiacki.com — backend
#
# Run ONCE on a fresh Ubuntu 22.04 GCP Compute Engine VM as root (or with sudo).
# This script is idempotent: safe to re-run if something fails partway through.
#
# What it does:
#   1. Installs Docker (needed to run the FastAPI backend and MongoDB containers)
#   2. Installs nginx (the public-facing web server / reverse proxy)
#   3. Installs Certbot (for free Let's Encrypt SSL certificates)
#   4. Clones the backend production repo to /opt/events_app_backend
#   5. Creates the directory where the frontend static files will live
#   6. Installs and activates the nginx site config
#
# What it does NOT do:
#   - Create the .env file (you must do this manually — it contains secrets)
#   - Issue the SSL certificate (requires DNS to be pointing at this VM first)
#   - Start the application (do this after the .env and SSL are in place)
#   - Deploy the frontend (done separately via rsync from your local machine)
#
# Usage:
#   sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/YOUR_USERNAME/events_app_backend/main/deploy/setup.sh)"
set -e

echo "=== [1/6] Installing Docker ==="
# We install the official Docker CE package rather than the Ubuntu-bundled
# docker.io package, because docker.io lags behind in releases and does not
# include the Compose plugin (docker compose v2).
apt-get update
apt-get install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

echo "=== [2/6] Installing nginx ==="
# nginx serves as the single public entry point for the VM.
# It handles SSL termination and routes traffic:
#   /api/* → FastAPI container on :8000
#   /*     → static files from /opt/events_frontend/dist/
apt-get install -y nginx

echo "=== [3/6] Installing Certbot ==="
# Certbot obtains and auto-renews Let's Encrypt TLS certificates.
# The python3-certbot-nginx plugin allows Certbot to automatically edit
# the nginx config after issuance.
apt-get install -y certbot python3-certbot-nginx

echo "=== [4/6] Cloning backend repository ==="
mkdir -p /opt/events_app_backend
git clone https://github.com/nbosiacki/events_app_backend.git /opt/events_app_backend
cd /opt/events_app_backend

echo "=== [5/6] Creating frontend static files directory ==="
# The frontend is deployed separately by rsyncing pre-built files from your
# local machine to this directory. nginx serves these files directly.
# Creating the directory here prevents nginx from failing to start with a
# "directory not found" error before the first frontend deploy.
mkdir -p /opt/events_frontend/dist

echo "=== [6/6] Installing nginx site config ==="
# The nginx config routes API traffic to the backend container and serves
# the frontend static files. We remove the default site to avoid conflicts.
cp deploy/nginx.conf /etc/nginx/sites-available/events.nicholasbosiacki.com
ln -sf /etc/nginx/sites-available/events.nicholasbosiacki.com /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo ""
echo "=== Bootstrap complete. Next steps (in order) ==="
echo ""
echo "  1. Point your DNS A record: events.nicholasbosiacki.com → this VM's external IP"
echo "     Verify with: dig events.nicholasbosiacki.com"
echo ""
echo "  2. Create the .env file with all production secrets:"
echo "     sudo nano /opt/events_app_backend/.env"
echo "     (See DEPLOYMENT.md for the full list of required variables)"
echo ""
echo "  3. Issue the SSL certificate (requires DNS to be resolving first):"
echo "     sudo certbot --nginx -d events.nicholasbosiacki.com"
echo ""
echo "  4. Start the backend and database:"
echo "     cd /opt/events_app_backend && sudo docker compose -f docker-compose.prod.yml up -d --build"
echo ""
echo "  5. Deploy the frontend from your local machine:"
echo "     ./deploy/deploy-frontend.sh  (run from the monorepo root)"
echo ""
