#!/usr/bin/env bash
set -euo pipefail
mkdir -p /project/backups
while true; do
  TS=$(date +%Y%m%d_%H%M%S)
  /project/scripts/backup_postgres.sh || true
  /project/scripts/backup_qdrant.sh || true
  /project/scripts/backup_minio.sh || true
  echo "[$TS] backups done"
  sleep ${BACKUP_INTERVAL_SECONDS:-86400}
done
