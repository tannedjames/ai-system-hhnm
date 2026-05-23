"""Hybrid retrieval: dense + sparse → RRF fusion → cross-encoder rerank.

Pipeline (matches the assignment diagram):

    query
      ├─ dense search  (Qdrant)        → list A
      └─ sparse search (BM25)          → list B
              │
              ▼
        RRF fusion (combine A + B)     → fused list
              │
              ▼
        cross-encoder rerank           → final top-k
"""
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# Lazy-loaded cross-encoder
_reranker = None


def _chunk_key(c: Dict[str, Any]) -> str:
    """Stable identity for a chunk (used to dedupe across dense/sparse)."""
    return f"{c['source']}::{c['chunk_index']}"


def reciprocal_rank_fusion(
    dense: List[Dict[str, Any]],
    sparse: List[Dict[str, Any]],
    k: int = 60,
) -> List[Dict[str, Any]]:
    """
    Combine two ranked lists using Reciprocal Rank Fusion.

    RRF score for a doc = sum over each list of 1 / (k + rank).
    k=60 is the value from the original RRF paper; it dampens the
    influence of any single list's top results.

    Returns a single list sorted by fused score (desc).
    """
    scores: Dict[str, float] = {}
    chunk_by_key: Dict[str, Dict[str, Any]] = {}

    for ranked_list in (dense, sparse):
        for rank, chunk in enumerate(ranked_list):
            key = _chunk_key(chunk)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            # Keep the first occurrence (they're identical payloads anyway)
            if key not in chunk_by_key:
                chunk_by_key[key] = chunk

    fused = []
    for key, score in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
        c = dict(chunk_by_key[key])
        c["rrf_score"] = round(score, 6)
        fused.append(c)

    return fused


def _get_reranker():
    """Load the cross-encoder model on first use."""
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder
        # Small, fast, multilingual-friendly cross-encoder
        model_name = "cross-encoder/ms-marco-MiniLM-L-6-v2"
        logger.info(f"Loading cross-encoder: {model_name}")
        _reranker = CrossEncoder(model_name, max_length=512)
        logger.info("Cross-encoder loaded")
    return _reranker


def rerank(
    query: str,
    chunks: List[Dict[str, Any]],
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """
    Re-score chunks with a cross-encoder (query, chunk_text) and return top_k.

    A cross-encoder reads the query and the chunk *together*, so it's much
    more accurate than the bi-encoder used for initial retrieval — but too
    slow to run over the whole corpus, which is why we only rerank the
    fused candidates.
    """
    if not chunks:
        return []

    try:
        model = _get_reranker()
    except Exception as e:
        logger.warning(f"Reranker unavailable, skipping rerank: {e}")
        return chunks[:top_k]

    pairs = [[query, c["text"]] for c in chunks]
    scores = model.predict(pairs)
    scores = [float(x) for x in scores]

    
    import math

    # Softmax -> probabilities in 0..1 (top won't necessarily be 1.0)
    max_score = max(scores) if scores else 0.0
    exp_scores = [math.exp(s - max_score) for s in scores]
    sum_exp = sum(exp_scores) if exp_scores else 1.0

    for c, s_raw, p in zip(chunks, scores, [(e / sum_exp) for e in exp_scores]):
        c["rerank_score"] = s_raw
        c["rerank_score_norm"] = float(p)  # 0..1

    reranked = sorted(chunks, key=lambda c: c["rerank_score"], reverse=True)
    return reranked[:top_k]


def hybrid_search(
    query: str,
    query_vector: List[float],
    top_k: int = 5,
    candidate_k: int = 10,
    use_rerank: bool = True,
) -> List[Dict[str, Any]]:
    """
    Full hybrid retrieval.

    Args:
        query: raw user question (for BM25 + reranker)
        query_vector: embedded question (for dense search)
        top_k: how many final results to return
        candidate_k: how many to pull from each retriever before fusion
        use_rerank: if False, skip the cross-encoder (faster)

    Returns final ranked chunks with keys:
        text, source, page, chunk_index, score,
        rrf_score, (rerank_score if reranked)
    """
    # Import here to avoid circular imports at module load
    from backend.storage import qdrant_client as qdrant
    from backend.bm25_search import search_bm25

    # 1. Dense (semantic) search
    try:
        dense = qdrant.search_chunks(query_vector, top_k=candidate_k)
    except Exception as e:
        logger.warning(f"Dense search failed: {e}")
        dense = []

    # 2. Sparse (keyword) search
    try:
        sparse = search_bm25(query, top_k=candidate_k)
    except Exception as e:
        logger.warning(f"Sparse search failed: {e}")
        sparse = []

    if not dense and not sparse:
        return []

    # 3. RRF fusion
    fused = reciprocal_rank_fusion(dense, sparse)

    # 4. Cross-encoder rerank (optional)
    if use_rerank:
        final = rerank(query, fused, top_k=top_k)
    else:
        final = fused[:top_k]

    # Normalize a "score" field for the API response
    
    for c in final:
       c["score"] = c.get("rerank_score_norm", c.get("rrf_score", 0.0))

    return final