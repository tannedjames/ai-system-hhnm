"""FastAPI backend with full RAG pipeline."""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.config import settings
from backend.models import Document, Conversation, User
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

    # Initialize all storage backends (run in threadpool to avoid blocking loop)
    try:
        await asyncio.to_thread(init_db)
        await asyncio.to_thread(qdrant.init_collection)
        await asyncio.to_thread(minio.init_bucket)
        logger.info("All storage backends initialized")
    except Exception as e:
        logger.error(f"Storage init failed: {e}")
        logger.warning("Some services may not be available - check Docker Compose")
    
    # Pre-load embedding model at startup
    logger.info("Pre-loading embedding model...")
    from backend.rag_embedding import _get_model
    await asyncio.to_thread(_get_model)

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
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Request models ----------
class AskRequest(BaseModel):
    question: str
    top_k: int = Field(5, ge=1, le=50)


class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(5, ge=1, le=50)


class UserCreate(BaseModel):
    username: str
    email: str = ""
    role: str = "user"


class UserOut(BaseModel):
    id: int
    username: str
    email: str
    role: str
    created_at: str | None

    class Config:
        from_attributes = True


# ---------- Claude (via TrollLLM) ----------
def generate_with_claude(question: str, context: str = "") -> str:
    """Call Claude via TrollLLM API."""
    try:
        if context:
            prompt = (
                "You are a helpful academic assistant. Answer the question using ONLY "
                "the provided context. If the context doesn't contain the answer, say so explicitly. "
                "Cite the source document and page when relevant.\n\n"
                f"CONTEXT:\n{context}\n\n"
                f"QUESTION: {question}\n\nANSWER:"
            )
        else:
            prompt = question

        headers = {
            "x-api-key": settings.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/json",
        }

        payload = {
            "model": settings.CLAUDE_MODEL,
            "max_tokens": settings.CLAUDE_MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        }

        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                f"{settings.ANTHROPIC_BASE_URL}/v1/messages",
                json=payload,
                headers=headers,
            )

        if response.status_code != 200:
            raise RuntimeError(f"Claude error ({response.status_code}): {response.text[:300]}")

        data = response.json()
        return data["content"][0]["text"]

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
    """Upload a document — saves to MinIO and queues ingest task."""
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
        # Remove old vectors before re-ingestion
        try:
            qdrant.delete_by_source(file.filename)
        except Exception as e:
            logger.warning(f"Qdrant cleanup before re-upload failed: {e}")
        # Reset for re-ingestion
        existing.status = "pending"
        existing.size_bytes = size_bytes
        existing.error_message = None
        existing.chunks_count = 0
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
        "message": "File uploaded — processing in background",
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


@app.delete("/api/documents/{filename}")
def delete_document(filename: str, db: Session = Depends(get_db)):
    """Delete a document from MinIO, PostgreSQL, and Qdrant."""
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid filename")

    doc = db.query(Document).filter(Document.filename == filename).first()
    if not doc:
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    # Delete from all 3 storage backends
    try:
        minio.delete_file(filename)
    except Exception as e:
        logger.warning(f"MinIO delete failed: {e}")

    try:
        qdrant.delete_by_source(filename)
    except Exception as e:
        logger.warning(f"Qdrant delete failed: {e}")

    try:
        from backend.bm25_search import invalidate_cache
        invalidate_cache()
    except Exception as e:
        logger.warning(f"BM25 cache invalidation failed: {e}")

    db.delete(doc)
    db.commit()

    return {"success": True, "deleted": filename}


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
@app.post("/api/ask")
def ask(req: AskRequest, db: Session = Depends(get_db)):
    """Full RAG pipeline: retrieve → build context → ask Claude → return answer + sources."""
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    # 1. Embed question
    try:
        query_vector = embed_text(req.question)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Embedding failed: {e}")

    # 2. Hybrid retrieve: dense + sparse → RRF → rerank
    try:
        from backend.hybrid_search import hybrid_search
        chunks = hybrid_search(req.question, query_vector, top_k=req.top_k)
    except Exception as e:
        logger.exception("Retrieval failed")
        chunks = []  # Fall back to no-context answer

    # 3. Build context from chunks
    if chunks:
        context = "\n\n---\n\n".join([
            f"[Source: {c['source']}, Page: {c['page']}]\n{c['text']}"
            for c in chunks
        ])
    else:
        context = ""

    # 4. Generate answer with Claude
    answer = generate_with_claude(req.question, context=context)

    # 5. Format sources
    sources = [
        {
            "file": c["source"],
            "page": c["page"],
            "chunk_index": c["chunk_index"],
            "relevance_score": round(c["score"], 3),
            "preview": c["text"][:200] + "..." if len(c["text"]) > 200 else c["text"],
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
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------- Users ----------
@app.get("/api/users")
def list_users(db: Session = Depends(get_db)):
    """List all system users."""
    users = db.query(User).order_by(User.created_at.desc()).all()
    return {
        "total": len(users),
        "users": [
            {
                "id": u.id,
                "username": u.username,
                "email": u.email or "",
                "role": u.role,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ],
    }


@app.post("/api/users")
def create_user(req: UserCreate, db: Session = Depends(get_db)):
    """Create a new user."""
    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(status_code=409, detail="Username already exists")
    user = User(username=req.username, email=req.email or None, role=req.role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email or "",
        "role": user.role,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@app.delete("/api/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db)):
    """Delete a user."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(user)
    db.commit()
    return {"success": True, "deleted": user.username}


# ---------- Conversations ----------
@app.get("/api/conversations")
def list_conversations(limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
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


@app.exception_handler(Exception)
async def generic_exception_handler(request, exc):
    logger.exception("Unhandled exception")
    return JSONResponse(
        status_code=500,
        content={"error": True, "status_code": 500, "message": "Internal server error"},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host=settings.BACKEND_HOST,
        port=settings.BACKEND_PORT,
        reload=settings.ENVIRONMENT == "development",
    )
