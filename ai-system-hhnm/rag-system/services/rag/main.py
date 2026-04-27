"""
RAG Pipeline Service — FastAPI Application
=============================================
Orchestrates the full RAG flow: hybrid retrieval → reranking → LLM generation.
Connects to Qdrant for vector search and Ollama for Qwen3.5-4B generation.

Run:
    conda activate rag-pipeline
    uvicorn main:app --host 0.0.0.0 --port 8003
"""

import sys
import os
import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Add the parent 'services' directory to sys.path
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from shared.config import shared_settings as settings

from pipeline import HybridRAGPipeline

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global pipeline reference
# ---------------------------------------------------------------------------
_pipeline: Optional[HybridRAGPipeline] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the RAG pipeline at startup."""
    global _pipeline

    logger.info("Initializing RAG pipeline...")
    _pipeline = HybridRAGPipeline(
        qdrant_host=settings.QDRANT_HOST,
        qdrant_port=settings.QDRANT_PORT,
        collection_name=settings.QDRANT_COLLECTION,
        embedding_service_url=settings.EMBEDDING_SERVICE_URL,
        reranker_service_url=settings.RERANKER_SERVICE_URL,
        ollama_base_url=settings.OLLAMA_BASE_URL,
        ollama_model=settings.OLLAMA_LLM_MODEL,
    )
    logger.info("RAG pipeline ready.")

    yield

    logger.info("Shutting down RAG pipeline...")
    if _pipeline:
        await _pipeline.close()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="RAG Pipeline Service",
    version="1.0.0",
    description="Hybrid Search + Reranking + LLM Generation pipeline",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------
class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, description="User's question")
    retrieval_top_k: int = Field(
        20, ge=1, le=100, description="Number of docs to retrieve"
    )
    rerank_top_k: int = Field(
        5, ge=1, le=50, description="Number of docs after reranking"
    )


class SourceDocument(BaseModel):
    text: str
    metadata: dict
    rerank_score: float


class QueryResponse(BaseModel):
    answer: str
    sources: list[SourceDocument]
    retrieval_count: int
    rerank_count: int


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------
@app.get("/health", tags=["Health"])
async def health():
    """Health check — verifies pipeline and upstream services."""
    status = {"status": "healthy" if _pipeline else "not_initialized"}

    # Check Qdrant connection
    try:
        if _pipeline:
            collections = _pipeline.qdrant.get_collections()
            status["qdrant"] = {
                "status": "healthy",
                "collections": [c.name for c in collections.collections],
            }
    except Exception as e:
        status["qdrant"] = {"status": "unhealthy", "error": str(e)}

    status["config"] = {
        "collection": settings.QDRANT_COLLECTION,
        "llm_model": settings.OLLAMA_LLM_MODEL,
        "embedding_service": settings.EMBEDDING_SERVICE_URL,
        "reranker_service": settings.RERANKER_SERVICE_URL,
    }

    return status


@app.get("/collections", tags=["Qdrant"])
async def list_collections():
    """List all Qdrant collections."""
    if not _pipeline:
        raise HTTPException(status_code=503, detail="Pipeline not initialized")
    try:
        result = _pipeline.qdrant.get_collections()
        return {
            "collections": [
                {"name": c.name, "points_count": c.points_count}
                for c in result.collections
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/query", response_model=QueryResponse, tags=["RAG"])
async def query(req: QueryRequest):
    """
    Full RAG pipeline (non-streaming):
    Embed → Hybrid Search → Rerank → Generate Answer.
    """
    if not _pipeline:
        raise HTTPException(status_code=503, detail="Pipeline not initialized")

    try:
        result = await _pipeline.query(
            question=req.question,
            retrieval_top_k=req.retrieval_top_k,
            rerank_top_k=req.rerank_top_k,
        )
        return QueryResponse(**result)
    except Exception as e:
        logger.error(f"Query failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/query/stream", tags=["RAG"])
async def query_stream(req: QueryRequest):
    """
    Streaming RAG pipeline (SSE):
    Embed → Hybrid Search → Rerank → Stream Answer tokens.

    Returns Server-Sent Events:
    - event: token  → each LLM token
    - event: sources → source documents (sent first)
    - event: done   → stream complete
    """
    if not _pipeline:
        raise HTTPException(status_code=503, detail="Pipeline not initialized")

    try:
        stream, sources = await _pipeline.query_stream(
            question=req.question,
            retrieval_top_k=req.retrieval_top_k,
            rerank_top_k=req.rerank_top_k,
        )
    except Exception as e:
        logger.error(f"Query stream setup failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

    async def event_generator():
        # First, send sources
        yield f"event: sources\ndata: {json.dumps(sources, ensure_ascii=False)}\n\n"

        # Then stream LLM tokens
        async for token in stream:
            yield f"event: token\ndata: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"

        # Signal completion
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
