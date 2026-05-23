"""FastAPI backend with full RAG pipeline."""
import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx
import requests
from fastapi import FastAPI, File, HTTPException, UploadFile, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, HttpUrl
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.auth import create_access_token, get_current_user, hash_password, require_admin, verify_password
from backend.config import settings
from backend.models import Conversation, Document, User, SystemConfig
from backend.rag_embedding import embed_text
from backend.storage import minio_client as minio
from backend.storage import qdrant_client as qdrant
from backend.storage.database import SessionLocal, get_db, init_db
from backend.cache import _client as redis_client
from backend.cache import make_key, get_json, set_json, delete_prefix

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("rag-backend")


class AskRequest(BaseModel):
    question: str
    top_k: int = 5


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"

class UpdateUserRequest(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    password: str | None = None


class UploadUrlRequest(BaseModel):
    url: HttpUrl

class SetConfigRequest(BaseModel):
    key: str
    value: str

class RagSettingsRequest(BaseModel):
    CHUNK_SIZE: int | None = None
    CHUNK_OVERLAP: int | None = None
    RAG_CHUNK_MIN_CHARS: int | None = None
    RAG_DEDUP_CHUNKS: bool | None = None
    RETRIEVAL_TOP_K: int | None = None
    USE_RERANKER: bool | None = None
    MAX_CONTEXT_CHUNKS: int | None = None
    MAX_SOURCES_IN_ANSWER: int | None = None
    MIN_RELEVANCE_SCORE: float | None = None
    EMBEDDING_PROVIDER: str | None = None
    EMBEDDING_MODEL: str | None = None
    EMBEDDING_DIM: int | None = None
    OCR_ENABLED: bool | None = None
    OCR_LANGS: str | None = None
    OCR_DPI: int | None = None
    OCR_MIN_TEXT_CHARS: int | None = None

RAG_SETTING_KEYS = [
    "CHUNK_SIZE",
    "CHUNK_OVERLAP",
    "RAG_CHUNK_MIN_CHARS",
    "RAG_DEDUP_CHUNKS",
    "RETRIEVAL_TOP_K",
    "USE_RERANKER",
    "MAX_CONTEXT_CHUNKS",
    "MAX_SOURCES_IN_ANSWER",
    "MIN_RELEVANCE_SCORE",
    "EMBEDDING_PROVIDER",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIM",
    "OCR_ENABLED",
    "OCR_LANGS",
    "OCR_DPI",
    "OCR_MIN_TEXT_CHARS",
]

_MD_CLEAN_RE = re.compile(r"[*_`#>{}]")
_ANSWER_FALLBACK_MARKERS = (
    "i don't have enough information",
    "i do not have enough information",
    "context insufficient",
    "not enough information",
)
_QUERY_STOPWORDS = {
    "about", "after", "and", "are", "can", "for", "from", "how", "into",
    "that", "the", "their", "this", "tell", "what", "when", "where",
    "which", "with", "would", "you",
}

def _clean_markdown_text(text: str) -> str:
    value = text or ""
    value = _MD_CLEAN_RE.sub("", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()

def _settings_rows(db: Session) -> dict[str, str]:
    return {row.key: row.value for row in db.query(SystemConfig).all()}

def _effective_rag_settings(db: Session) -> dict[str, str]:
    rows = _settings_rows(db)
    defaults = {
        "CHUNK_SIZE": str(settings.CHUNK_SIZE),
        "CHUNK_OVERLAP": str(settings.CHUNK_OVERLAP),
        "RAG_CHUNK_MIN_CHARS": str(settings.RAG_CHUNK_MIN_CHARS),
        "RAG_DEDUP_CHUNKS": str(settings.RAG_DEDUP_CHUNKS),
        "RETRIEVAL_TOP_K": str(settings.RETRIEVAL_TOP_K),
        "USE_RERANKER": str(settings.USE_RERANKER),
        "MAX_CONTEXT_CHUNKS": str(settings.MAX_CONTEXT_CHUNKS),
        "MAX_SOURCES_IN_ANSWER": str(settings.MAX_SOURCES_IN_ANSWER),
        "MIN_RELEVANCE_SCORE": str(settings.MIN_RELEVANCE_SCORE),
        "EMBEDDING_PROVIDER": settings.EMBEDDING_PROVIDER,
        "EMBEDDING_MODEL": settings.EMBEDDING_MODEL,
        "EMBEDDING_DIM": str(settings.EMBEDDING_DIM),
        "OCR_ENABLED": str(settings.OCR_ENABLED),
        "OCR_LANGS": settings.OCR_LANGS,
        "OCR_DPI": str(settings.OCR_DPI),
        "OCR_MIN_TEXT_CHARS": str(settings.OCR_MIN_TEXT_CHARS),
    }
    return {key: rows.get(key, defaults[key]) for key in RAG_SETTING_KEYS}

def _save_system_config(db: Session, key: str, value: str) -> SystemConfig:
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if row:
        row.value = value
        row.updated_at = datetime.now(timezone.utc)
    else:
        row = SystemConfig(key=key, value=value, updated_at=datetime.now(timezone.utc))
        db.add(row)
    db.commit()
    db.refresh(row)
    return row

def _bool_value(value: str | bool) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

def _runtime_rag_options(db: Session) -> dict:
    effective = _effective_rag_settings(db)
    return {
        "retrieval_top_k": max(1, int(effective["RETRIEVAL_TOP_K"])),
        "max_context_chunks": max(1, int(effective["MAX_CONTEXT_CHUNKS"])),
        "max_sources": max(1, int(effective["MAX_SOURCES_IN_ANSWER"])),
        "min_relevance_score": max(0.0, float(effective["MIN_RELEVANCE_SCORE"])),
        "use_reranker": _bool_value(effective["USE_RERANKER"]),
    }

def _reset_ingest_tracking(doc: Document, effective: dict[str, str]):
    doc.status = "processing"
    doc.error_message = None
    doc.chunks_count = 0
    doc.pages_count = None
    doc.empty_pages_count = None
    doc.ocr_pages_count = None
    doc.chunks_dropped = None
    doc.ingest_duration_ms = None
    doc.processed_at = None
    doc.embedding_model = effective["EMBEDDING_MODEL"]
    doc.embedding_dim = int(effective["EMBEDDING_DIM"])
    doc.chunk_size = int(effective["CHUNK_SIZE"])
    doc.chunk_overlap = int(effective["CHUNK_OVERLAP"])
    doc.pipeline_version = "v2"

def _queue_vector_index_repairs(collection_state: dict):
    db = SessionLocal()
    try:
        docs = db.query(Document).order_by(Document.uploaded_at.asc()).all()
        if not docs:
            return

        force_full_reindex = bool(collection_state.get("created") or collection_state.get("recreated"))
        source_counts = {} if force_full_reindex else qdrant.source_counts()
        if not force_full_reindex and not source_counts and any((doc.chunks_count or 0) > 0 for doc in docs):
            force_full_reindex = True

        effective = _effective_rag_settings(db)
        queued: list[tuple[str, int]] = []
        refreshed = 0

        for doc in docs:
            indexed_chunks = source_counts.get(doc.filename, 0)
            missing_vectors = indexed_chunks == 0 and doc.status in {"completed", "pending", "processing"}
            partial_vectors = 0 < indexed_chunks < (doc.chunks_count or 0)
            same_dim_model_changed = (
                indexed_chunks > 0
                and doc.embedding_dim == settings.EMBEDDING_DIM
                and bool(doc.embedding_model)
                and doc.embedding_model != settings.EMBEDDING_MODEL
            )

            if force_full_reindex or missing_vectors or partial_vectors or same_dim_model_changed:
                _reset_ingest_tracking(doc, effective)
                queued.append((doc.filename, doc.id))
                continue

            if indexed_chunks > 0:
                changed = False
                if doc.chunks_count != indexed_chunks:
                    doc.chunks_count = indexed_chunks
                    changed = True
                if doc.embedding_dim != settings.EMBEDDING_DIM:
                    doc.embedding_dim = settings.EMBEDDING_DIM
                    changed = True
                if doc.embedding_model != settings.EMBEDDING_MODEL:
                    doc.embedding_model = settings.EMBEDDING_MODEL
                    changed = True
                if changed:
                    refreshed += 1

        db.commit()

        if not queued:
            if refreshed:
                logger.info("Refreshed vector metadata for %s documents", refreshed)
            return

        from worker.tasks.ingest import ingest_document

        for filename, doc_id in queued:
            ingest_document.delay(filename, doc_id, True)
        delete_prefix("ask")
        delete_prefix("search")
        logger.warning("Queued vector index repair for %s documents", len(queued))
    except Exception as e:
        db.rollback()
        logger.exception("Vector index repair check failed: %s", e)
    finally:
        db.close()

def _filter_and_cap_sources(chunks: list[dict], options: dict) -> list[dict]:
    if not chunks:
        return []

    max_chunks = options["max_context_chunks"]
    best_score = max(float(c.get("score", 0.0)) for c in chunks)
    configured_floor = options["min_relevance_score"]
    relative_floor = best_score * 0.18 if best_score > 0 else 0.0
    min_score = max(configured_floor, relative_floor)
    kept = [c for c in chunks if float(c.get("score", 0.0)) >= min_score]
    if not kept and chunks:
        kept = chunks[:max_chunks]
    return kept[:max_chunks]

def _public_sources(chunks: list[dict], options: dict) -> list[dict]:
    max_sources = options["max_sources"]
    return [
        {
            "file": c["source"],
            "page": c["page"],
            "chunk_index": c["chunk_index"],
            "relevance_score": round(float(c.get("score", 0.0)), 3),
            "preview": _clean_markdown_text((c.get("text") or "")[:240]),
        }
        for c in chunks[:max_sources]
    ]

def _sources_for_answer(chunks: list[dict], answer: str, options: dict) -> list[dict]:
    max_sources = options["max_sources"]
    cited_pairs = [
        (source.strip(), int(page))
        for source, page in re.findall(r"\[Source:\s*([^,\]]+),\s*Page:\s*(\d+)\]", answer or "")
    ]

    selected: list[dict] = []
    seen: set[str] = set()
    for cited_source, cited_page in cited_pairs:
        for chunk in chunks:
            if chunk.get("source") == cited_source and int(chunk.get("page", 0)) == cited_page:
                key = f"{chunk.get('source')}::{chunk.get('chunk_index')}"
                if key not in seen:
                    selected.append(chunk)
                    seen.add(key)
                break

    for chunk in chunks:
        key = f"{chunk.get('source')}::{chunk.get('chunk_index')}"
        if key not in seen:
            selected.append(chunk)
            seen.add(key)
        if len(selected) >= max_sources:
            break

    return _public_sources(selected, options)

def _context_from_chunks(chunks: list[dict]) -> str:
    return "\n\n---\n\n".join(
        f"[Source: {c['source']}, Page: {c['page']}]\n{c['text']}"
        for c in chunks
    )

def _query_terms(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"\w+", (text or "").lower())
        if len(token) > 2 and token not in _QUERY_STOPWORDS
    }

def _query_variants(question: str) -> list[str]:
    variants = [question]
    lower = question.lower()
    if "loss" in lower:
        variants.append("loss functions squared error logistic regression support vector machine hinge loss classification")
    if "chapter 8" in lower or "after chapter" in lower:
        variants.append("tree-based methods bagging random forests boosting support vector machines unsupervised learning deep learning neural networks")
    if "non-linear" in lower or "nonlinearity" in lower or "non-linearity" in lower:
        variants.append("non-linear relationships GAM splines trees random forests boosting neural networks deep learning")
    if "gam" in lower or "tree" in lower or "neural" in lower:
        variants.append("GAM splines tree-based methods random forests boosting neural networks deep learning nonlinear")
    return list(dict.fromkeys(v.strip() for v in variants if v.strip()))

def _retrieve_answer_chunks(question: str, top_k: int, options: dict) -> list[dict]:
    from backend.hybrid_search import hybrid_search

    merged: dict[str, dict] = {}
    request_k = max(1, min(50, top_k), options["max_context_chunks"], options["retrieval_top_k"])
    for variant in _query_variants(question):
        try:
            chunks = hybrid_search(
                variant,
                embed_text(variant),
                top_k=request_k,
                candidate_k=request_k * 6,
                use_rerank=options["use_reranker"],
            )
        except Exception as exc:
            logger.warning("Retrieval failed for variant %r: %s", variant, exc)
            continue
        for chunk in chunks:
            key = f"{chunk.get('source')}::{chunk.get('chunk_index')}"
            existing = merged.get(key)
            if existing is None or float(chunk.get("score", 0.0)) > float(existing.get("score", 0.0)):
                merged[key] = chunk

    ranked = sorted(merged.values(), key=lambda c: float(c.get("score", 0.0)), reverse=True)
    return _filter_and_cap_sources(ranked, options)


def _normalize_anthropic_base_url(raw_url: str) -> str:
    value = (raw_url or "").strip()
    if not value:
        value = "https://chat.trollllm.xyz"
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    parts = urlsplit(value)
    path = parts.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _normalize_openai_base_url(raw_url: str) -> str:
    value = (raw_url or "").strip() or "https://chat.trollllm.xyz/v1"
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    parts = urlsplit(value)
    path = parts.path.rstrip("/")
    if not path.endswith("/v1"):
        path = f"{path}/v1" if path else "/v1"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))

