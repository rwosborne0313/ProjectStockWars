#!/usr/bin/env bash
set -euo pipefail

# Deploy script: laptop -> EC2 using scp + ssh.
# Run from the repo root on your laptop:
#   bash scripts/deploy_ec2.sh
#
# Requirements on laptop: ssh, scp, tar
#
# Configure these before running (or override via env vars):
EC2_HOST="${EC2_HOST:-ec2-52-55-150-41.compute-1.amazonaws.com}"   # swap to Elastic IP / DNS
EC2_USER="${EC2_USER:-ubuntu}"
SSH_KEY="${SSH_KEY:-$HOME/Downloads/stockwarskeypair232026.pem}"
SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=accept-new}"

REMOTE_BASE="/opt/stockwars"
REMOTE_USER="stockwars"
REMOTE_TMP_TGZ="/tmp/stockwars-app.tgz"

if [[ ! -f "${SSH_KEY}" ]]; then
  echo "ERROR: SSH key not found at: ${SSH_KEY}"
  exit 1
fi
chmod 400 "${SSH_KEY}" || true

echo "Clearing any stale known_hosts entry (if present)..."
ssh-keygen -R "${EC2_HOST}" >/dev/null 2>&1 || true

echo "Building deployment tarball..."
TMP_TGZ="$(mktemp -t stockwars-app.XXXXXX.tgz)"
trap 'rm -f "${TMP_TGZ}"' EXIT

tar czf "${TMP_TGZ}" \
  --exclude=".git" \
  --exclude=".venv" \
  --exclude="__pycache__" \
  --exclude="*.pyc" \
  --exclude=".DS_Store" \
  --exclude="media" \
  .

echo "Uploading tarball to ${EC2_USER}@${EC2_HOST}:${REMOTE_TMP_TGZ} ..."
scp ${SSH_OPTS} -i "${SSH_KEY}" "${TMP_TGZ}" "${EC2_USER}@${EC2_HOST}:${REMOTE_TMP_TGZ}"

echo "Extracting + installing + migrating + restarting..."
ssh ${SSH_OPTS} -i "${SSH_KEY}" "${EC2_USER}@${EC2_HOST}" <<EOF
set -euo pipefail

sudo mkdir -p ${REMOTE_BASE}
if [[ ! -f ${REMOTE_BASE}/.env ]]; then
  echo "ERROR: Missing ${REMOTE_BASE}/.env. Run scripts/ec2_02_upload_env.sh first."
  exit 2
fi
sudo rm -rf ${REMOTE_BASE}/app
sudo mkdir -p ${REMOTE_BASE}/app
sudo tar xzf ${REMOTE_TMP_TGZ} -C ${REMOTE_BASE}/app
sudo chown -R ${REMOTE_USER}:${REMOTE_USER} ${REMOTE_BASE}

sudo -u ${REMOTE_USER} bash -lc '
  cd ${REMOTE_BASE}/app
  python3 -m venv ${REMOTE_BASE}/venv
  source ${REMOTE_BASE}/venv/bin/activate
  pip install --upgrade pip
  pip install -r requirements.txt
  python manage.py migrate
  python manage.py collectstatic --noinput
'

sudo systemctl restart daphne-stockwars
sudo systemctl restart nginx

echo "OK. Visit: https://${EC2_HOST}/ (browser warning expected: self-signed cert)"
EOF

