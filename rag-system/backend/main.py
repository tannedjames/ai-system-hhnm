"""FastAPI backend with TrollLLM (Claude proxy) integration."""
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.config import settings

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("rag-backend")

# TrollLLM endpoint
TROLLLLM_BASE_URL = settings.ANTHROPIC_BASE_URL


# ---------- Lifespan ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting RAG backend")
    logger.info(f"Environment   : {settings.ENVIRONMENT}")
    logger.info(f"Claude model  : {settings.CLAUDE_MODEL}")
    logger.info(f"Upload dir    : {settings.UPLOAD_DIR}")
    logger.info(f"Vector DB     : {settings.CHROMA_DB_PATH}")
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
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Models ----------
class AskRequest(BaseModel):
    question: str
    top_k: int = 3


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5


# ---------- TrollLLM helper ----------
def _build_prompt(question: str, context: str = "") -> str:
    if not context:
        return question
    return (
        "Answer the question using ONLY the provided context. "
        "If the context does not contain the answer, say so explicitly.\n\n"
        f"CONTEXT:\n{context}\n\n"
        f"QUESTION: {question}\n\nANSWER:"
    )


def generate_with_claude(question: str, context: str = "") -> str:
    """Call Claude via TrollLLM API."""
    try:
        prompt = _build_prompt(question, context)
        
        headers = {
            "x-api-key": settings.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": settings.CLAUDE_MODEL,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}]
        }
        
        logger.info(f"Calling TrollLLM: {settings.ANTHROPIC_BASE_URL}/v1/messages")
        logger.info(f"Model: {settings.CLAUDE_MODEL}")
        logger.info(f"Payload: {payload}")
        
        with httpx.Client(timeout=15.0) as client:
            response = client.post(
                f"{TROLLLLM_BASE_URL}/v1/messages",
                json=payload,
                headers=headers
            )
            
            logger.info(f"Response status: {response.status_code}")
            logger.info(f"Response body: {response.text[:500]}")
        
        if response.status_code != 200:
            raise RuntimeError(f"TrollLLM error ({response.status_code}): {response.text[:200]}")
        
        data = response.json()
        return data["content"][0]["text"]
    
    except Exception as e:
        logger.error(f"Claude call failed: {e}")
        raise HTTPException(status_code=502, detail=f"Claude error: {e}") from e
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


# ---------- Claude test ----------
@app.post("/api/test-claude")
def test_claude():
    """Verify the Claude API key works end-to-end."""
    try:
        text = generate_with_claude("Reply with exactly: OK")
        return {
            "success": True,
            "model": settings.CLAUDE_MODEL,
            "response": text.strip(),
        }
    except HTTPException as e:
        return {"success": False, "error": e.detail}


# ---------- Documents ----------
@app.post("/api/documents/upload")
async def upload_document(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in settings.allowed_file_types:
        raise HTTPException(
            status_code=400,
            detail=f"File type .{ext} not allowed. Allowed: {settings.allowed_file_types}",
        )

    dest = os.path.join(settings.UPLOAD_DIR, file.filename)
    size_bytes = 0
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    with open(dest, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            size_bytes += len(chunk)
            if size_bytes > max_bytes:
                f.close()
                os.remove(dest)
                raise HTTPException(
                    status_code=413,
                    detail=f"File exceeds {settings.MAX_UPLOAD_SIZE_MB} MB",
                )
            f.write(chunk)

    size_mb = round(size_bytes / (1024 * 1024), 2)
    logger.info(f"Uploaded {file.filename} ({size_mb} MB)")

    return {
        "success": True,
        "file": {
            "name": file.filename,
            "size_mb": size_mb,
            "type": ext,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        },
    }


@app.get("/api/documents")
def list_documents():
    if not os.path.isdir(settings.UPLOAD_DIR):
        return {"total": 0, "documents": []}

    docs = []
    for name in sorted(os.listdir(settings.UPLOAD_DIR)):
        path = os.path.join(settings.UPLOAD_DIR, name)
        if not os.path.isfile(path) or name.startswith("."):
            continue
        stat = os.stat(path)
        docs.append({
            "name": name,
            "size_mb": round(stat.st_size / (1024 * 1024), 2),
            "type": name.rsplit(".", 1)[-1].lower() if "." in name else "",
        })
    return {"total": len(docs), "documents": docs}


@app.delete("/api/documents/{filename}")
def delete_document(filename: str):
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid filename")

    path = os.path.join(settings.UPLOAD_DIR, filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    os.remove(path)
    return {"success": True, "deleted": filename}


# ---------- Q&A ----------
@app.post("/api/ask")
def ask(req: AskRequest):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    answer = generate_with_claude(req.question)

    return {
        "question": req.question,
        "answer": answer,
        "model": settings.CLAUDE_MODEL,
        "sources": [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/search")
def search(req: SearchRequest):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    return {
        "query": req.query,
        "results": [],
        "note": "Vector search arrives in Phase 2",
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