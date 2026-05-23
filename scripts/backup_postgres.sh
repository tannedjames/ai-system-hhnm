#!/usr/bin/env bash
set -euo pipefail
mkdir -p backups
TS=$(date +%Y%m%d_%H%M%S)
docker exec rag-postgres pg_dump -U "${POSTGRES_USER:-raguser}" "${POSTGRES_DB:-rag_db}" > "backups/postgres_${TS}.sql"
echo "backup written: backups/postgres_${TS}.sql"
