"""Celery task: ingest pipeline (extract → chunk → embed → store)."""
import logging
from datetime import datetime, timezone

from celery import Celery
from sqlalchemy.orm import Session

from backend.config import settings
from backend.models import Document
from backend.storage.database import SessionLocal
from backend.storage import minio_client as minio
from backend.storage import qdrant_client as qdrant
from backend.rag_extraction import extract_text
from backend.rag_chunking import chunk_text as chunk_simple_text, chunk_pdf_pages
from backend.rag_embedding import embed_batch

logger = logging.getLogger(__name__)

# Celery app
celery_app = Celery(
    "rag_worker",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
)


def _update_status(db: Session, doc_id: int, status: str, error: str = None, chunks_count: int = 0):
    """Update document status in PostgreSQL."""
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if doc:
        doc.status = status
        if error:
            doc.error_message = error
        if chunks_count:
            doc.chunks_count = chunks_count
        if status in ("completed", "failed"):
            doc.processed_at = datetime.now(timezone.utc)
        db.commit()


@celery_app.task(name="ingest_document", bind=True, max_retries=2)
def ingest_document(self, filename: str, doc_id: int):
    """
    Full ingest pipeline:
    1. Download file from MinIO
    2. Extract text (PDF/DOCX/TXT/MD)
    3. Chunk text
    4. Embed chunks
    5. Upsert to Qdrant
    6. Update PostgreSQL status
    """
    db = SessionLocal()
    try:
        logger.info(f"[{filename}] Starting ingest...")
        _update_status(db, doc_id, "processing")

        # 1. Download from MinIO
        logger.info(f"[{filename}] Downloading from MinIO...")
        file_content = minio.download_file(filename)

        # 2. Extract text
        logger.info(f"[{filename}] Extracting text...")
        extracted = extract_text(filename, file_content)

        # 3. Chunk
        logger.info(f"[{filename}] Chunking...")
        if isinstance(extracted, list):
            # PDF - list of pages
            chunks = chunk_pdf_pages(
                pages=extracted,
                source=filename,
                chunk_size=settings.CHUNK_SIZE,
                overlap=settings.CHUNK_OVERLAP,
            )
        else:
            # Plain text
            chunks = chunk_simple_text(
                text=extracted,
                source=filename,
                chunk_size=settings.CHUNK_SIZE,
                overlap=settings.CHUNK_OVERLAP,
            )

        if not chunks:
            raise ValueError("No text could be extracted from document")

        logger.info(f"[{filename}] Created {len(chunks)} chunks")

        # 4. Embed in batches (more efficient)
        logger.info(f"[{filename}] Generating embeddings...")
        texts = [c["text"] for c in chunks]
        vectors = embed_batch(texts)

        # 5. Upsert to Qdrant — first delete any prior chunks for this source so the
        # new chunk set fully replaces the old one (handles re-ingest where the new
        # version has fewer chunks than the old one).
        try:
            qdrant.delete_by_source(filename)
        except Exception as e:
            logger.warning(f"[{filename}] Pre-upsert cleanup failed (continuing): {e}")
        logger.info(f"[{filename}] Saving to Qdrant...")
        qdrant.upsert_chunks(chunks, vectors)

        # 6. Invalidate BM25 cache so new chunks are keyword-searchable
        try:
            from backend.bm25_search import invalidate_cache
            invalidate_cache()
        except Exception as e:
            logger.warning(f"[{filename}] BM25 cache invalidation failed: {e}")

        # 7. Mark as completed
        _update_status(db, doc_id, "completed", chunks_count=len(chunks))
        logger.info(f"[{filename}] Ingest complete: {len(chunks)} chunks indexed")

        return {
            "filename": filename,
            "status": "completed",
            "chunks": len(chunks),
        }

    except Exception as e:
        logger.exception(f"[{filename}] Ingest failed")
        _update_status(db, doc_id, "failed", error=str(e))

        # Retry on transient errors
        try:
            raise self.retry(exc=e, countdown=10)
        except self.MaxRetriesExceededError:
            return {"filename": filename, "status": "failed", "error": str(e)}

    finally:
        db.close()


@celery_app.task(name="ping")
def ping():
    """Smoke test."""
    return "pong"
