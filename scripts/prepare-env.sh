#!/bin/sh
set -eu

cd "$(dirname "$0")/.."
if [ -e .env ]; then
  echo "An .env file already exists; leaving it unchanged."
  exit 0
fi
if ! command -v openssl >/dev/null 2>&1; then
  echo "OpenSSL is required to create safe random passwords. Install openssl and try again." >&2
  exit 1
fi

POSTGRES_PASSWORD=$(openssl rand -hex 32)
GRAFANA_ADMIN_PASSWORD=$(openssl rand -hex 32)
JWT_SECRET_KEY=$(openssl rand -hex 32)
INITIAL_ADMIN_SETUP_TOKEN=$(openssl rand -hex 32)
temporary=$(mktemp)
trap 'rm -f "$temporary"' EXIT HUP INT TERM

while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    POSTGRES_PASSWORD=*) line="POSTGRES_PASSWORD=$POSTGRES_PASSWORD" ;;
    GRAFANA_ADMIN_PASSWORD=*) line="GRAFANA_ADMIN_PASSWORD=$GRAFANA_ADMIN_PASSWORD" ;;
    JWT_SECRET_KEY=*) line="JWT_SECRET_KEY=$JWT_SECRET_KEY" ;;
    INITIAL_ADMIN_SETUP_TOKEN=*) line="INITIAL_ADMIN_SETUP_TOKEN=$INITIAL_ADMIN_SETUP_TOKEN" ;;
  esac
  printf '%s\n' "$line" >> "$temporary"
done < .env.example
chmod 600 "$temporary"
mv "$temporary" .env
trap - EXIT HUP INT TERM
echo "Created .env with four different random secrets. Keep this file private; it is excluded from Git."
