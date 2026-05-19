#!/usr/bin/env bash
set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

API_URL="http://localhost:8000"
MAX_WAIT=300  # seconds
TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo -e "${YELLOW}=== RAG System Test ===${NC}"
cd "$TEST_DIR"

# 1. Ensure .env exists
if [ ! -f .env ]; then
    echo -e "${YELLOW}Creating .env from .env.example${NC}"
    if [ -f .env.example ]; then
        cp .env.example .env
    else
        cat > .env << 'EOF'
ANTHROPIC_API_KEY=dummy-key-for-test
ANTHROPIC_BASE_URL=https://chat.trollllm.xyz
CLAUDE_MODEL=claude-sonnet-4-6
POSTGRES_USER=raguser
POSTGRES_PASSWORD=ragpass
POSTGRES_DB=rag_db
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
EOF
    fi
fi

# 2. Start services
echo -e "${YELLOW}Building & starting Docker Compose...${NC}"
docker compose down -v >/dev/null 2>&1 || true
docker compose up --build -d

# 3. Wait for backend health
echo -e "${YELLOW}Waiting for backend health (max ${MAX_WAIT}s)...${NC}"
SECONDS=0
until curl -sf "${API_URL}/health" >/dev/null 2>&1; do
    if [ "$SECONDS" -ge "$MAX_WAIT" ]; then
        echo -e "${RED}Backend did not become healthy in time${NC}"
        docker compose logs backend --tail 50
        docker compose down -v
        exit 1
    fi
    sleep 2
    printf "."
done
printf "\n"
echo -e "${GREEN}Backend is healthy${NC}"

# 4. Create test document
echo -e "${YELLOW}Creating test document...${NC}"
cat > /tmp/test_doc.txt << 'EOF'
The quick brown fox jumps over the lazy dog.
This document is about machine learning and natural language processing.
RAG systems combine retrieval with generation to produce accurate answers.
EOF

# 5. Upload
echo -e "${YELLOW}Uploading test document...${NC}"
UPLOAD_RES=$(curl -s -X POST "${API_URL}/api/documents/upload" \
    -F "file=@/tmp/test_doc.txt")
echo "$UPLOAD_RES" | python3 -m json.tool || echo "$UPLOAD_RES"

# 6. Poll for completion
echo -e "${YELLOW}Polling for ingest completion (max 60s)...${NC}"
SECONDS=0
while true; do
    LIST_RES=$(curl -s "${API_URL}/api/documents")
    STATUS=$(echo "$LIST_RES" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['documents'][0]['status'] if d.get('documents') else 'unknown')" 2>/dev/null || echo "unknown")
    if [ "$STATUS" = "completed" ]; then
        echo -e "${GREEN}Ingest completed${NC}"
        break
    fi
    if [ "$STATUS" = "failed" ]; then
        echo -e "${RED}Ingest failed${NC}"
        echo "$LIST_RES" | python3 -m json.tool || true
        docker compose logs worker --tail 50
        docker compose down -v
        exit 1
    fi
    if [ "$SECONDS" -ge 60 ]; then
        echo -e "${YELLOW}Polling timeout — proceeding anyway${NC}"
        break
    fi
    sleep 2
    printf "."
done
printf "\n"

# 7. List documents
echo -e "${YELLOW}Documents list:${NC}"
curl -s "${API_URL}/api/documents" | python3 -m json.tool || true

# 8. Search
echo -e "${YELLOW}Running semantic search...${NC}"
curl -s -X POST "${API_URL}/api/search" \
    -H "Content-Type: application/json" \
    -d '{"query":"machine learning","top_k":3}' | python3 -m json.tool || true

# 9. Ask (if API key is real; skip on dummy key)
if grep -q "ANTHROPIC_API_KEY=dummy-key-for-test" .env 2>/dev/null; then
    echo -e "${YELLOW}Skipping /api/ask test (dummy API key)${NC}"
else
    echo -e "${YELLOW}Running RAG ask...${NC}"
    curl -s -X POST "${API_URL}/api/ask" \
        -H "Content-Type: application/json" \
        -d '{"question":"What is this document about?","top_k":3}' | python3 -m json.tool || true
fi

# 10. Cleanup
echo -e "${YELLOW}Cleaning up...${NC}"
docker compose down -v
echo -e "${GREEN}=== Test Complete ===${NC}"
