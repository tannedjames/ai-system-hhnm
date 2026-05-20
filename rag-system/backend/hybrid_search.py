"""Hybrid retrieval: dense + sparse -> RRF fusion -> cross-encoder rerank.

Pipeline:

    query
      | dense search  (Qdrant, semantic)   -> list A
      | sparse search (BM25, keyword)      -> list B
              |
              v
        RRF fusion (combine A + B)          -> fused list
              |
              v
        cross-encoder rerank                -> reranked candidates
              |
              v
        per-document diversification        -> final top-k

Notes
-----
* For broad / synthesis questions ("summarize", "what's the main topic",
  "overview of...") we widen the candidate pool and the final top_k so the
  LLM sees a representative cross-section of the corpus rather than five
  near-duplicate chunks from a single section.
* The reranker is a multilingual cross-encoder so Vietnamese-language docs
  aren't unfairly demoted by an English-only model.
* Relevance scores returned to the API are sigmoid-of-logit, the standard
  interpretation for ms-marco-style cross-encoder outputs.
"""
import logging
import math
import re
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

# Lazy-loaded cross-encoder
_reranker = None
_reranker_failed = False

# Recognising broad/synthesis-style questions in English and Vietnamese.
_BROAD_QUERY_PATTERNS = re.compile(
    r"\b("
    r"summari[sz]e|summary|overview|outline|main\s+(topic|idea|theme|point)s?|"
    r"key\s+(point|takeaway|idea|theme)s?|overall|in\s+general|"
    r"what\s+is\s+(this|the\s+document|the\s+paper|the\s+book)\s+about|"
    r"compare|contrast|differences?|similarities|across|throughout|"
    r"list\s+(all|the)|all\s+the|every|each\s+chapter|"
    r"tom\s*tat|tong\s*quan|chu\s*de\s*chinh|y\s*chinh|"
    r"noi\s*dung\s*chinh|liet\s*ke|so\s*sanh|tong\s*hop|"
    r"tóm\s*tắt|tổng\s*quan|chủ\s*đề\s*chính|ý\s*chính|"
    r"nội\s*dung\s*chính|liệt\s*kê|so\s*sánh|tổng\s*hợp"
    r")\b",
    re.IGNORECASE,
)


def _looks_broad(query: str) -> bool:
    """Heuristic: does this question require synthesis across the whole corpus?"""
    if not query:
        return False
    if _BROAD_QUERY_PATTERNS.search(query):
        return True
    words = query.split()
    if len(words) <= 3 and any(
        w.lower() in {"summary", "overview", "topic", "topics"} for w in words
    ):
        return True
    return False


def _chunk_key(c: Dict[str, Any]) -> str:
    """Stable identity for a chunk (used to dedupe across dense/sparse)."""
    return f"{c['source']}::{c['chunk_index']}"


def reciprocal_rank_fusion(
    dense: List[Dict[str, Any]],
    sparse: List[Dict[str, Any]],
    k: int = 60,
) -> List[Dict[str, Any]]:
    """Combine two ranked lists using Reciprocal Rank Fusion."""
    scores: Dict[str, float] = {}
    chunk_by_key: Dict[str, Dict[str, Any]] = {}

    for ranked_list in (dense, sparse):
        for rank, chunk in enumerate(ranked_list):
            key = _chunk_key(chunk)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            if key not in chunk_by_key:
                chunk_by_key[key] = chunk

    fused = []
    for key, score in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
        c = dict(chunk_by_key[key])
        c["rrf_score"] = round(score, 6)
        fused.append(c)
    return fused


def _get_reranker():
    """Load the multilingual cross-encoder on first use."""
    global _reranker, _reranker_failed
    if _reranker is not None or _reranker_failed:
        return _reranker

    try:
        from sentence_transformers import CrossEncoder
    except Exception as e:
        logger.warning(f"sentence_transformers not available: {e}")
        _reranker_failed = True
        return None

    candidates = [
        "cross-encoder/ms-marco-MiniLM-L-6-v2",
        "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
    ]
    try:
        from backend.config import settings
        if "multilingual" in (settings.EMBEDDING_MODEL or "").lower():
            candidates = [
                "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
                "cross-encoder/ms-marco-MiniLM-L-6-v2",
            ]
    except Exception:
        pass

    for name in candidates:
        try:
            logger.info(f"Loading cross-encoder: {name}")
            _reranker = CrossEncoder(name, max_length=512)
            logger.info(f"Cross-encoder loaded: {name}")
            return _reranker
        except Exception as e:
            logger.warning(f"Reranker {name} failed to load: {e}")

    _reranker_failed = True
    return None


