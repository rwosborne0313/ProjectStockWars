#!/usr/bin/env bash
set -euo pipefail

# One-time provisioning script for Ubuntu 22.04 EC2.
# Run on the EC2 instance as ubuntu:
#   bash scripts/ec2_provision_server.sh
#
# What it does:
# - installs OS deps (nginx, python build deps, local postgres)
# - creates a dedicated system user (stockwars) and directories under /opt/stockwars
# - creates a self-signed TLS cert valid for 180 days (~6 months)
# - configures nginx (HTTPS + WebSockets) proxying to Daphne via unix socket
# - creates a systemd service for Daphne (ASGI)
#
# IMPORTANT:
# - You must create /opt/stockwars/.env (DB creds, secret key, etc).
# - If /opt/stockwars/.env exists at provision time, this script will also create
#   the Postgres role/database defined there (idempotent).

APP_USER="stockwars"
BASE_DIR="/opt/stockwars"
APP_DIR="/opt/stockwars/app"
SOCK_DIR="/run/daphne"
SOCK_PATH="${SOCK_DIR}/stockwars.sock"

_imds_get() {
  # Query IMDSv2 (falls back cleanly if unavailable).
  local path="$1"
  local token
  token="$(curl -sS -m 2 -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" || true)"
  if [[ -n "${token}" ]]; then
    curl -sS -m 2 -H "X-aws-ec2-metadata-token: ${token}" "http://169.254.169.254/latest/meta-data/${path}" || true
  else
    curl -sS -m 2 "http://169.254.169.254/latest/meta-data/${path}" || true
  fi
}

_sanitize_cn() {
  # Keep CN within 64 chars and remove problematic characters.
  # Allows letters, digits, dots, dashes, underscores (covers hostnames + IPv4).
  local raw="$1"
  raw="${raw//$'\n'/}"
  raw="${raw//$'\r'/}"
  raw="$(echo -n "${raw}" | tr -cd 'A-Za-z0-9._-')"
  echo -n "${raw:0:64}"
}

# Cert CN: prefer STOCKWARS_CERT_CN, else EC2 public-hostname, else localhost.
CERT_CN="$(_sanitize_cn "${STOCKWARS_CERT_CN:-}")"
if [[ -z "${CERT_CN}" ]]; then
  CERT_CN="$(_sanitize_cn "$(_imds_get "public-hostname")")"
fi
CERT_CN="${CERT_CN:-localhost}"

echo "Provisioning packages..."
sudo apt-get update
sudo apt-get install -y \
  python3-venv python3-dev build-essential pkg-config \
  nginx postgresql postgresql-client libpq-dev \
  openssl curl

echo "Ensuring Postgres is running..."
sudo systemctl enable --now postgresql >/dev/null 2>&1 || true

echo "Creating local Postgres role/db (if env present)..."
if [[ -f "${BASE_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${BASE_DIR}/.env"
  set +a

  POSTGRES_DB="${POSTGRES_DB:-stockwars}"
  POSTGRES_USER="${POSTGRES_USER:-stockwars}"
  POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"

  if [[ -z "${POSTGRES_PASSWORD}" ]]; then
    echo "WARN: ${BASE_DIR}/.env is missing POSTGRES_PASSWORD; skipping DB/user creation."
  else
    sudo -u postgres psql -v ON_ERROR_STOP=1 -tc "SELECT 1 FROM pg_roles WHERE rolname='${POSTGRES_USER}'" | grep -q 1 \
      || sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE ROLE ${POSTGRES_USER} LOGIN PASSWORD '${POSTGRES_PASSWORD}';"

    sudo -u postgres psql -v ON_ERROR_STOP=1 -tc "SELECT 1 FROM pg_database WHERE datname='${POSTGRES_DB}'" | grep -q 1 \
      || sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE DATABASE ${POSTGRES_DB} OWNER ${POSTGRES_USER};"
  fi
else
  echo "INFO: ${BASE_DIR}/.env not found yet; skipping DB/user creation for now."
  echo "      After you create it, you can rerun this script or create the DB manually."
fi

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
echo "2) Deploy code to ${APP_DIR} (git clone / git pull)"
echo "3) Create venv + migrate + collectstatic + restart services (see scripts/deploy_on_server.sh)"

