#!/usr/bin/env bash
set -euo pipefail

# Configure nginx to use a Let's Encrypt certificate and reload nginx.
#
# Usage (on EC2):
#   sudo bash scripts/ec2_enable_letsencrypt_nginx.sh investingwars.com
#
# Notes:
# - Assumes cert already exists (e.g. created via certbot certonly --standalone).
# - Updates /etc/nginx/sites-available/stockwars in-place (creates a .bak backup).
#

DOMAIN="${1:-}"
if [[ -z "${DOMAIN}" ]]; then
  echo "Usage: sudo bash scripts/ec2_enable_letsencrypt_nginx.sh <domain>"
  exit 2
fi

NGINX_SITE="${NGINX_SITE:-/etc/nginx/sites-available/stockwars}"
LE_LIVE_DIR="/etc/letsencrypt/live/${DOMAIN}"
FULLCHAIN="${LE_LIVE_DIR}/fullchain.pem"
PRIVKEY="${LE_LIVE_DIR}/privkey.pem"

if [[ ! -f "${NGINX_SITE}" ]]; then
  echo "ERROR: nginx site config not found: ${NGINX_SITE}"
  exit 3
fi
if [[ ! -f "${FULLCHAIN}" ]]; then
  echo "ERROR: missing Let's Encrypt fullchain: ${FULLCHAIN}"
  echo "Hint: run: sudo certbot certonly --standalone -d ${DOMAIN} -d www.${DOMAIN}"
  exit 4
fi
if [[ ! -f "${PRIVKEY}" ]]; then
  echo "ERROR: missing Let's Encrypt privkey: ${PRIVKEY}"
  exit 5
fi

echo "Updating nginx cert paths in ${NGINX_SITE} ..."
sudo cp -a "${NGINX_SITE}" "${NGINX_SITE}.bak.$(date +%Y%m%d%H%M%S)"

sudo sed -i \
  -e "s|^[[:space:]]*ssl_certificate[[:space:]].*;|  ssl_certificate     ${FULLCHAIN};|g" \
  -e "s|^[[:space:]]*ssl_certificate_key[[:space:]].*;|  ssl_certificate_key ${PRIVKEY};|g" \
  "${NGINX_SITE}"

echo "Testing nginx config..."
sudo nginx -t

echo "Reloading nginx..."
sudo systemctl reload nginx

echo "OK. nginx is now using Let's Encrypt for ${DOMAIN}."

