import os
from pathlib import Path

from celery.utils.log import get_task_logger
from redis import Redis
from requests import RequestException

from celery_app import REDIS_URL, celery_app
from db import SessionLocal, get_document, get_system_config, init_db, update_document_status
from rag import delete_document_chunks
from rag_ingest import fetch_web_text, ingest_file, ingest_text
from storage import download_file, init_bucket, upload_file

logger = get_task_logger(__name__)
UPLOAD_DIR = Path(os.getenv("RAG_STAGING_DIR", "/tmp/rag-staging"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
redis_client = Redis.from_url(REDIS_URL)
MAX_RETRIES = int(os.getenv("CELERY_MAX_RETRIES", "5"))
RETRY_BACKOFF_BASE_SECONDS = int(os.getenv("CELERY_RETRY_BACKOFF_BASE_SECONDS", "5"))
RETRY_BACKOFF_MAX_SECONDS = int(os.getenv("CELERY_RETRY_BACKOFF_MAX_SECONDS", "120"))
INGEST_LOCK_TTL_SECONDS = int(os.getenv("INGEST_LOCK_TTL_SECONDS", "3600"))


def _local_doc_path(doc_id: int, file_name: str) -> Path:
    return UPLOAD_DIR / f"{doc_id}-{file_name}"


def _lock_key(doc_id: int) -> str:
    return f"ingest-lock:{doc_id}"


def _acquire_ingest_lock(doc_id: int, task_id: str) -> bool:
    return bool(
        redis_client.set(
            _lock_key(doc_id),
            task_id,
            nx=True,
            ex=INGEST_LOCK_TTL_SECONDS,
        )
    )


def _release_ingest_lock(doc_id: int):
    try:
        redis_client.delete(_lock_key(doc_id))
    except Exception:
        logger.exception("Failed to release ingest lock for document %s", doc_id)


def _is_retryable_error(exc: Exception) -> bool:
    retryable_types = (RequestException, ConnectionError, TimeoutError)
    if isinstance(exc, retryable_types):
        return True
    message = str(exc).lower()
    retryable_markers = [
        "timed out",
        "temporarily unavailable",
        "connection reset",
        "connection refused",
        "service unavailable",
        "rate limit",
        "429",
    ]
    return any(marker in message for marker in retryable_markers)


def _retry_delay_seconds(retry_count: int) -> int:
    return min(RETRY_BACKOFF_BASE_SECONDS * (2**retry_count), RETRY_BACKOFF_MAX_SECONDS)


def _resolve_chunking_settings(config) -> tuple[int, int]:
    chunk_size = int(config.chunk_size)
    chunk_overlap = int(config.chunk_overlap)
    if chunk_size < 100:
        chunk_size = 100
    if chunk_overlap < 0:
        chunk_overlap = 0
    if chunk_overlap >= chunk_size:
        chunk_overlap = chunk_size - 1
    return chunk_size, chunk_overlap


@celery_app.task(bind=True, name="tasks.ingest_document", max_retries=MAX_RETRIES)
def ingest_document_task(self, doc_id: int):
    init_db()
    init_bucket()

    db = SessionLocal()
    local_path = None
    lock_acquired = False

    try:
        doc = get_document(db, doc_id)
        if doc is None:
            logger.warning("Document %s not found", doc_id)
            return {"status": "missing", "document_id": doc_id}

        lock_acquired = _acquire_ingest_lock(doc_id, self.request.id or "")
        if not lock_acquired:
            logger.info("Skip document %s because another ingest task is holding lock", doc_id)
            return {"status": "skipped_locked", "document_id": doc_id}

        config = get_system_config(db)
        chunk_size, chunk_overlap = _resolve_chunking_settings(config)

        update_document_status(
            db,
            doc_id,
            status="processing",
            error_message=None,
            ingest_task_id=self.request.id,
        )

        if doc.source_type == "web":
            if not doc.source_uri:
                raise ValueError("Web document is missing source URI")

            source_title, text = fetch_web_text(doc.source_uri)
            source_name = doc.original_name or source_title
            local_path = _local_doc_path(doc.id, doc.filename)
            local_path.write_text(text, encoding="utf-8")

            upload_file(str(local_path), doc.object_key)
            _vector_store, chunk_count = ingest_text(
                text,
                doc.id,
                source_name=source_name,
                source_type="web",
                source_uri=doc.source_uri,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
        else:
            local_path = _local_doc_path(doc.id, doc.filename)
            download_file(doc.object_key, str(local_path))

            _vector_store, chunk_count = ingest_file(
                str(local_path),
                doc.id,
                source_name=doc.filename,
                source_type=doc.source_type or "file",
                source_uri=doc.source_uri,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )

        if get_document(db, doc.id) is None:
            delete_document_chunks(doc.id)
            return {"status": "deleted", "document_id": doc.id}

        latest_doc = get_document(db, doc.id)
        if latest_doc and latest_doc.status == "deleting":
            delete_document_chunks(doc.id)
            return {"status": "deleted", "document_id": doc.id}

        update_document_status(
            db,
            doc.id,
            status="completed",
            error_message=None,
            chunk_count=chunk_count,
            ingest_task_id=self.request.id,
        )

        return {
            "status": "completed",
            "document_id": doc.id,
            "chunk_count": chunk_count,
        }
    except Exception as exc:
        if _is_retryable_error(exc) and self.request.retries < MAX_RETRIES:
            delay = _retry_delay_seconds(self.request.retries)
            update_document_status(
                db,
                doc_id,
                status="pending",
                error_message=f"Retrying ingest after transient error: {exc}",
                ingest_task_id=self.request.id,
            )
            raise self.retry(exc=exc, countdown=delay)

        update_document_status(
            db,
            doc_id,
            status="failed",
            error_message=str(exc),
            ingest_task_id=self.request.id,
        )
        raise
    finally:
        if lock_acquired:
            _release_ingest_lock(doc_id)
        db.close()
        if local_path and local_path.exists():
            local_path.unlink()
