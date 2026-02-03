#!/usr/bin/env bash

# Copy-paste into /opt/stockwars/.env on EC2 (edit values).
# This file is NOT sourced by the app locally; it's just a template.

cat <<'ENV'
DJANGO_SECRET_KEY=change-me
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=ec2-52-55-150-41.compute-1.amazonaws.com

POSTGRES_DB=stockwars
POSTGRES_USER=stockwars
POSTGRES_PASSWORD=change-me
POSTGRES_HOST=change-me
POSTGRES_PORT=5432
POSTGRES_CONN_MAX_AGE=60

TWELVE_DATA_API_KEY=change-me
MAX_QUOTE_AGE_SECONDS=300
ENV

