"""Local embeddings for RAG.

Default provider is a real local sentence-transformers semantic model.
Set EMBEDDING_PROVIDER=hashing only for local smoke tests.
"""
import hashlib
import logging
import math
import re
from urllib.parse import urlsplit, urlunsplit
from typing import List

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

_model = None  # Lazy load
_token_re = re.compile(r"\w+", re.UNICODE)


def _hashing_embed(text: str) -> List[float]:
    vector = [0.0] * settings.EMBEDDING_DIM
    tokens = _token_re.findall((text or "").lower())
    if not tokens:
        raise ValueError("Cannot embed empty text")

    features = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
    for feature in features:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        raw = int.from_bytes(digest, "big")
        index = raw % settings.EMBEDDING_DIM
        sign = 1.0 if (raw >> 63) else -1.0
        vector[index] += sign

    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _get_model():
    """Load the embedding model on first use."""
    global _model
    if _model is None:
        if settings.EMBEDDING_PROVIDER != "sentence-transformers":
            return None
        from sentence_transformers import SentenceTransformer
        logger.info(f"Loading embedding model: {settings.EMBEDDING_MODEL}")
        _model = SentenceTransformer(settings.EMBEDDING_MODEL, device=settings.EMBEDDING_DEVICE)
        logger.info("Embedding model loaded")
    return _model

def _normalize_openai_base_url(raw_url: str) -> str:
    value = (raw_url or "").strip() or "https://api.openai.com/v1"
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    parts = urlsplit(value)
    path = parts.path.rstrip("/")
    if not path.endswith("/v1"):
        path = f"{path}/v1" if path else "/v1"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _openai_embed_batch(texts: List[str]) -> List[List[float]]:
    if not settings.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is required for openai-compatible embeddings")
    api_base = _normalize_openai_base_url(settings.OPENAI_BASE_URL)
    headers = {
        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": settings.OPENAI_USER_AGENT,
    }
    payload = {"model": settings.EMBEDDING_MODEL, "input": texts}
    with httpx.Client(timeout=60.0) as client:
        response = client.post(f"{api_base}/embeddings", json=payload, headers=headers)
    response.raise_for_status()
    data = sorted(response.json()["data"], key=lambda item: item.get("index", 0))
    return [item["embedding"] for item in data]


def embed_text(text: str) -> List[float]:
    """Embed a single text into a vector."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Cannot embed empty text")

    if settings.EMBEDDING_PROVIDER == "hashing":
        return _hashing_embed(text)
    if settings.EMBEDDING_PROVIDER == "openai-compatible":
        return _openai_embed_batch([text])[0]
    if settings.EMBEDDING_PROVIDER != "sentence-transformers":
        raise ValueError(f"Unsupported embedding provider: {settings.EMBEDDING_PROVIDER}")

    vector = _get_model().encode(text, convert_to_numpy=True)
    return vector.tolist()


def embed_batch(texts: List[str]) -> List[List[float]]:
    """Embed multiple texts at once (more efficient)."""
    if not texts:
        return []

    if settings.EMBEDDING_PROVIDER == "hashing":
        return [_hashing_embed(text) for text in texts]
    if settings.EMBEDDING_PROVIDER == "openai-compatible":
        return _openai_embed_batch(texts)
    if settings.EMBEDDING_PROVIDER != "sentence-transformers":
        raise ValueError(f"Unsupported embedding provider: {settings.EMBEDDING_PROVIDER}")

    vectors = _get_model().encode(texts, convert_to_numpy=True, batch_size=settings.EMBEDDING_BATCH_SIZE)
    return vectors.tolist()
