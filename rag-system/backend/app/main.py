"""
RAG System — FastAPI Entry Point
=================================
Barebones application with a /health endpoint that validates connectivity
to all infrastructure services: PostgreSQL, MinIO, Qdrant, and Redis.

Run locally:
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings


# =============================================================================
# Lifespan — startup / shutdown hooks
# =============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs on app startup and shutdown."""
    print(f"--> {settings.APP_NAME} starting up ...")
    yield
    print(f"<-- {settings.APP_NAME} shutting down ...")


# =============================================================================
# FastAPI Application
# =============================================================================
app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    description="RAG Knowledge Base System — Infrastructure Health Check",
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


# =============================================================================
# API Routes
# =============================================================================
@app.get("/", tags=["Root"])
async def root():
    """Root endpoint — basic info."""
    return {
        "app": settings.APP_NAME,
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health",
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

    # Overall status: healthy only if ALL services are healthy
    all_healthy = all(
        svc["status"] == "healthy" for svc in results.values()
    )

    return {
        "status": "healthy" if all_healthy else "degraded",
        "services": results,
    }