def _sigmoid(x: float) -> float:
    """Sigmoid clipped to avoid overflow on extreme cross-encoder logits."""
    x = max(min(x, 50.0), -50.0)
    return 1.0 / (1.0 + math.exp(-x))


def rerank(
    query: str,
    chunks: List[Dict[str, Any]],
    top_k: int = 10,
) -> List[Dict[str, Any]]:
    """Re-score chunks with a cross-encoder and return the top_k."""
    if not chunks:
        return []

    model = _get_reranker()
    if model is None:
        logger.warning("No reranker available, returning fused order")
        return chunks[:top_k]

    try:
        pairs = [[query, c["text"]] for c in chunks]
        raw_scores = model.predict(pairs)
        scores = [float(x) for x in raw_scores]
    except Exception as e:
        logger.warning(f"Rerank prediction failed, falling back to fused order: {e}")
        return chunks[:top_k]

    for c, raw in zip(chunks, scores):
        c["rerank_score"] = raw
        c["rerank_score_norm"] = _sigmoid(raw)

    reranked = sorted(chunks, key=lambda c: c["rerank_score"], reverse=True)
    return reranked[:top_k]


def _diversify_by_source(
    chunks: List[Dict[str, Any]],
    top_k: int,
    per_source_cap: int,
) -> List[Dict[str, Any]]:
    """Re-order chunks so the top_k spans multiple source documents."""
    if per_source_cap <= 0 or len(chunks) <= top_k:
        return chunks[:top_k]

    picked: List[Dict[str, Any]] = []
    per_source_count: Dict[str, int] = {}
    leftover: List[Dict[str, Any]] = []

    for c in chunks:
        src = c.get("source", "unknown")
        if per_source_count.get(src, 0) < per_source_cap:
            picked.append(c)
            per_source_count[src] = per_source_count.get(src, 0) + 1
            if len(picked) >= top_k:
                return picked
        else:
            leftover.append(c)

    for c in leftover:
        if len(picked) >= top_k:
            break
        picked.append(c)
    return picked


def hybrid_search(
    query: str,
    query_vector: List[float],
    top_k: int = 5,
    candidate_k: int = None,
    use_rerank: bool = True,
) -> List[Dict[str, Any]]:
    """Full hybrid retrieval."""
    from backend.storage import qdrant_client as qdrant
    from backend.bm25_search import search_bm25

    is_broad = _looks_broad(query)

    if is_broad:
        effective_top_k = max(top_k, 12)
        effective_candidate_k = candidate_k if candidate_k is not None else 60
    else:
        effective_top_k = top_k
        effective_candidate_k = candidate_k if candidate_k is not None else max(30, top_k * 5)

    logger.info(
        f"hybrid_search: broad={is_broad} top_k={effective_top_k} "
        f"candidate_k={effective_candidate_k}"
    )

    try:
        dense = qdrant.search_chunks(query_vector, top_k=effective_candidate_k)
    except Exception as e:
        logger.warning(f"Dense search failed: {e}")
        dense = []

    try:
        sparse = search_bm25(query, top_k=effective_candidate_k)
    except Exception as e:
        logger.warning(f"Sparse search failed: {e}")
        sparse = []

    if not dense and not sparse:
        return []

    fused = reciprocal_rank_fusion(dense, sparse)

    rerank_pool = min(len(fused), max(effective_top_k * 3, 30))
    if use_rerank:
        reranked = rerank(query, fused[:rerank_pool], top_k=rerank_pool)
    else:
        reranked = fused[:rerank_pool]

    unique_sources = {c.get("source", "unknown") for c in reranked}
    if is_broad and len(unique_sources) > 1:
        per_source_cap = max(2, effective_top_k // max(1, len(unique_sources)) + 1)
    else:
        per_source_cap = max(2, effective_top_k // 2 + 1)

    final = _diversify_by_source(reranked, effective_top_k, per_source_cap)

    for c in final:
        c["score"] = c.get("rerank_score_norm", c.get("rrf_score", 0.0))

    return final
