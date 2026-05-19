#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo -e "${YELLOW}=== RAG System Startup ===${NC}"

# 1. Check Docker
if ! command -v docker &> /dev/null; then
    echo -e "${RED}Docker not found. Install Docker first.${NC}"
    exit 1
fi
if ! command -v docker compose &> /dev/null; then
    echo -e "${RED}Docker Compose not found. Install Docker Compose first.${NC}"
    exit 1
fi

# 2. Ensure .env exists
if [ ! -f .env ]; then
    echo -e "${YELLOW}Creating .env from .env.example${NC}"
    if [ -f .env.example ]; then
        cp .env.example .env
    else
        cat > .env << 'EOF'
ANTHROPIC_API_KEY=your-api-key-here
ANTHROPIC_BASE_URL=https://chat.trollllm.xyz
CLAUDE_MODEL=claude-sonnet-4-6
POSTGRES_USER=raguser
POSTGRES_PASSWORD=ragpass
POSTGRES_DB=rag_db
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
EOF
    fi
    echo -e "${YELLOW}Edit .env and set your ANTHROPIC_API_KEY before using /api/ask${NC}"
fi

# 3. Start services
echo -e "${YELLOW}Building & starting services...${NC}"
docker compose up --build -d

# 4. Wait for backend
echo -e "${YELLOW}Waiting for backend to be ready...${NC}"
SECONDS=0
MAX_WAIT=120
until curl -sf http://localhost:8000/health >/dev/null 2>&1; do
    if [ "$SECONDS" -ge "$MAX_WAIT" ]; then
        echo -e "${RED}Backend not ready after ${MAX_WAIT}s. Check logs:${NC}"
        echo "  docker compose logs backend --tail 30"
        exit 1
    fi
    sleep 2
    printf "."
done
printf "\n"

# 5. Show URLs
echo ""
echo -e "${GREEN}=== Services Ready ===${NC}"
echo -e "${BLUE}User UI:${NC}     http://localhost"
echo -e "${BLUE}Admin UI:${NC}    http://localhost/admin"
echo -e "${BLUE}API Docs:${NC}    http://localhost/docs"
echo -e "${BLUE}Health:${NC}      http://localhost/health"
echo ""
echo -e "${YELLOW}Useful commands:${NC}"
echo "  docker compose logs -f backend    # watch backend logs"
echo "  docker compose logs -f worker     # watch worker logs"
echo "  docker compose down               # stop everything"
echo ""
echo -e "${GREEN}Press Ctrl+C to stop watching logs, services keep running.${NC}"
echo ""

# 6. Stream logs
docker compose logs -f