def _answer_needs_fallback(answer: str) -> bool:
    normalized = (answer or "").strip().lower()
    return not normalized or any(marker in normalized for marker in _ANSWER_FALLBACK_MARKERS)

def _extractive_fallback_answer(question: str, chunks: list[dict]) -> str:
    if not chunks:
        return "I don't have enough information."

    terms = _query_terms(question)
    evidence: list[str] = []
    for chunk in chunks:
        text = _clean_markdown_text(chunk.get("text") or "")
        sentences = re.split(r"(?<=[.!?])\s+", text)
        ranked = sorted(
            sentences,
            key=lambda sentence: len(terms & _query_terms(sentence)),
            reverse=True,
        )
        selected = next((sentence.strip() for sentence in ranked if sentence.strip()), "")
        if selected:
            source = chunk.get("source", "unknown")
            page = chunk.get("page", "?")
            evidence.append(f"{selected[:320]} [Source: {source}, Page: {page}]")
        if len(evidence) >= 3:
            break

    if not evidence:
        return "I don't have enough information."
    return "Based on the retrieved pages:\n" + "\n".join(f"- {item}" for item in evidence)


def _ensure_default_admin():
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == settings.ADMIN_DEFAULT_USERNAME).first()
        if not existing:
            db.add(
                User(
                    username=settings.ADMIN_DEFAULT_USERNAME,
                    password_hash=hash_password(settings.ADMIN_DEFAULT_PASSWORD),
                    role="admin",
                    is_active=True,
                )
            )
            db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting RAG backend")
    try:
        init_db()
        _ensure_default_admin()
        collection_state = qdrant.init_collection()
        minio.init_bucket()
        _queue_vector_index_repairs(collection_state)
    except Exception as e:
        logger.error(f"Storage init failed: {e}")

    if settings.PRELOAD_EMBEDDING_ON_STARTUP:
        from backend.rag_embedding import _get_model
        _get_model()
    yield


