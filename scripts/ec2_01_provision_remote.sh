#!/usr/bin/env bash
set -euo pipefail

# Step 1 (Laptop): Provision EC2 (Ubuntu 22.04) remotely.
# This script:
# - ensures your .pem has safe permissions
# - uploads scripts/ec2_provision_server.sh to the instance
# - runs it on the instance (installs nginx + generates 180-day self-signed cert + systemd unit)
#
# Run from repo root:
#   bash scripts/ec2_01_provision_remote.sh
#
# Optional overrides:
#   EC2_HOST=... SSH_KEY=... CERT_CN=... bash scripts/ec2_01_provision_remote.sh

EC2_HOST="${EC2_HOST:-ec2-52-55-150-41.compute-1.amazonaws.com}"
EC2_USER="${EC2_USER:-ubuntu}"
SSH_KEY="${SSH_KEY:-$HOME/Downloads/stockwarskeypair232026.pem}"
CERT_CN="${CERT_CN:-$EC2_HOST}" # set to Elastic IP/DNS if desired
SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=accept-new}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROVISION_SCRIPT_LOCAL="${REPO_ROOT}/scripts/ec2_provision_server.sh"

if [[ ! -f "${SSH_KEY}" ]]; then
  echo "ERROR: SSH key not found at: ${SSH_KEY}"
  exit 1
fi
chmod 400 "${SSH_KEY}" || true

if [[ ! -f "${PROVISION_SCRIPT_LOCAL}" ]]; then
  echo "ERROR: Provision script missing: ${PROVISION_SCRIPT_LOCAL}"
  exit 1
fi

echo "Clearing any stale known_hosts entry (if present)..."
ssh-keygen -R "${EC2_HOST}" >/dev/null 2>&1 || true

echo "Uploading provision script..."
scp ${SSH_OPTS} -i "${SSH_KEY}" "${PROVISION_SCRIPT_LOCAL}" "${EC2_USER}@${EC2_HOST}:/tmp/ec2_provision_server.sh"

echo "Running provision script on EC2 (CERT_CN=${CERT_CN})..."
ssh ${SSH_OPTS} -i "${SSH_KEY}" "${EC2_USER}@${EC2_HOST}" "export STOCKWARS_CERT_CN='${CERT_CN}' && bash /tmp/ec2_provision_server.sh"

echo "Provisioning done."
echo "Next: bash scripts/ec2_02_upload_env.sh"

