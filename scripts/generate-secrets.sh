#!/bin/bash
# Prints freshly generated, cryptographically random secrets for pasting
# into .env.staging / .env.production — never writes them to a file itself
# (so it can't accidentally create a secrets file with the wrong
# permissions or in the wrong place).
#
# Usage: scripts/generate-secrets.sh
set -euo pipefail

rand() { openssl rand -base64 "$1" | tr -d '\n=+/' | cut -c1-"$2"; }

echo "# Paste these into .env.staging or .env.production — generate FRESH values"
echo "# for each environment, never reuse one between them."
echo
echo "DJANGO_SECRET_KEY=$(rand 64 50)"
echo "DB_PASSWORD=$(rand 32 32)"
echo "REDIS_PASSWORD=$(rand 32 32)"
echo "MONITORING_TOKEN=$(rand 32 40)"
