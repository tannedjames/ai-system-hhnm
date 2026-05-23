"""BM25 sparse keyword search.

BM25 is a bag-of-words ranking function (the classic search-engine algorithm).
It complements dense vector search: dense search captures meaning, BM25
captures exact keyword overlap. Combining both (see hybrid_search.py) gives
better retrieval than either alone.

Design note: BM25 needs an in-memory index of every chunk. Because the backend
and worker run in separate containers, we don't try to share the index. Instead
we lazily (re)build it from whatever is currently stored in Qdrant, and cache
it in-process. The cache is invalidated whenever documents change.

The freshness check uses a *signature* (count + hash of source identities),
not just a chunk count - otherwise deleting N chunks and adding N different
chunks would leave a stale index in memory.
"""
import hashlib
import logging
import re
import threading
from typing import List, Dict, Any, Optional, Tuple

from rank_bm25 import BM25Okapi

from backend.storage import qdrant_client as qdrant

logger = logging.getLogger(__name__)

# Process-local cache
_lock = threading.Lock()
_bm25: Optional[BM25Okapi] = None
_corpus_chunks: List[Dict[str, Any]] = []
_index_signature: Optional[Tuple[int, str]] = None  # (count, sources_hash)


def _tokenize(text: str) -> List[str]:
    """Lowercase + split on non-alphanumeric. Works for EN, VN (Unicode \\w by default)."""
    return re.findall(r"\w+", (text or "").lower())


def _fetch_all_chunks() -> List[Dict[str, Any]]:
    """Pull every chunk payload out of Qdrant via scroll."""
    all_chunks: List[Dict[str, Any]] = []
    offset = None

    while True:
        points, offset = qdrant.client.scroll(
            collection_name=qdrant.settings.QDRANT_COLLECTION,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for p in points:
            payload = p.payload or {}
            all_chunks.append({
                "text": payload.get("text", ""),
                "source": payload.get("source", "unknown"),
                "page": payload.get("page", 0),
                "page_end": payload.get("page_end", payload.get("page", 0)),
                "chunk_index": payload.get("chunk_index", 0),
            })
        if offset is None:
            break

    return all_chunks


def invalidate_cache():
    """Call after documents are added/removed so the index rebuilds."""
    global _bm25, _corpus_chunks, _index_signature
    with _lock:
        _bm25 = None
        _corpus_chunks = []
        _index_signature = None
    logger.info("BM25 cache invalidated")


def _current_signature() -> Optional[Tuple[int, str]]:
    """Cheap signature of the current corpus: (count, hash of (source, chunk_index)).

    Survives the same-count churn that the old count-only check missed.
    """
    try:
        count = qdrant.client.count(
            collection_name=qdrant.settings.QDRANT_COLLECTION,
            exact=False,
        ).count
    except Exception as e:
        logger.warning(f"Could not count Qdrant points: {e}")
        return None

    h = hashlib.md5()
    offset = None
    try:
        while True:
            points, offset = qdrant.client.scroll(
                collection_name=qdrant.settings.QDRANT_COLLECTION,
                limit=512,
                offset=offset,
                with_payload=["source", "chunk_index"],
                with_vectors=False,
            )
            for p in points:
                payload = p.payload or {}
                h.update(f"{payload.get('source', '')}::{payload.get('chunk_index', '')}\n".encode("utf-8"))
            if offset is None:
                break
    except Exception as e:
        logger.warning(f"Signature scan failed (will rebuild defensively): {e}")
        return (count, "scan-failed")

    return (count, h.hexdigest())


def _ensure_index():
    """Build the BM25 index if it's missing or stale."""
    global _bm25, _corpus_chunks, _index_signature

    sig = _current_signature()

    with _lock:
        if _bm25 is not None and sig is not None and sig == _index_signature:
            return  # Cache is fresh

        logger.info("Building BM25 index from Qdrant...")
        chunks = _fetch_all_chunks()

        if not chunks:
            _bm25 = None
            _corpus_chunks = []
            _index_signature = (0, "empty")
            logger.info("BM25 index empty (no chunks)")
            return

        tokenized = [_tokenize(c["text"]) for c in chunks]
        _bm25 = BM25Okapi(tokenized)
        _corpus_chunks = chunks
        _index_signature = sig if sig is not None else (len(chunks), "no-sig")
        logger.info(f"BM25 index built: {len(chunks)} chunks")


def search_bm25(query: str, top_k: int = 10) -> List[Dict[str, Any]]:
    """Keyword search over all indexed chunks.

    Returns list of dicts: text, source, page, page_end, chunk_index, score
    """
    _ensure_index()

    if _bm25 is None or not _corpus_chunks:
        return []

    tokens = _tokenize(query)
    if not tokens:
        return []

    scores = _bm25.get_scores(tokens)

    ranked = sorted(
        range(len(scores)),
        key=lambda i: scores[i],
        reverse=True,
    )[:top_k]

    results = []
    for i in ranked:
        if scores[i] <= 0:
            continue
        chunk = _corpus_chunks[i]
        results.append({
            "text": chunk["text"],
            "source": chunk["source"],
            "page": chunk["page"],
            "page_end": chunk.get("page_end", chunk["page"]),
            "chunk_index": chunk["chunk_index"],
            "score": float(scores[i]),
        })

    return results
