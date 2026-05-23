#!/usr/bin/env bash
set -euo pipefail
mkdir -p backups/minio
docker cp rag-minio:/data "backups/minio/data_$(date +%Y%m%d_%H%M%S)"
echo "minio backup copied"
