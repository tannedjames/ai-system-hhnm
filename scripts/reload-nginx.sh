#!/usr/bin/env sh
set -eu
if docker ps --format '{{.Names}}' | grep -q '^rag-nginx$'; then
  docker kill -s HUP rag-nginx >/dev/null 2>&1 || true
  echo "[certbot] nginx reloaded"
else
  echo "[certbot] nginx container not found"
fi
