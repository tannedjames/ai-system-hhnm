#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="${PROJECT_ROOT}/backups/${TIMESTAMP}"
mkdir -p "$BACKUP_DIR"
DB_USER="${DB_USER:-rag_user}"
DB_NAME="${DB_NAME:-rag_db}"

echo "[1/3] Backup PostgreSQL -> ${BACKUP_DIR}/postgres.sql"
docker compose exec -T postgres pg_dump -U "$DB_USER" "$DB_NAME" > "${BACKUP_DIR}/postgres.sql"

echo "[2/3] Backup MinIO data -> ${BACKUP_DIR}/minio-data.tar.gz"
docker compose exec -T minio sh -lc 'tar -czf - /data' > "${BACKUP_DIR}/minio-data.tar.gz"

echo "[3/3] Backup Qdrant storage -> ${BACKUP_DIR}/qdrant-storage.tar.gz"
docker compose exec -T qdrant sh -lc 'tar -czf - /qdrant/storage' > "${BACKUP_DIR}/qdrant-storage.tar.gz"

echo "Backup completed: ${BACKUP_DIR}"
