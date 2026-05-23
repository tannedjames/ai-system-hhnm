"""FastAPI backend with full RAG pipeline."""
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.config import settings
from backend.models import Document, Conversation
from backend.storage.database import init_db, get_db
from backend.storage import qdrant_client as qdrant
from backend.storage import minio_client as minio
from backend.rag_embedding import embed_text

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("rag-backend")


# ---------- Lifespan ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting RAG backend")
    logger.info(f"Environment   : {settings.ENVIRONMENT}")
    logger.info(f"Claude model  : {settings.CLAUDE_MODEL}")

    # Initialize all storage backends
    try:
        init_db()
        qdrant.init_collection()
        minio.init_bucket()
        logger.info("All storage backends initialized")
    except Exception as e:
        logger.error(f"Storage init failed: {e}")
        logger.warning("Some services may not be available - check Docker Compose")

    # Pre-load embedding model at startup
    logger.info("Pre-loading embedding model...")
    from backend.rag_embedding import _get_model
    _get_model()

    yield
    logger.info("Shutting down RAG backend")


# ---------- App ----------
app = FastAPI(
    title=settings.API_TITLE,
    version=settings.API_VERSION,
    description="RAG System for Academic Document Retrieval",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # Per the CORS spec, allow_credentials=True is incompatible with the "*"
    # wildcard origin - browsers will reject the preflight. We don't actually
    # use credentialed requests, so set this to False.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Request models ----------
class AskRequest(BaseModel):
    question: str
    top_k: int = 8  # bigger default so synthesis questions get enough context


class SearchRequest(BaseModel):
    query: str
    top_k: int = 8


# ---------- Claude (via TrollLLM) ----------
SYNTHESIS_SYSTEM_PROMPT = (
    "You are a careful academic assistant answering questions about a set of "
    "uploaded documents. Use ONLY the information in the provided context. "
    "When the question is broad (asks for a summary, overview, comparison, or "
    "main themes), synthesize across ALL the context excerpts rather than "
    "answering from a single passage. If you draw from multiple documents, say so. "
    "Cite the source file and page in inline parentheses, e.g. (paper.pdf, p.3). "
    "If the context genuinely does not contain enough information to answer, say so "
    "explicitly and explain what is missing - do not invent facts.\n"
    "Respond in the same language as the question."
)


def generate_with_claude(question: str, context: str = "", system: Optional[str] = None) -> str:
    """Call Claude via TrollLLM API."""
    try:
        if context:
            user_msg = (
                f"CONTEXT (excerpts retrieved from the user's documents):\n{context}\n\n"
                f"QUESTION: {question}\n\nANSWER:"
            )
        else:
            user_msg = question

        headers = {
            "x-api-key": settings.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/json",
        }

        payload = {
            "model": settings.CLAUDE_MODEL,
            "max_tokens": settings.CLAUDE_MAX_TOKENS,
            "messages": [{"role": "user", "content": user_msg}],
        }
        if system:
            payload["system"] = system

        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                f"{settings.ANTHROPIC_BASE_URL}/v1/messages",
                json=payload,
                headers=headers,
            )

        if response.status_code != 200:
            raise RuntimeError(f"Claude error ({response.status_code}): {response.text[:300]}")

        data = response.json()
        if isinstance(data, dict) and "content" in data and data["content"]:
            return data["content"][0].get("text", "")
        if isinstance(data, dict) and "completion" in data:
            return data["completion"]
        raise RuntimeError(f"Unexpected Claude response shape: {str(data)[:300]}")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Claude call failed")
        raise HTTPException(status_code=502, detail=f"Claude error: {e}")


# ---------- Health ----------
@app.get("/")
def root():
    return {
        "service": settings.API_TITLE,
        "version": settings.API_VERSION,
        "docs": "/docs",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "environment": settings.ENVIRONMENT,
        "claude_model": settings.CLAUDE_MODEL,
    }


@app.post("/api/test-claude")
def test_claude():
    """Verify Claude API connection."""
    try:
        text = generate_with_claude("Reply with exactly: OK")
        return {"success": True, "model": settings.CLAUDE_MODEL, "response": text.strip()}
    except HTTPException as e:
        return {"success": False, "error": e.detail}


