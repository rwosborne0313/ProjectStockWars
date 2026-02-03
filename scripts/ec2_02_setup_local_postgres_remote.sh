#!/usr/bin/env bash
set -euo pipefail

# Runs on your laptop.
# Uploads and runs the remote local-Postgres + env setup script on EC2.

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <path_to_pem> <user@host_or_elastic_ip>"
  echo "Example: $0 ~/Downloads/stockwarskeypair232026.pem ubuntu@203.0.113.10"
  exit 2
fi

PEM_PATH="$1"
REMOTE="$2"

SCRIPT_LOCAL="scripts/ec2_setup_local_postgres_and_env.sh"
SCRIPT_REMOTE="/tmp/ec2_setup_local_postgres_and_env.sh"

if [[ ! -f "${PEM_PATH}" ]]; then
  echo "PEM not found: ${PEM_PATH}"
  exit 2
fi

if [[ ! -f "${SCRIPT_LOCAL}" ]]; then
  echo "Missing script in repo: ${SCRIPT_LOCAL}"
  exit 2
fi

chmod 400 "${PEM_PATH}" || true

HOST="${REMOTE#*@}"
ssh-keygen -R "${HOST}" >/dev/null 2>&1 || true

SSH_OPTS=(
  -i "${PEM_PATH}"
  -o StrictHostKeyChecking=accept-new
  -o ServerAliveInterval=15
  -o ServerAliveCountMax=4
)

echo "Uploading local Postgres + env setup script..."
scp "${SSH_OPTS[@]}" "${SCRIPT_LOCAL}" "${REMOTE}:${SCRIPT_REMOTE}"

echo "Running remote setup (may take a few minutes)..."
ssh "${SSH_OPTS[@]}" "${REMOTE}" "chmod +x ${SCRIPT_REMOTE} && sudo -H ${SCRIPT_REMOTE}"

echo "Done."

