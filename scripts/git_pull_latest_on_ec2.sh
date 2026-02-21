#!/usr/bin/env bash
set -euo pipefail

# Pull the latest code on the EC2 instance (git-based workflow).
#
# Usage:
#   sudo bash scripts/git_pull_latest_on_ec2.sh
#

APP_DIR="${APP_DIR:-/opt/stockwars/app}"
APP_USER="${APP_USER:-stockwars}"

if [[ ! -d "${APP_DIR}" ]]; then
  echo "ERROR: APP_DIR not found: ${APP_DIR}"
  exit 2
fi
if [[ ! -d "${APP_DIR}/.git" ]]; then
  echo "ERROR: ${APP_DIR} is not a git repository (missing .git)"
  exit 3
fi

echo "Pulling latest repo changes in ${APP_DIR} ..."
sudo -u "${APP_USER}" bash -lc "cd '${APP_DIR}' && git status --porcelain && git pull --ff-only"
echo "OK."

