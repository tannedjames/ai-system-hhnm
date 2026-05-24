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
# Includes plural forms ("what are these files about", "what are the documents
# about") because asking about multiple sources is *the* canonical broad query
# and must trigger broad-mode retrieval to avoid small-doc starvation.
_BROAD_QUERY_PATTERNS = re.compile(
    r"\b("
    r"summari[sz]e|summary|overview|outline|main\s+(topic|idea|theme|point)s?|"
    r"key\s+(point|takeaway|idea|theme)s?|overall|in\s+general|"
    # Permissive "what is/are/do/does ... about" — catches any noun the user picks:
    # "files", "stuff", "uploaded stuff", "the materials", "these things", etc.
    # Matches up to ~60 chars between "is/are" and "about". No end-anchor so
    # trailing punctuation (?, .) doesn't break the outer \b word-boundary.
    r"what\s+(is|are|do|does)\s+[\w\s'\-]{1,60}\s+about|"
    r"what\s+(is|are)\s+(this|these|it|they)\s+about|"
    r"what(\s+are)?\s+(these|the|all\s+the)\s+(document|paper|book|file)s?(\s+about)?|"
    # Informal "tell me about" and "explain/describe these/all/everything" forms
    r"tell\s+me\s+about\s+(this|these|them|all|everything|the\s+\w+)|"
    r"(explain|describe)\s+(this|these|them|all|everything|the\s+\w+)|"
    r"compare|contrast|differences?|similarities|across|throughout|"
    r"list\s+(all|the)|all\s+the|every|each\s+chapter|"
    r"tom\s*tat|tong\s*quan|chu\s*de\s*chinh|y\s*chinh|"
    r"noi\s*dung\s*chinh|liet\s*ke|so\s*sanh|tong\s*hop|"
    r"(cac|nhung|nhg)\s*(tai\s*lieu|file|van\s*ban)|"
    r"tóm\s*tắt|tổng\s*quan|chủ\s*đề\s*chính|ý\s*chính|"
    r"nội\s*dung\s*chính|liệt\s*kê|so\s*sánh|tổng\s*hợp|"
    r"(các|những)\s*(tài\s*liệu|file|văn\s*bản)"
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


def _ensure_per_source_coverage(
    fused: List[Dict[str, Any]],
    pool_size: int,
    min_per_source: int,
) -> List[Dict[str, Any]]:
    """Take the top `pool_size` chunks from `fused`, but guarantee that every
    source appearing anywhere in `fused` contributes at least `min_per_source`
    chunks (if it has that many).

    Why this exists
    ---------------
    Without this, a small document (say 3 chunks) competing against a large
    document (100+ chunks) can be statistically squeezed out of the rerank
    pool entirely — even when the user asks a broad question. The rerank
    pool was a fixed top-N over the fused list, and that fixed N was almost
    always dominated by the bigger document.

    With this, under-represented sources are topped up from lower positions
    in the fused list, so the reranker actually sees their chunks. Adds zero
    new queries — only re-orders already-fetched data.
    """
    if not fused or pool_size <= 0:
        return []

    pool: List[Dict[str, Any]] = list(fused[:pool_size])
    pool_keys = {_chunk_key(c) for c in pool}

    # Tally current per-source representation
    counts: Dict[str, int] = {}
    for c in pool:
        src = c.get("source", "unknown")
        counts[src] = counts.get(src, 0) + 1

    # Collect every source that appears in the full fused list, in fused order
    seen_sources = set()
    all_sources: List[str] = []
    for c in fused:
        src = c.get("source", "unknown")
        if src not in seen_sources:
            seen_sources.add(src)
            all_sources.append(src)

    # Top up under-represented sources from positions below pool_size
    for src in all_sources:
        if counts.get(src, 0) >= min_per_source:
            continue
        for c in fused[pool_size:]:
            if counts.get(src, 0) >= min_per_source:
                break
            if c.get("source", "unknown") != src:
                continue
            key = _chunk_key(c)
            if key in pool_keys:
                continue
            pool.append(c)
            pool_keys.add(key)
            counts[src] = counts.get(src, 0) + 1

    return pool


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
        # Wider net for broad queries — small docs that rank low globally must
        # still appear in dense top-N so they can be reranked alongside the rest.
        effective_candidate_k = candidate_k if candidate_k is not None else 200
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

    # Strong guarantee for broad queries: explicitly pull the top chunks from
    # *every* document in the corpus, not just the top-N globally. This is the
    # only way a small doc (3 chunks) competing against a big doc (200 chunks)
    # can be sure to land in the rerank pool — global ranking will always favor
    # the bigger doc on generic queries like "main topic".
    if is_broad:
        try:
            all_sources = qdrant.list_unique_sources()
            sources_in_fused = {c.get("source", "unknown") for c in fused}
            missing_sources = [s for s in all_sources if s not in sources_in_fused]
            if missing_sources:
                logger.info(
                    f"hybrid_search: forcing inclusion of {len(missing_sources)} "
                    f"missing source(s) for broad query"
                )
            existing_keys = {_chunk_key(c) for c in fused}
            for src in all_sources:
                try:
                    per_source_hits = qdrant.search_by_source(
                        query_vector, src, top_k=3
                    )
                except Exception as e:
                    logger.warning(f"Per-source query for '{src}' failed: {e}")
                    continue
                for c in per_source_hits:
                    if _chunk_key(c) in existing_keys:
                        continue
                    # Append at the END — RRF order is preserved; supplemental
                    # chunks are just guaranteed to be visible to the reranker.
                    fused.append(c)
                    existing_keys.add(_chunk_key(c))
        except Exception as e:
            logger.warning(f"Per-source augmentation failed (continuing): {e}")

    rerank_pool_size = min(len(fused), max(effective_top_k * 3, 30))
    # Guarantee small documents aren't squeezed out by larger ones: every source
    # in `fused` contributes at least `min_per_source` chunks to the rerank pool.
    # Broader queries widen the guarantee since synthesis answers need every doc.
    min_per_source = 2 if is_broad else 1
    rerank_input = _ensure_per_source_coverage(
        fused,
        pool_size=rerank_pool_size,
        min_per_source=min_per_source,
    )
    logger.info(
        f"hybrid_search: rerank_pool={len(rerank_input)} "
        f"(natural={rerank_pool_size}, min_per_source={min_per_source}, "
        f"sources={len({c.get('source','unknown') for c in rerank_input})})"
    )

    if use_rerank:
        reranked = rerank(query, rerank_input, top_k=len(rerank_input))
    else:
        reranked = rerank_input

    unique_sources = {c.get("source", "unknown") for c in reranked}
    if is_broad and len(unique_sources) > 1:
        per_source_cap = max(2, effective_top_k // max(1, len(unique_sources)) + 1)
    else:
        per_source_cap = max(2, effective_top_k // 2 + 1)

    final = _diversify_by_source(reranked, effective_top_k, per_source_cap)

    for c in final:
        c["score"] = c.get("rerank_score_norm", c.get("rrf_score", 0.0))

    return final
