"""
Reranker Service — FastAPI Application
========================================
Serves Qwen3-Reranker-0.6B as a cross-encoder reranker via REST API.
Uses sentence-transformers CrossEncoder (official usage from model card).

Run:
    conda activate rag-rerank
    uvicorn main:app --host 0.0.0.0 --port 8002
"""

import sys
import os
import logging
from contextlib import asynccontextmanager
from typing import Optional

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Add the parent 'services' directory to sys.path so we can import shared.config
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from shared.config import shared_settings as settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global model reference
# ---------------------------------------------------------------------------
_reranker = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the CrossEncoder model at startup."""
    global _reranker

    logger.info(f"Loading reranker: {settings.RERANKER_MODEL_NAME}...")
    from sentence_transformers import CrossEncoder

    device = "cuda" if torch.cuda.is_available() else "cpu"
    _reranker = CrossEncoder(
        settings.RERANKER_MODEL_NAME,
        device=device,
    )
    logger.info(f"Reranker loaded on {device}.")

    yield

    logger.info("Reranker service shutting down.")
    _reranker = None


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="RAG Reranker Service",
    version="1.0.0",
    description="Qwen3-Reranker-0.6B cross-encoder reranking service",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------
class RerankRequest(BaseModel):
    query: str = Field(..., description="The search query")
    documents: list[str] = Field(..., min_length=1, description="Documents to rerank")
    top_k: int = Field(5, ge=1, le=100, description="Number of top results to return")
    instruction: Optional[str] = Field(
        None,
        description="Custom task instruction (default: web search retrieval)",
    )


class RankedDocument(BaseModel):
    index: int = Field(..., description="Original index in the input list")
    score: float = Field(..., description="Relevance score (0-1 probability)")
    text: str = Field(..., description="The document text")


class RerankResponse(BaseModel):
    results: list[RankedDocument]
    model: str
    query: str


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------
@app.get("/health", tags=["Health"])
async def health():
    """Health check."""
    return {
        "status": "healthy" if _reranker else "loading",
        "model": settings.RERANKER_MODEL_NAME,
    }


@app.post("/rerank", response_model=RerankResponse, tags=["Reranking"])
async def rerank(req: RerankRequest):
    """
    Rerank documents by relevance to the query.
    Uses Qwen3-Reranker-0.6B CrossEncoder with sigmoid activation
    to produce 0-1 probability scores.
    """
    if _reranker is None:
        raise HTTPException(status_code=503, detail="Reranker model not loaded")

    # Build query-document pairs
    pairs = [(req.query, doc) for doc in req.documents]

    # Score with sigmoid for 0-1 probabilities
    scores = _reranker.predict(
        pairs,
        activation_fn=torch.nn.Sigmoid(),
    )

    # Build scored results
    scored = []
    for i, (score, doc) in enumerate(zip(scores, req.documents)):
        scored.append(
            RankedDocument(
                index=i,
                score=float(score),
                text=doc,
            )
        )

    # Sort by score descending, take top_k
    scored.sort(key=lambda x: x.score, reverse=True)
    top_results = scored[: req.top_k]

    return RerankResponse(
        results=top_results,
        model=settings.RERANKER_MODEL_NAME,
        query=req.query,
    )