app = FastAPI(title=settings.API_TITLE, version=settings.API_VERSION, description="RAG System", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _build_answer_prompt(question: str, context: str = "") -> str:
    if not context:
        return question
    return (
        "You are a retrieval-grounded academic assistant for machine learning course notes.\n"
        "Answer using the context first. If the context is partial but relevant, synthesize the best answer from it instead of refusing.\n"
        "Keep the answer concise: 4-6 lines. Do not use markdown styling. Cite evidence as [Source: <file>, Page: <n>].\n\n"
        f"CONTEXT:\n{context}\n\nQUESTION: {question}\n\nANSWER:"
    )

def _generate_with_openai_compatible(prompt: str) -> tuple[str, str | None]:
    if not settings.OPENAI_API_KEY:
        return "OpenAI-compatible API key is not configured.", "missing_openai_api_key"
    try:
        api_base = _normalize_openai_base_url(settings.OPENAI_BASE_URL)
        headers = {
            "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": settings.OPENAI_USER_AGENT,
        }
        payload = {
            "model": settings.LLM_MODEL or settings.CLAUDE_MODEL,
            "temperature": 0,
            "max_tokens": settings.CLAUDE_MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        }
        with httpx.Client(timeout=30.0) as client:
            response = client.post(f"{api_base}/chat/completions", json=payload, headers=headers)
        if response.status_code != 200:
            return "OpenAI-compatible LLM service returned an error.", f"openai_http_{response.status_code}"
        data = response.json()
        return data["choices"][0]["message"]["content"], None
    except Exception as exc:
        logger.warning("OpenAI-compatible LLM request failed: %s", exc)
        return "OpenAI-compatible LLM service is temporarily unavailable.", str(exc)

def generate_with_claude(question: str, context: str = "") -> tuple[str, str | None]:
    prompt = _build_answer_prompt(question, context)

    if not settings.ANTHROPIC_API_KEY:
        return _generate_with_openai_compatible(prompt)

    try:
        headers = {
            "x-api-key": settings.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {"model": settings.CLAUDE_MODEL, "max_tokens": settings.CLAUDE_MAX_TOKENS, "messages": [{"role": "user", "content": prompt}]}
        api_base = _normalize_anthropic_base_url(settings.ANTHROPIC_BASE_URL)
        with httpx.Client(timeout=30.0) as client:
            response = client.post(f"{api_base}/v1/messages", json=payload, headers=headers)
        if response.status_code != 200:
            fallback_answer, fallback_error = _generate_with_openai_compatible(prompt)
            return fallback_answer, fallback_error or f"anthropic_http_{response.status_code}"
        return response.json()["content"][0]["text"], None
    except Exception as e:
        logger.warning("LLM request failed: %s", e)
        fallback_answer, fallback_error = _generate_with_openai_compatible(prompt)
        return fallback_answer, fallback_error or str(e)


@app.get("/")
def root():
    return {"service": settings.API_TITLE, "version": settings.API_VERSION, "docs": "/docs"}


@app.get("/health")
def health():
    components = {
        "postgres": "ok",
        "redis": "ok",
        "qdrant": "ok",
        "minio": "ok",
    }
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception:
        components["postgres"] = "error"
    try:
        redis_client.ping()
    except Exception:
        components["redis"] = "error"
    try:
        qdrant.client.get_collections()
    except Exception:
        components["qdrant"] = "error"
    try:
        minio.client.bucket_exists(settings.MINIO_BUCKET)
    except Exception:
        components["minio"] = "error"
    overall = "healthy" if all(value == "ok" for value in components.values()) else "degraded"
    return {
        "status": overall,
        "components": components,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/metrics")
def metrics(db: Session = Depends(get_db)):
    total_docs = db.query(Document).count()
    completed_docs = db.query(Document).filter(Document.status == "completed").count()
    failed_docs = db.query(Document).filter(Document.status == "failed").count()
    total_conversations = db.query(Conversation).count()
    body = (
        "# TYPE rag_documents_total gauge\n"
        f"rag_documents_total {total_docs}\n"
        "# TYPE rag_documents_completed gauge\n"
        f"rag_documents_completed {completed_docs}\n"
        "# TYPE rag_documents_failed gauge\n"
        f"rag_documents_failed {failed_docs}\n"
        "# TYPE rag_conversations_total gauge\n"
        f"rag_conversations_total {total_conversations}\n"
    )
    return StreamingResponse(iter([body]), media_type="text/plain; version=0.0.4")


@app.post("/api/auth/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form_data.username, User.is_active.is_(True)).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_access_token(subject=user.username, role=user.role)
    return {"access_token": token, "token_type": "bearer", "role": user.role, "username": user.username}


@app.get("/api/auth/me")
def me(current_user: User = Depends(get_current_user)):
    return {"id": current_user.id, "username": current_user.username, "role": current_user.role}


@app.post("/api/admin/users")
def create_user(req: CreateUserRequest, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    if req.role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="Role must be admin or user")
    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(status_code=409, detail="Username already exists")
    user = User(username=req.username, password_hash=hash_password(req.password), role=req.role, is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "username": user.username, "role": user.role, "is_active": user.is_active}


@app.get("/api/admin/users")
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    users = db.query(User).order_by(User.created_at.desc()).all()
    return {"total": len(users), "users": [{"id": u.id, "username": u.username, "role": u.role, "is_active": u.is_active} for u in users]}


@app.patch("/api/admin/users/{user_id}")
def update_user(user_id: int, req: UpdateUserRequest, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if req.role is not None:
        if req.role not in ("admin", "user"):
            raise HTTPException(status_code=400, detail="Role must be admin or user")
        if user.username == admin.username and req.role != "admin":
            raise HTTPException(status_code=400, detail="Cannot remove your own admin role")
        user.role = req.role
    if req.is_active is not None:
        if user.username == admin.username and not req.is_active:
            raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
        user.is_active = req.is_active
    if req.password:
        user.password_hash = hash_password(req.password)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "username": user.username, "role": user.role, "is_active": user.is_active}

@app.delete("/api/admin/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.username == admin.username:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    db.delete(user)
    db.commit()
    return {"success": True}


@app.get("/api/admin/config")
def list_config(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    rows = db.query(SystemConfig).order_by(SystemConfig.key.asc()).all()
    return {"total": len(rows), "items": [{"key": r.key, "value": r.value, "updated_at": r.updated_at.isoformat() if r.updated_at else None} for r in rows]}


@app.post("/api/admin/config")
def set_config(req: SetConfigRequest, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    row = _save_system_config(db, req.key, req.value)
    if req.key in RAG_SETTING_KEYS:
        delete_prefix("ask")
        delete_prefix("search")
    return {"success": True, "key": row.key, "value": row.value}


@app.get("/api/admin/rag-settings")
def get_rag_settings(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return {
        "settings": _effective_rag_settings(db),
        "restart_required_for": ["EMBEDDING_PROVIDER", "EMBEDDING_MODEL", "EMBEDDING_DIM"],
        "reindex_required_for": [
            "CHUNK_SIZE",
            "CHUNK_OVERLAP",
            "RAG_CHUNK_MIN_CHARS",
            "RAG_DEDUP_CHUNKS",
            "OCR_ENABLED",
            "OCR_LANGS",
            "OCR_DPI",
            "OCR_MIN_TEXT_CHARS",
            "EMBEDDING_PROVIDER",
            "EMBEDDING_MODEL",
            "EMBEDDING_DIM",
        ],
    }

@app.patch("/api/admin/rag-settings")
def update_rag_settings(req: RagSettingsRequest, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    current = _effective_rag_settings(db)
    payload = req.model_dump(exclude_none=True)
    if "CHUNK_SIZE" in payload and payload["CHUNK_SIZE"] < 50:
        raise HTTPException(status_code=400, detail="CHUNK_SIZE must be at least 50")
    if "CHUNK_OVERLAP" in payload and payload["CHUNK_OVERLAP"] < 0:
        raise HTTPException(status_code=400, detail="CHUNK_OVERLAP cannot be negative")
    if "RAG_CHUNK_MIN_CHARS" in payload and payload["RAG_CHUNK_MIN_CHARS"] < 40:
        raise HTTPException(status_code=400, detail="RAG_CHUNK_MIN_CHARS must be at least 40")
    if "OCR_DPI" in payload and payload["OCR_DPI"] < 100:
        raise HTTPException(status_code=400, detail="OCR_DPI must be at least 100")
    if "OCR_MIN_TEXT_CHARS" in payload and payload["OCR_MIN_TEXT_CHARS"] < 1:
        raise HTTPException(status_code=400, detail="OCR_MIN_TEXT_CHARS must be positive")
    next_size = int(payload.get("CHUNK_SIZE", current["CHUNK_SIZE"]))
    next_overlap = int(payload.get("CHUNK_OVERLAP", current["CHUNK_OVERLAP"]))
    if next_overlap >= next_size:
        raise HTTPException(status_code=400, detail="CHUNK_OVERLAP must be smaller than CHUNK_SIZE")

    changed = []
    for key, value in payload.items():
        if key not in RAG_SETTING_KEYS:
            continue
        _save_system_config(db, key, str(value))
        changed.append(key)
    if changed:
        delete_prefix("ask")
        delete_prefix("search")

    return {
        "success": True,
        "changed": changed,
        "settings": _effective_rag_settings(db),
        "restart_required": any(key in {"EMBEDDING_PROVIDER", "EMBEDDING_MODEL", "EMBEDDING_DIM"} for key in changed),
        "reindex_required": any(key in {
            "CHUNK_SIZE",
            "CHUNK_OVERLAP",
            "RAG_CHUNK_MIN_CHARS",
            "RAG_DEDUP_CHUNKS",
            "OCR_ENABLED",
            "OCR_LANGS",
            "OCR_DPI",
            "OCR_MIN_TEXT_CHARS",
            "EMBEDDING_PROVIDER",
            "EMBEDDING_MODEL",
            "EMBEDDING_DIM",
        } for key in changed),
    }

@app.post("/api/documents/upload-url")
def upload_document_url(req: UploadUrlRequest, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    try:
        r = requests.get(str(req.url), timeout=30)
        r.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {e}")
    filename = str(req.url).split('/')[-1] or 'web_document.txt'
    if '.' not in filename:
        filename += '.txt'
    content = r.content
    ext = filename.rsplit('.', 1)[-1].lower()
    if ext not in settings.allowed_file_types:
        filename = filename.rsplit('.',1)[0] + '.txt'
        ext = 'txt'
    minio.upload_file(filename, content)
    effective = _effective_rag_settings(db)
    existing = db.query(Document).filter(Document.filename == filename).first()
    force_reindex = existing is not None
    if existing:
        existing.size_bytes = len(content)
        existing.uploaded_at = datetime.now(timezone.utc)
        existing.source_url = str(req.url)
        doc = existing
    else:
        doc = Document(filename=filename, size_bytes=len(content), file_type=ext, source_url=str(req.url))
        db.add(doc)
    _reset_ingest_tracking(doc, effective)
    db.commit(); db.refresh(doc)
    from worker.tasks.ingest import ingest_document
    ingest_document.delay(filename, doc.id, force_reindex)
    delete_prefix('ask'); delete_prefix('search')
    return {"success": True, "document": {"id": doc.id, "filename": doc.filename, "status": doc.status}}


@app.post("/api/documents/upload")
async def upload_document(file: UploadFile = File(...), db: Session = Depends(get_db), _: User = Depends(require_admin)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in settings.allowed_file_types:
        raise HTTPException(status_code=400, detail=f"File type .{ext} not allowed")
    file_content = await file.read()
    size_bytes = len(file_content)
    if size_bytes > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.MAX_UPLOAD_SIZE_MB} MB")
    minio.upload_file(file.filename, file_content)

    effective = _effective_rag_settings(db)
    existing = db.query(Document).filter(Document.filename == file.filename).first()
    force_reindex = existing is not None
    if existing:
        existing.size_bytes = size_bytes
        existing.uploaded_at = datetime.now(timezone.utc)
        existing.source_url = None
        doc = existing
    else:
        doc = Document(filename=file.filename, size_bytes=size_bytes, file_type=ext)
        db.add(doc)
    _reset_ingest_tracking(doc, effective)
    db.commit()
    db.refresh(doc)

    from worker.tasks.ingest import ingest_document

    ingest_document.delay(file.filename, doc.id, force_reindex)
    delete_prefix("ask"); delete_prefix("search")
    return {"success": True, "document": {"id": doc.id, "filename": doc.filename, "status": doc.status}}


@app.get("/api/documents")
def list_documents(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    docs = db.query(Document).order_by(Document.uploaded_at.desc()).all()
    return {
        "total": len(docs),
        "documents": [
            {
                "id": d.id,
                "name": d.filename,
                "size_mb": round(d.size_bytes / (1024 * 1024), 2),
                "type": d.file_type,
                "status": d.status,
                "chunks_count": d.chunks_count,
                "pages_count": d.pages_count,
                "empty_pages_count": d.empty_pages_count,
                "ocr_pages_count": d.ocr_pages_count,
                "chunks_dropped": d.chunks_dropped,
                "ingest_duration_ms": d.ingest_duration_ms,
                "embedding_model": d.embedding_model,
                "embedding_dim": d.embedding_dim,
                "chunk_size": d.chunk_size,
                "chunk_overlap": d.chunk_overlap,
                "pipeline_version": d.pipeline_version,
                "error": d.error_message,
                "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
                "processed_at": d.processed_at.isoformat() if d.processed_at else None,
            }
            for d in docs
        ],
    }


@app.delete("/api/documents/{filename}")
def delete_document(filename: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    doc = db.query(Document).filter(Document.filename == filename).first()
    if not doc:
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")
    try:
        minio.delete_file(filename)
    except Exception:
        pass
    try:
        qdrant.delete_by_source(filename)
    except Exception:
        pass
    db.delete(doc)
    db.commit()
    return {"success": True, "deleted": filename}


@app.post("/api/documents/reindex-all")
def reindex_all_documents(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    docs = db.query(Document).all()
    if not docs:
        raise HTTPException(status_code=400, detail="No documents to reindex")
    from worker.tasks.ingest import ingest_document

    effective = _effective_rag_settings(db)
    queued = []
    for doc in docs:
        _reset_ingest_tracking(doc, effective)
        queued.append(doc.filename)
    db.commit()

    for doc in docs:
        ingest_document.delay(doc.filename, doc.id, True)
    delete_prefix("ask"); delete_prefix("search")
    return {"success": True, "queued": queued, "total": len(queued)}

@app.post("/api/documents/{filename}/reindex")
def reindex_document(filename: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    doc = db.query(Document).filter(Document.filename == filename).first()
    if not doc:
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")
    effective = _effective_rag_settings(db)
    _reset_ingest_tracking(doc, effective)
    db.commit()
    from worker.tasks.ingest import ingest_document

    ingest_document.delay(filename, doc.id, True)
    delete_prefix("ask"); delete_prefix("search")
    return {"success": True, "message": "Reindex queued", "filename": filename}


@app.post("/api/search")
def search(req: SearchRequest, db: Session = Depends(get_db)):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    from backend.hybrid_search import hybrid_search

    options = _runtime_rag_options(db)
    requested_top_k = max(1, min(50, req.top_k))
    cache_key = make_key("search", {"q": req.query, "k": requested_top_k, "v": 2, "rag": options})
    cached = get_json(cache_key)
    if cached:
        return cached
    results = hybrid_search(
        req.query,
        embed_text(req.query),
        top_k=requested_top_k,
        candidate_k=max(requested_top_k, options["retrieval_top_k"]) * 6,
        use_rerank=options["use_reranker"],
    )
    payload = {"query": req.query, "total": len(results), "results": results}
    set_json(cache_key, payload, ttl_seconds=120)
    return payload


@app.post("/api/ask")
def ask(req: AskRequest, db: Session = Depends(get_db)):
    options = _runtime_rag_options(db)
    cache_key = make_key("ask", {"q": req.question, "k": req.top_k, "v": 5, "rag": options})
    cached = get_json(cache_key)
    if cached:
        return cached
    chunks = _retrieve_answer_chunks(req.question, req.top_k, options)
    context = _context_from_chunks(chunks) if chunks else ""
    answer_text, llm_error = generate_with_claude(req.question, context=context)
    answer = _clean_markdown_text(answer_text)
    if llm_error or _answer_needs_fallback(answer):
        answer = _extractive_fallback_answer(req.question, chunks)
    sources = _sources_for_answer(chunks, answer, options)
    try:
        db.add(Conversation(question=req.question, answer=answer, sources=json.dumps(sources), model_used=settings.CLAUDE_MODEL))
        db.commit()
    except Exception:
        pass
    payload = {"question": req.question, "answer": answer, "sources": sources, "timestamp": datetime.now(timezone.utc).isoformat()}
    set_json(cache_key, payload, ttl_seconds=120)
    return payload


@app.post("/api/ask/stream")
def ask_stream(req: AskRequest, db: Session = Depends(get_db)):
    options = _runtime_rag_options(db)
    chunks = _retrieve_answer_chunks(req.question, req.top_k, options)
    context = _context_from_chunks(chunks) if chunks else ""
    def event_stream():
        yield f"data: {json.dumps({'type':'start'})}\n\n"
        answer_text, llm_error = generate_with_claude(req.question, context=context)
        answer = _clean_markdown_text(answer_text)
        if llm_error or _answer_needs_fallback(answer):
            answer = _extractive_fallback_answer(req.question, chunks)
        sources = _sources_for_answer(chunks, answer, options)
        for token in answer.split():
            yield f"data: {json.dumps({'type':'token','text': token + ' '})}\n\n"
        yield f"data: {json.dumps({'type':'end','sources':sources,'timestamp':datetime.now(timezone.utc).isoformat()})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/conversations")
def list_conversations(limit: int = 20, db: Session = Depends(get_db)):
    convs = db.query(Conversation).order_by(Conversation.created_at.desc()).limit(limit).all()
    return {"total": len(convs), "conversations": [{"id": c.id, "question": c.question, "answer": c.answer, "sources": json.loads(c.sources) if c.sources else [], "model": c.model_used, "created_at": c.created_at.isoformat() if c.created_at else None} for c in convs]}


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(status_code=exc.status_code, content={"error": True, "status_code": exc.status_code, "message": exc.detail})
