"""Local embeddings via sentence-transformers (lazy-loaded)."""
import logging
from typing import List

logger = logging.getLogger(__name__)

_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_model = None  # lazy


def _get_model():
    """Load the model on first use, not at import time."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info(f"Loading embedding model: {_MODEL_NAME}")
        _model = SentenceTransformer(_MODEL_NAME)
        logger.info("Embedding model loaded")
    return _model


def embed_text(text: str) -> List[float]:
    text = (text or "").strip()
    if not text:
        raise ValueError("Cannot embed empty text")
    vector = _get_model().encode(text, convert_to_numpy=True)
    return vector.tolist()


def embed_batch(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []
    vectors = _get_model().encode(texts, convert_to_numpy=True, batch_size=32)
    return vectors.tolist()
