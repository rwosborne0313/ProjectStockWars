#!/usr/bin/env bash
set -euo pipefail

# One-time provisioning script for Ubuntu 22.04 EC2.
# Run on the EC2 instance as ubuntu:
#   bash scripts/ec2_provision_server.sh
#
# What it does:
# - installs OS deps (nginx, python build deps)
# - creates a dedicated system user (stockwars) and directories under /opt/stockwars
# - creates a self-signed TLS cert valid for 180 days (~6 months)
# - configures nginx (HTTPS + WebSockets) proxying to Daphne via unix socket
# - creates a systemd service for Daphne (ASGI)
#
# IMPORTANT:
# - You must create /opt/stockwars/.env separately (DB creds, secret key, etc).

APP_USER="stockwars"
BASE_DIR="/opt/stockwars"
APP_DIR="/opt/stockwars/app"
SOCK_DIR="/run/daphne"
SOCK_PATH="${SOCK_DIR}/stockwars.sock"

# Cert CN: set STOCKWARS_CERT_CN or we default to instance public hostname.
CERT_CN="${STOCKWARS_CERT_CN:-}"
if [[ -z "${CERT_CN}" ]]; then
  CERT_CN="$(curl -s http://169.254.169.254/latest/meta-data/public-hostname || true)"
fi
CERT_CN="${CERT_CN:-localhost}"

echo "Provisioning packages..."
sudo apt-get update
sudo apt-get install -y \
  python3-venv python3-dev build-essential pkg-config \
  nginx postgresql-client libpq-dev \
  openssl curl

echo "Creating app user + directories..."
if ! id -u "${APP_USER}" >/dev/null 2>&1; then
  sudo useradd --system --create-home --shell /bin/bash "${APP_USER}"
fi

sudo mkdir -p "${BASE_DIR}" "${APP_DIR}" "${BASE_DIR}/static" "${BASE_DIR}/media" "${SOCK_DIR}"
sudo chown -R "${APP_USER}:${APP_USER}" "${BASE_DIR}" "${SOCK_DIR}"

echo "Generating self-signed cert for CN=${CERT_CN} (180 days)..."
sudo mkdir -p /etc/ssl/stockwars
sudo openssl req -x509 -nodes -newkey rsa:2048 -days 180 \
  -keyout /etc/ssl/stockwars/stockwars.key \
  -out /etc/ssl/stockwars/stockwars.crt \
  -subj "/CN=${CERT_CN}"

echo "Writing nginx site config..."
sudo tee /etc/nginx/sites-available/stockwars >/dev/null <<'NGINX'
server {
  listen 80;
  server_name _;
  return 301 https://$host$request_uri;
}

server {
  listen 443 ssl;
  server_name _;

  ssl_certificate     /etc/ssl/stockwars/stockwars.crt;
  ssl_certificate_key /etc/ssl/stockwars/stockwars.key;

  client_max_body_size 50M;

  location /static/ {
    alias /opt/stockwars/static/;
    expires 7d;
  }

  location /media/ {
    alias /opt/stockwars/media/;
    expires 1d;
  }

  location / {
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;

    # WebSocket support
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";

    proxy_pass http://unix:/run/daphne/stockwars.sock;
  }
}
NGINX

sudo ln -sf /etc/nginx/sites-available/stockwars /etc/nginx/sites-enabled/stockwars
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx

echo "Writing systemd unit for Daphne..."
sudo tee /etc/systemd/system/daphne-stockwars.service >/dev/null <<SERVICE
[Unit]
Description=StockWars Daphne (ASGI)
After=network.target

[Service]
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${BASE_DIR}/.env

ExecStart=${BASE_DIR}/venv/bin/daphne --unix-socket ${SOCK_PATH} stockwars.asgi:application

Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable daphne-stockwars

echo "Provisioning complete."
echo "Next:"
echo "1) Create ${BASE_DIR}/.env"
echo "2) Deploy code from your laptop (scripts/deploy_ec2.sh)"

