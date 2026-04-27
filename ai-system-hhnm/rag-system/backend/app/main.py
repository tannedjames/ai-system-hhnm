"""
RAG System — FastAPI Entry Point (API Gateway)
=================================================
Gateway application that routes requests to microservices:
  - /health          → infrastructure health checks
  - /api/query       → RAG Pipeline Service (:8003)
  - /api/upload      → Upload to MinIO + trigger Celery ingest
  - /api/query/stream → Streaming RAG response

Run locally:
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""

import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.config import settings


# =============================================================================
# HTTP client for microservice communication
# =============================================================================
_http_client: httpx.AsyncClient = None


# =============================================================================
# Lifespan — startup / shutdown hooks
# =============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs on app startup and shutdown."""
    global _http_client
    print(f"--> {settings.APP_NAME} starting up ...")
    _http_client = httpx.AsyncClient(timeout=120.0)
    yield
    print(f"<-- {settings.APP_NAME} shutting down ...")
    await _http_client.aclose()


# =============================================================================
# FastAPI Application
# =============================================================================
app = FastAPI(
    title=settings.APP_NAME,
    version="0.2.0",
    description="RAG Knowledge Base System — API Gateway",
    lifespan=lifespan,
)

# Allow all origins for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# Health-check helpers
# =============================================================================
def _check_postgres() -> dict:
    """Test PostgreSQL connectivity using SQLAlchemy."""
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version();"))
            version = result.scalar()
        engine.dispose()
        return {"status": "healthy", "version": version}
    except Exception as exc:
        return {"status": "unhealthy", "error": str(exc)}


def _check_redis() -> dict:
    """Test Redis connectivity."""
    try:
        import redis as redis_lib

        r = redis_lib.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD,
            socket_connect_timeout=5,
        )
        pong = r.ping()
        info = r.info("server")
        r.close()
        return {
            "status": "healthy" if pong else "unhealthy",
            "version": info.get("redis_version", "unknown"),
        }
    except Exception as exc:
        return {"status": "unhealthy", "error": str(exc)}


def _check_minio() -> dict:
    """Test MinIO connectivity and ensure the default bucket exists."""
    try:
        from minio import Minio

        client = Minio(
            endpoint=settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ROOT_USER,
            secret_key=settings.MINIO_ROOT_PASSWORD,
            secure=settings.MINIO_SECURE,
        )
        # List buckets to verify connection
        buckets = [b.name for b in client.list_buckets()]

        # Auto-create the default bucket if it doesn't exist
        bucket = settings.MINIO_BUCKET_NAME
        if bucket not in buckets:
            client.make_bucket(bucket)
            buckets.append(bucket)

        return {
            "status": "healthy",
            "buckets": buckets,
        }
    except Exception as exc:
        return {"status": "unhealthy", "error": str(exc)}


def _check_qdrant() -> dict:
    """Test Qdrant connectivity."""
    try:
        from qdrant_client import QdrantClient

        client = QdrantClient(
            host=settings.QDRANT_HOST,
            port=settings.QDRANT_PORT,
            timeout=5,
        )
        # Get cluster info to validate connection
        info = client.get_collections()
        collections = [c.name for c in info.collections]
        client.close()
        return {
            "status": "healthy",
            "collections": collections,
        }
    except Exception as exc:
        return {"status": "unhealthy", "error": str(exc)}


async def _check_service(name: str, url: str) -> dict:
    """Check if a microservice is healthy."""
    try:
        resp = await _http_client.get(f"{url}/health", timeout=5.0)
        if resp.status_code == 200:
            return {"status": "healthy", "url": url}
        return {"status": "unhealthy", "url": url, "code": resp.status_code}
    except Exception as exc:
        return {"status": "unreachable", "url": url, "error": str(exc)}


# =============================================================================
# Request / Response schemas
# =============================================================================
class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, description="User's question")
    retrieval_top_k: int = Field(20, ge=1, le=100)
    rerank_top_k: int = Field(5, ge=1, le=50)


class UploadResponse(BaseModel):
    document_id: str
    filename: str
    file_size: int
    status: str
    message: str


# =============================================================================
# API Routes
# =============================================================================
@app.get("/", tags=["Root"])
async def root():
    """Root endpoint — basic info."""
    return {
        "app": settings.APP_NAME,
        "version": "0.2.0",
        "docs": "/docs",
        "health": "/health",
        "endpoints": {
            "query": "POST /api/query",
            "query_stream": "POST /api/query/stream",
            "upload": "POST /api/upload",
        },
    }


