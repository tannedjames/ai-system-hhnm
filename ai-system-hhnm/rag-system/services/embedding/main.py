"""
Embedding Service — FastAPI Application
=========================================
Serves dense embeddings (Qwen3-Embedding-0.6B via sentence-transformers)
and sparse embeddings (BM25 via fastembed) behind a REST API.

Run:
    conda activate rag-embed
    uvicorn main:app --host 0.0.0.0 --port 8001
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
# Global model references (loaded once at startup)
# ---------------------------------------------------------------------------
_dense_model = None
_sparse_model = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load models at startup, release at shutdown."""
    global _dense_model, _sparse_model

    logger.info("Loading Qwen3-Embedding-0.6B dense model...")
    from sentence_transformers import SentenceTransformer

    device = "cuda" if torch.cuda.is_available() else "cpu"
    _dense_model = SentenceTransformer(
        settings.EMBEDDING_MODEL_NAME,
        device=device,
    )
    logger.info(
        f"Dense model loaded on {device} — dim={_dense_model.get_sentence_embedding_dimension()}"
    )

    logger.info("Loading FastEmbed BM25 sparse model...")
    from fastembed import SparseTextEmbedding

    _sparse_model = SparseTextEmbedding(model_name=settings.SPARSE_MODEL_NAME)
    logger.info("Sparse model loaded.")

    yield

    logger.info("Embedding service shutting down.")
    _dense_model = None
    _sparse_model = None


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="RAG Embedding Service",
    version="1.0.0",
    description="Dense (Qwen3-Embedding-0.6B) + Sparse (BM25) embedding service",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------
class EmbedRequest(BaseModel):
    texts: list[str] = Field(..., min_length=1, description="Texts to embed")
    instruction: Optional[str] = Field(
        None,
        description="Instruction prefix for queries (improves retrieval 1-5%)",
    )


class DenseEmbedResponse(BaseModel):
    embeddings: list[list[float]]
    dim: int
    model: str


class SparseVector(BaseModel):
    indices: list[int]
    values: list[float]


class SparseEmbedResponse(BaseModel):
    embeddings: list[SparseVector]
    model: str


class HybridEmbedResponse(BaseModel):
    dense: list[list[float]]
    sparse: list[SparseVector]
    dense_dim: int
    dense_model: str
    sparse_model: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _format_texts_with_instruction(
    texts: list[str], instruction: Optional[str]
) -> list[str]:
    """Prepend instruction to texts for query-side embedding (Qwen3 format)."""
    if instruction:
        return [f"Instruct: {instruction}\nQuery: {t}" for t in texts]
    return texts


def _embed_dense(texts: list[str], instruction: Optional[str] = None) -> list[list[float]]:
    """Generate dense embeddings using Qwen3-Embedding-0.6B."""
    if _dense_model is None:
        raise HTTPException(status_code=503, detail="Dense model not loaded")

    formatted = _format_texts_with_instruction(texts, instruction)
    # Use prompt_name="query" when instruction is provided
    if instruction:
        embeddings = _dense_model.encode(
            texts, prompt_name="query", normalize_embeddings=True
        )
    else:
        embeddings = _dense_model.encode(
            formatted, normalize_embeddings=True
        )
    return embeddings.tolist()


def _embed_sparse(texts: list[str]) -> list[SparseVector]:
    """Generate BM25 sparse embeddings using FastEmbed."""
    if _sparse_model is None:
        raise HTTPException(status_code=503, detail="Sparse model not loaded")

    results = list(_sparse_model.embed(texts))
    sparse_vectors = []
    for sparse in results:
        sparse_vectors.append(
            SparseVector(
                indices=sparse.indices.tolist(),
                values=sparse.values.tolist(),
            )
        )
    return sparse_vectors


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------
@app.get("/health", tags=["Health"])
async def health():
    """Health check."""
    return {
        "status": "healthy" if _dense_model and _sparse_model else "loading",
        "dense_model": settings.EMBEDDING_MODEL_NAME,
        "sparse_model": settings.SPARSE_MODEL_NAME,
        "device": str(_dense_model.device) if _dense_model else "n/a",
    }


@app.post("/embed/dense", response_model=DenseEmbedResponse, tags=["Embedding"])
async def embed_dense(req: EmbedRequest):
    """Generate dense embeddings using Qwen3-Embedding-0.6B."""
    embeddings = _embed_dense(req.texts, req.instruction)
    return DenseEmbedResponse(
        embeddings=embeddings,
        dim=len(embeddings[0]) if embeddings else 0,
        model=settings.EMBEDDING_MODEL_NAME,
    )


@app.post("/embed/sparse", response_model=SparseEmbedResponse, tags=["Embedding"])
async def embed_sparse(req: EmbedRequest):
    """Generate BM25 sparse embeddings using FastEmbed."""
    sparse_vectors = _embed_sparse(req.texts)
    return SparseEmbedResponse(
        embeddings=sparse_vectors,
        model=settings.SPARSE_MODEL_NAME,
    )


@app.post("/embed/hybrid", response_model=HybridEmbedResponse, tags=["Embedding"])
async def embed_hybrid(req: EmbedRequest):
    """Generate both dense + sparse embeddings in one call."""
    dense = _embed_dense(req.texts, req.instruction)
    sparse = _embed_sparse(req.texts)
    return HybridEmbedResponse(
        dense=dense,
        sparse=sparse,
        dense_dim=len(dense[0]) if dense else 0,
        dense_model=settings.EMBEDDING_MODEL_NAME,
        sparse_model=settings.SPARSE_MODEL_NAME,
    )
