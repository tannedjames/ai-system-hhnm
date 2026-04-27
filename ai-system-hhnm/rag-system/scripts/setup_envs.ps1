# =============================================================================
# RAG System — Environment Setup Script
# =============================================================================
# Creates 4 separate conda environments for the microservices.
# Run this ONCE from the rag-system/ directory:
#   .\scripts\setup_envs.ps1
# =============================================================================

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " RAG System — Environment Setup" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""

$BASE_DIR = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

# Environment definitions: Name → requirements.txt path
$envs = @{
    "rag-embed"    = "$BASE_DIR\services\embedding\requirements.txt"
    "rag-rerank"   = "$BASE_DIR\services\reranker\requirements.txt"
    "rag-pipeline" = "$BASE_DIR\services\rag\requirements.txt"
    "rag-ingest"   = "$BASE_DIR\services\ingest\requirements.txt"
}

foreach ($envName in $envs.Keys) {
    $reqFile = $envs[$envName]
    
    Write-Host ""
    Write-Host "---------------------------------------------" -ForegroundColor Yellow
    Write-Host " Setting up: $envName" -ForegroundColor Yellow
    Write-Host " Requirements: $reqFile" -ForegroundColor DarkGray
    Write-Host "---------------------------------------------" -ForegroundColor Yellow
    
    # Check if environment already exists
    $existingEnvs = conda env list 2>&1
    if ($existingEnvs -match $envName) {
        Write-Host " [SKIP] Environment '$envName' already exists." -ForegroundColor Green
        Write-Host " Installing/updating packages..." -ForegroundColor DarkGray
        conda run -n $envName pip install -r $reqFile --quiet
    } else {
        Write-Host " [CREATE] Creating conda environment '$envName' (Python 3.11.7)..." -ForegroundColor Cyan
        conda create -y -n $envName python=3.11.7
        Write-Host " [INSTALL] Installing packages..." -ForegroundColor Cyan
        conda run -n $envName pip install -r $reqFile
    }
    
    Write-Host " [DONE] $envName ready!" -ForegroundColor Green
}

Write-Host ""
Write-Host "=============================================" -ForegroundColor Green
Write-Host " All environments created successfully!" -ForegroundColor Green
Write-Host "=============================================" -ForegroundColor Green
Write-Host ""
Write-Host "Environments:" -ForegroundColor White
Write-Host "  rag-embed     → Embedding Service   (port 8001)" -ForegroundColor DarkGray
Write-Host "  rag-rerank    → Reranker Service     (port 8002)" -ForegroundColor DarkGray
Write-Host "  rag-pipeline  → RAG Pipeline Service (port 8003)" -ForegroundColor DarkGray
Write-Host "  rag-ingest    → Ingest Celery Worker" -ForegroundColor DarkGray
Write-Host ""
Write-Host "Next: Run '.\scripts\start_services.ps1' to launch all services." -ForegroundColor Yellow
Write-Host ""
