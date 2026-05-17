"""Local embeddings using sentence-transformers (lazy-loaded)."""
import logging
from typing import List

from backend.config import settings

logger = logging.getLogger(__name__)

_model = None  # Lazy load


def _get_model():
    """Load the embedding model on first use."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info(f"Loading embedding model: {settings.EMBEDDING_MODEL}")
        _model = SentenceTransformer(settings.EMBEDDING_MODEL)
        logger.info("Embedding model loaded")
    return _model


def embed_text(text: str) -> List[float]:
    """Embed a single text into a vector."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Cannot embed empty text")

    vector = _get_model().encode(text, convert_to_numpy=True)
    return vector.tolist()


def embed_batch(texts: List[str]) -> List[List[float]]:
    """Embed multiple texts at once (more efficient)."""
    if not texts:
        return []

    vectors = _get_model().encode(texts, convert_to_numpy=True, batch_size=32)
    return vectors.tolist()