# ---------- Documents ----------
@app.post("/api/documents/upload")
async def upload_document(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload a document - saves to MinIO and queues ingest task."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in settings.allowed_file_types:
        raise HTTPException(
            status_code=400,
            detail=f"File type .{ext} not allowed. Allowed: {settings.allowed_file_types}",
        )

    # Read file content
    file_content = await file.read()
    size_bytes = len(file_content)
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    if size_bytes > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {settings.MAX_UPLOAD_SIZE_MB} MB",
        )

    # 1. Upload to MinIO
    try:
        minio.upload_file(file.filename, file_content)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"MinIO upload failed: {e}")

    # 2. Check if doc already exists in DB
    existing = db.query(Document).filter(Document.filename == file.filename).first()
    if existing:
        # Reset for re-ingestion - wipe old chunks so the new version fully replaces the old one
        try:
            qdrant.delete_by_source(file.filename)
        except Exception as e:
            logger.warning(f"Qdrant cleanup before re-ingest failed: {e}")
        try:
            from backend.bm25_search import invalidate_cache
            invalidate_cache()
        except Exception as e:
            logger.warning(f"BM25 invalidate before re-ingest failed: {e}")

        existing.status = "pending"
        existing.size_bytes = size_bytes
        existing.error_message = None
        existing.chunks_count = 0
        existing.processed_at = None
        existing.uploaded_at = datetime.now(timezone.utc)
        doc = existing
    else:
        # 3. Create document record
        doc = Document(
            filename=file.filename,
            size_bytes=size_bytes,
            file_type=ext,
            status="pending",
        )
        db.add(doc)

    db.commit()
    db.refresh(doc)

    # 4. Queue Celery ingest task
    try:
        from worker.tasks.ingest import ingest_document
        ingest_document.delay(file.filename, doc.id)
        logger.info(f"Queued ingest task for: {file.filename}")
    except Exception as e:
        logger.warning(f"Could not queue Celery task (worker not running?): {e}")

    return {
        "success": True,
        "file": {
            "id": doc.id,
            "name": file.filename,
            "size_mb": round(size_bytes / (1024 * 1024), 2),
            "type": ext,
            "status": doc.status,
            "uploaded_at": doc.uploaded_at.isoformat(),
        },
        "message": "File uploaded - processing in background",
    }


@app.get("/api/documents")
def list_documents(db: Session = Depends(get_db)):
    """List all uploaded documents with their ingest status."""
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
                "error": d.error_message,
                "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
                "processed_at": d.processed_at.isoformat() if d.processed_at else None,
            }
            for d in docs
        ],
    }


@app.delete("/api/documents/{filename:path}")
def delete_document(filename: str, db: Session = Depends(get_db)):
    """Delete a document from MinIO, Qdrant, and PostgreSQL."""
    # Basic path-traversal protection (filename can still contain spaces/unicode)
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid filename")

    doc = db.query(Document).filter(Document.filename == filename).first()
    if not doc:
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    errors = []

    # 1. Delete chunks from Qdrant FIRST so any in-flight queries stop citing this doc
    try:
        qdrant.delete_by_source(filename)
    except Exception as e:
        logger.warning(f"Qdrant delete failed for {filename}: {e}")
        errors.append(f"qdrant: {e}")

    # 2. Delete the file from MinIO
    try:
        minio.delete_file(filename)
    except Exception as e:
        logger.warning(f"MinIO delete failed for {filename}: {e}")
        errors.append(f"minio: {e}")

    # 3. Invalidate BM25 in-memory index so deleted chunks stop appearing
    try:
        from backend.bm25_search import invalidate_cache
        invalidate_cache()
    except Exception as e:
        logger.warning(f"BM25 cache invalidation failed: {e}")
        errors.append(f"bm25: {e}")

    # 4. Drop the SQL row last
    db.delete(doc)
    db.commit()

    logger.info(f"Deleted document {filename} (errors: {errors or 'none'})")
    return {"success": True, "deleted": filename, "warnings": errors}


