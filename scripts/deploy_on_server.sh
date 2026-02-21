#!/usr/bin/env bash
set -euo pipefail

# Deploy script: run ON the EC2 instance (git-based deploy).
#
# Assumptions:
# - Repo is checked out at /opt/stockwars/app and is a git repository
# - /opt/stockwars/.env exists (systemd uses it)
# - `daphne-stockwars` + nginx were provisioned via scripts/ec2_provision_server.sh
#
# Usage (recommended from ubuntu user):
#   sudo bash scripts/deploy_on_server.sh
#

APP_USER="${APP_USER:-stockwars}"
BASE_DIR="${BASE_DIR:-/opt/stockwars}"
APP_DIR="${APP_DIR:-/opt/stockwars/app}"
VENV_DIR="${VENV_DIR:-/opt/stockwars/venv}"

if [[ ! -d "${APP_DIR}" ]]; then
  echo "ERROR: APP_DIR not found: ${APP_DIR}"
  exit 2
fi
if [[ ! -f "${BASE_DIR}/.env" ]]; then
  echo "ERROR: Missing ${BASE_DIR}/.env"
  exit 3
fi

echo "Updating code (git pull)..."
sudo -u "${APP_USER}" bash -lc "cd '${APP_DIR}' && git pull --ff-only"

echo "Ensuring virtualenv exists..."
if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  sudo -u "${APP_USER}" bash -lc "python3 -m venv '${VENV_DIR}'"
fi

echo "Installing Python dependencies..."
sudo -u "${APP_USER}" bash -lc "source '${VENV_DIR}/bin/activate' && pip install --upgrade pip setuptools wheel && pip install -r '${APP_DIR}/requirements.txt'"

echo "Running migrations + collectstatic..."
sudo -u "${APP_USER}" bash -lc "cd '${APP_DIR}' && set -a && source '${BASE_DIR}/.env' && set +a && source '${VENV_DIR}/bin/activate' && python manage.py migrate && python manage.py collectstatic --noinput"

echo "Restarting services..."
sudo systemctl restart daphne-stockwars
sudo systemctl restart nginx

echo "Deploy complete."

