#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
"$SCRIPT_DIR/backup_postgres.sh"
"$SCRIPT_DIR/backup_qdrant.sh"
"$SCRIPT_DIR/backup_minio.sh"