@app.get("/health", tags=["Health"])
async def health_check():
    """
    Comprehensive health endpoint.
    Tests connectivity to every infrastructure service and returns
    individual + overall status.
    """
    results = {
        "postgres": _check_postgres(),
        "redis": _check_redis(),
        "minio": _check_minio(),
        "qdrant": _check_qdrant(),
    }

    # Check microservices
    results["embedding_service"] = await _check_service(
        "embedding", settings.EMBEDDING_SERVICE_URL
    )
    results["reranker_service"] = await _check_service(
        "reranker", settings.RERANKER_SERVICE_URL
    )
    results["rag_service"] = await _check_service(
        "rag", settings.RAG_SERVICE_URL
    )

    # Overall status: healthy only if ALL infrastructure services are healthy
    infra_healthy = all(
        results[svc]["status"] == "healthy"
        for svc in ["postgres", "redis", "minio", "qdrant"]
    )

    return {
        "status": "healthy" if infra_healthy else "degraded",
        "services": results,
    }


@app.post("/api/query", tags=["RAG"])
async def api_query(req: QueryRequest):
    """
    Proxy query to the RAG Pipeline Service.
    Returns the full answer with sources.
    """
    try:
        resp = await _http_client.post(
            f"{settings.RAG_SERVICE_URL}/query",
            json=req.model_dump(),
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail="RAG Pipeline Service is unreachable. Is it running on :8003?",
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/query/stream", tags=["RAG"])
async def api_query_stream(req: QueryRequest):
    """
    Proxy streaming query to the RAG Pipeline Service.
    Returns Server-Sent Events.
    """
    try:
        # Use streaming request to RAG service
        rag_req = _http_client.build_request(
            "POST",
            f"{settings.RAG_SERVICE_URL}/query/stream",
            json=req.model_dump(),
        )
        rag_resp = await _http_client.send(rag_req, stream=True)

        async def stream_proxy():
            try:
                async for line in rag_resp.aiter_lines():
                    yield line + "\n"
            finally:
                await rag_resp.aclose()

        return StreamingResponse(
            stream_proxy(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail="RAG Pipeline Service is unreachable.",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/upload", response_model=UploadResponse, tags=["Documents"])
async def api_upload(file: UploadFile = File(...)):
    """
    Upload a document to MinIO and trigger async ingestion.
    Supported formats: PDF, DOCX, TXT.
    """
    # Validate file type
    allowed_extensions = {"pdf", "docx", "txt", "md"}
    ext = file.filename.rsplit(".", 1)[-1].lower() if file.filename else ""
    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: .{ext}. Allowed: {allowed_extensions}",
        )

    try:
        # Read file content
        content = await file.read()
        file_size = len(content)

        # Generate document ID
        document_id = str(uuid.uuid4())
        minio_path = f"uploads/{document_id}/{file.filename}"

        # Upload to MinIO
        from minio import Minio
        import io

        minio_client = Minio(
            endpoint=settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ROOT_USER,
            secret_key=settings.MINIO_ROOT_PASSWORD,
            secure=settings.MINIO_SECURE,
        )

        # Ensure bucket exists
        if not minio_client.bucket_exists(settings.MINIO_BUCKET_NAME):
            minio_client.make_bucket(settings.MINIO_BUCKET_NAME)

        minio_client.put_object(
            bucket_name=settings.MINIO_BUCKET_NAME,
            object_name=minio_path,
            data=io.BytesIO(content),
            length=file_size,
            content_type=file.content_type or "application/octet-stream",
        )

        # Insert document record into PostgreSQL
        from sqlalchemy import create_engine, text

        engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO documents (id, filename, file_type, file_size_bytes, "
                    "minio_path, status) VALUES (:id, :filename, :file_type, "
                    ":file_size, :minio_path, 'pending')"
                ),
                {
                    "id": document_id,
                    "filename": file.filename,
                    "file_type": ext,
                    "file_size": file_size,
                    "minio_path": minio_path,
                },
            )
            conn.commit()
        engine.dispose()

        # Trigger Celery ingest task
        from celery import Celery

        celery_client = Celery(
            broker=settings.CELERY_BROKER_URL,
            backend=settings.CELERY_RESULT_BACKEND,
        )
        celery_client.send_task(
            "tasks.ingest_document",
            args=[document_id, minio_path, file.filename],
        )

        return UploadResponse(
            document_id=document_id,
            filename=file.filename,
            file_size=file_size,
            status="pending",
            message="Document uploaded. Ingestion started in background.",
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")
