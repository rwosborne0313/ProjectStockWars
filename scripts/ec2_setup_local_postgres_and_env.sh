#!/usr/bin/env bash
set -euo pipefail

# Runs ON the EC2 instance (one-time).
# - Installs PostgreSQL server locally
# - Creates DB/user for the app
# - Writes /opt/stockwars/.env configured for localhost Postgres
#
# This script is intentionally non-interactive.
#
# You can override:
#   APP_DB, APP_USER, APP_PASSWORD, DJANGO_ALLOWED_HOSTS, TWELVE_DATA_API_KEY

APP_USER_NAME="stockwars"
BASE_DIR="/opt/stockwars"

APP_DB="${APP_DB:-stockwars}"
APP_USER="${APP_USER:-stockwars}"
APP_PASSWORD="${APP_PASSWORD:-}"
DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS:-}"
TWELVE_DATA_API_KEY="${TWELVE_DATA_API_KEY:-}"

if [[ -z "${APP_PASSWORD}" ]]; then
  # Generate a strong password; print at end.
  APP_PASSWORD="$(openssl rand -base64 36 | tr -d '\n' | tr '+/' '-_')"
fi

if [[ -z "${DJANGO_ALLOWED_HOSTS}" ]]; then
  DJANGO_ALLOWED_HOSTS="$(curl -s http://169.254.169.254/latest/meta-data/public-hostname || true)"
fi
DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS:-localhost}"

DJANGO_SECRET_KEY="$(openssl rand -base64 48 | tr -d '\n' | tr '+/' '-_')"

echo "Installing PostgreSQL server..."
sudo apt-get update
sudo apt-get install -y postgresql postgresql-contrib

echo "Ensuring PostgreSQL is running..."
sudo systemctl enable postgresql
sudo systemctl restart postgresql

echo "Creating DB/user (idempotent)..."
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${APP_USER}') THEN
    CREATE ROLE ${APP_USER} LOGIN PASSWORD '${APP_PASSWORD}';
  END IF;
END
\$\$;

DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_database WHERE datname = '${APP_DB}') THEN
    CREATE DATABASE ${APP_DB} OWNER ${APP_USER};
  END IF;
END
\$\$;
SQL

echo "Writing ${BASE_DIR}/.env ..."
sudo mkdir -p "${BASE_DIR}"
sudo tee "${BASE_DIR}/.env" >/dev/null <<ENV
DJANGO_SECRET_KEY=${DJANGO_SECRET_KEY}
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=${DJANGO_ALLOWED_HOSTS}

POSTGRES_DB=${APP_DB}
POSTGRES_USER=${APP_USER}
POSTGRES_PASSWORD=${APP_PASSWORD}
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_CONN_MAX_AGE=60

TWELVE_DATA_API_KEY=${TWELVE_DATA_API_KEY}
MAX_QUOTE_AGE_SECONDS=300
ENV

sudo chown ${APP_USER_NAME}:${APP_USER_NAME} "${BASE_DIR}/.env"
sudo chmod 600 "${BASE_DIR}/.env"

echo
echo "Local Postgres configured."
echo "App DB: ${APP_DB}"
echo "App user: ${APP_USER}"
echo "App password (stored in ${BASE_DIR}/.env):"
echo "${APP_PASSWORD}"

