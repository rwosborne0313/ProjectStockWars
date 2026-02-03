#!/usr/bin/env bash
set -euo pipefail

# Step 2 (Laptop): Upload /opt/stockwars/.env to EC2 securely.
#
# This script prompts for the values you need (so you don't paste secrets into shell history),
# writes a temporary .env file locally, SCPs it to the instance, then moves it into place with
# correct ownership + permissions.
#
# IMPORTANT:
# - Use this script when your PostgreSQL database is NOT managed locally on the EC2 box
#   (e.g., AWS RDS or a separate DB host).
# - If you're running PostgreSQL on the SAME EC2 instance, do NOT use this script.
#   Instead run:
#     bash scripts/ec2_02_setup_local_postgres_remote.sh "<pem_path>" ubuntu@<elastic-ip>
#
# Run from repo root:
#   bash scripts/ec2_02_upload_env.sh
#
# Optional overrides:
#   EC2_HOST=... SSH_KEY=... bash scripts/ec2_02_upload_env.sh

EC2_HOST="${EC2_HOST:-ec2-52-55-150-41.compute-1.amazonaws.com}"
EC2_USER="${EC2_USER:-ubuntu}"
SSH_KEY="${SSH_KEY:-$HOME/Downloads/stockwarskeypair232026.pem}"
SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=accept-new}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -f "${SSH_KEY}" ]]; then
  echo "ERROR: SSH key not found at: ${SSH_KEY}"
  exit 1
fi
chmod 400 "${SSH_KEY}" || true

echo "Clearing any stale known_hosts entry (if present)..."
ssh-keygen -R "${EC2_HOST}" >/dev/null 2>&1 || true

echo "Creating EC2 .env (values will be prompted)."

read -r -p "DJANGO_ALLOWED_HOSTS [${EC2_HOST}]: " DJANGO_ALLOWED_HOSTS
DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS:-$EC2_HOST}"

read -r -p "POSTGRES_HOST (RDS endpoint / DB host): " POSTGRES_HOST
read -r -p "POSTGRES_DB [stockwars]: " POSTGRES_DB
POSTGRES_DB="${POSTGRES_DB:-stockwars}"
read -r -p "POSTGRES_USER [stockwars]: " POSTGRES_USER
POSTGRES_USER="${POSTGRES_USER:-stockwars}"
read -r -s -p "POSTGRES_PASSWORD: " POSTGRES_PASSWORD
echo

read -r -p "TWELVE_DATA_API_KEY [optional]: " TWELVE_DATA_API_KEY

# Generate a strong secret key without requiring python.
DJANGO_SECRET_KEY="$(openssl rand -base64 48 | tr -d '\n' | tr '+/' '-_')"

TMP_ENV="$(mktemp -t stockwars-ec2.env.XXXXXX)"
trap 'rm -f "${TMP_ENV}"' EXIT

cat > "${TMP_ENV}" <<ENV
DJANGO_SECRET_KEY=${DJANGO_SECRET_KEY}
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=${DJANGO_ALLOWED_HOSTS}

POSTGRES_DB=${POSTGRES_DB}
POSTGRES_USER=${POSTGRES_USER}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
POSTGRES_HOST=${POSTGRES_HOST}
POSTGRES_PORT=5432
POSTGRES_CONN_MAX_AGE=60

TWELVE_DATA_API_KEY=${TWELVE_DATA_API_KEY}
MAX_QUOTE_AGE_SECONDS=300
ENV

echo "Uploading .env to EC2..."
scp ${SSH_OPTS} -i "${SSH_KEY}" "${TMP_ENV}" "${EC2_USER}@${EC2_HOST}:/tmp/stockwars.env"

echo "Installing .env to /opt/stockwars/.env..."
ssh ${SSH_OPTS} -i "${SSH_KEY}" "${EC2_USER}@${EC2_HOST}" <<'EOF'
set -euo pipefail
sudo mkdir -p /opt/stockwars
sudo mv /tmp/stockwars.env /opt/stockwars/.env
sudo chown stockwars:stockwars /opt/stockwars/.env
sudo chmod 600 /opt/stockwars/.env
EOF

echo "Environment file installed."
echo "Next: bash scripts/deploy_ec2.sh"