# ---------- RAG: Search ----------
@app.post("/api/search")
def search(req: SearchRequest):
    """Semantic search over uploaded documents."""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    try:
        from backend.hybrid_search import hybrid_search
        query_vector = embed_text(req.query)
        results = hybrid_search(req.query, query_vector, top_k=req.top_k)
    except Exception as e:
        logger.exception("Search failed")
        raise HTTPException(status_code=502, detail=f"Search failed: {e}")

    return {
        "query": req.query,
        "total": len(results),
        "results": results,
    }


# ---------- RAG: Ask (full pipeline) ----------
def _format_page_ref(c: dict) -> str:
    """Format a page reference, collapsing page == page_end to a single number."""
    p = c.get("page", 0)
    pe = c.get("page_end", p) or p
    if pe and pe != p:
        return f"p.{p}-{pe}"
    if p:
        return f"p.{p}"
    return ""


@app.post("/api/ask")
def ask(req: AskRequest, db: Session = Depends(get_db)):
    """Full RAG pipeline: retrieve -> build context -> ask Claude -> return answer + sources."""
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    # 1. Embed question
    try:
        query_vector = embed_text(req.question)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Embedding failed: {e}")

    # 2. Hybrid retrieve: dense + sparse -> RRF -> rerank -> diversify.
    # hybrid_search will widen the window automatically for broad/synthesis questions.
    requested_top_k = max(1, min(req.top_k or settings.RETRIEVAL_TOP_K, 25))
    try:
        from backend.hybrid_search import hybrid_search, _looks_broad
        chunks = hybrid_search(req.question, query_vector, top_k=requested_top_k)
    except Exception as e:
        logger.exception("Retrieval failed")
        chunks = []  # Fall back to no-context answer

    # 3. Build context from chunks (dedupe in case the same chunk slipped through)
    seen_keys = set()
    deduped = []
    for c in chunks:
        key = f"{c.get('source')}::{c.get('chunk_index')}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(c)
    chunks = deduped

    if chunks:
        context_parts = []
        for c in chunks:
            page_ref = _format_page_ref(c)
            header = f"[Source: {c['source']}" + (f", {page_ref}" if page_ref else "") + "]"
            context_parts.append(f"{header}\n{c['text']}")
        context = "\n\n---\n\n".join(context_parts)
    else:
        context = ""

    # 4. Generate answer with Claude
    try:
        is_broad = _looks_broad(req.question)
    except Exception:
        is_broad = False
    answer = generate_with_claude(
        req.question,
        context=context,
        system=SYNTHESIS_SYSTEM_PROMPT,
    )

    # 5. Format sources
    sources = [
        {
            "file": c["source"],
            "page": c.get("page", 0),
            "page_end": c.get("page_end", c.get("page", 0)),
            "chunk_index": c["chunk_index"],
            "relevance_score": round(c["score"], 3),
            "preview": c["text"][:240] + "..." if len(c["text"]) > 240 else c["text"],
        }
        for c in chunks
    ]

    # 6. Save conversation to DB
    try:
        conv = Conversation(
            question=req.question,
            answer=answer,
            sources=json.dumps(sources),
            model_used=settings.CLAUDE_MODEL,
        )
        db.add(conv)
        db.commit()
    except Exception as e:
        logger.warning(f"Could not save conversation: {e}")

    return {
        "question": req.question,
        "answer": answer,
        "sources": sources,
        "model": settings.CLAUDE_MODEL,
        "context_used": bool(chunks),
        "is_broad_question": is_broad,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------- Conversations ----------
@app.get("/api/conversations")
def list_conversations(limit: int = 20, db: Session = Depends(get_db)):
    """List recent Q&A history."""
    convs = (
        db.query(Conversation)
        .order_by(Conversation.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "total": len(convs),
        "conversations": [
            {
                "id": c.id,
                "question": c.question,
                "answer": c.answer,
                "sources": json.loads(c.sources) if c.sources else [],
                "model": c.model_used,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in convs
        ],
    }


# ---------- Error handler ----------
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": True, "status_code": exc.status_code, "message": exc.detail},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host=settings.BACKEND_HOST,
        port=settings.BACKEND_PORT,
        reload=settings.ENVIRONMENT == "development",
    )
