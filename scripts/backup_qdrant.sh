#!/usr/bin/env bash
set -euo pipefail
mkdir -p backups/qdrant
docker cp rag-qdrant:/qdrant/storage "backups/qdrant/storage_$(date +%Y%m%d_%H%M%S)"
echo "qdrant backup copied"
