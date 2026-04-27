# =============================================================================
# RAG System — Start All Services
# =============================================================================
# Launches all 4 microservices + backend in separate terminal windows.
# Run from the rag-system/ directory:
#   .\scripts\start_services.ps1
#
# Prerequisites:
#   1. Docker containers running: docker compose up -d
#   2. Conda environments created: .\scripts\setup_envs.ps1
#   3. Ollama running with model: ollama pull qwen3.5:4b
# =============================================================================

$ErrorActionPreference = "Stop"

$BASE_DIR = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " RAG System — Starting All Services" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""

# ---------------------------------------------------------------------------
# Service 1: Embedding Service (port 8001)
# ---------------------------------------------------------------------------
Write-Host " [1/5] Starting Embedding Service on :8001..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$BASE_DIR\services\embedding'; " +
    "Write-Host 'Embedding Service (rag-embed) — port 8001' -ForegroundColor Green; " +
    "conda activate rag-embed; " +
    "uvicorn main:app --host 0.0.0.0 --port 8001"
)

# ---------------------------------------------------------------------------
# Service 2: Reranker Service (port 8002)
# ---------------------------------------------------------------------------
Write-Host " [2/5] Starting Reranker Service on :8002..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$BASE_DIR\services\reranker'; " +
    "Write-Host 'Reranker Service (rag-rerank) — port 8002' -ForegroundColor Green; " +
    "conda activate rag-rerank; " +
    "uvicorn main:app --host 0.0.0.0 --port 8002"
)

# ---------------------------------------------------------------------------
# Service 3: RAG Pipeline Service (port 8003)
# ---------------------------------------------------------------------------
Write-Host " [3/5] Starting RAG Pipeline Service on :8003..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$BASE_DIR\services\rag'; " +
    "Write-Host 'RAG Pipeline Service (rag-pipeline) — port 8003' -ForegroundColor Green; " +
    "conda activate rag-pipeline; " +
    "uvicorn main:app --host 0.0.0.0 --port 8003"
)

# ---------------------------------------------------------------------------
# Service 4: Ingest Worker (Celery)
# ---------------------------------------------------------------------------
Write-Host " [4/5] Starting Ingest Worker (Celery)..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$BASE_DIR\services\ingest'; " +
    "Write-Host 'Ingest Worker (rag-ingest) — Celery' -ForegroundColor Green; " +
    "conda activate rag-ingest; " +
    "celery -A worker:celery_app worker --loglevel=info --pool=solo"
)

# ---------------------------------------------------------------------------
# Service 5: Backend Gateway (port 8000)
# ---------------------------------------------------------------------------
Write-Host " [5/5] Starting Backend Gateway on :8000..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$BASE_DIR\backend'; " +
    "Write-Host 'Backend Gateway (rag-311) — port 8000' -ForegroundColor Green; " +
    "conda activate rag-311; " +
    "uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
)

Write-Host ""
Write-Host "=============================================" -ForegroundColor Green
Write-Host " All services launched in separate windows!" -ForegroundColor Green
Write-Host "=============================================" -ForegroundColor Green
Write-Host ""
Write-Host " Services:" -ForegroundColor White
Write-Host "   Backend Gateway  → http://localhost:8000" -ForegroundColor DarkGray
Write-Host "   Embedding        → http://localhost:8001" -ForegroundColor DarkGray
Write-Host "   Reranker         → http://localhost:8002" -ForegroundColor DarkGray
Write-Host "   RAG Pipeline     → http://localhost:8003" -ForegroundColor DarkGray
Write-Host "   Ingest Worker    → Celery (background)" -ForegroundColor DarkGray
Write-Host ""
Write-Host " Docs:" -ForegroundColor White
Write-Host "   Gateway API  → http://localhost:8000/docs" -ForegroundColor DarkGray
Write-Host "   Health Check → http://localhost:8000/health" -ForegroundColor DarkGray
Write-Host ""
Write-Host " Usage:" -ForegroundColor White
Write-Host "   Upload: POST http://localhost:8000/api/upload" -ForegroundColor DarkGray
Write-Host "   Query:  POST http://localhost:8000/api/query" -ForegroundColor DarkGray
Write-Host ""
