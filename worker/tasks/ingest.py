"""Celery task: ingest pipeline (extract → chunk → embed → store)."""
import logging
import re
import time
from hashlib import blake2b
from datetime import datetime, timezone

from celery import Celery
from celery.signals import worker_ready
from minio.error import S3Error
from sqlalchemy.orm import Session

from backend.config import settings
from backend.models import Document, SystemConfig
from backend.rag_chunking import chunk_pdf_pages, chunk_text as chunk_simple_text
from backend.rag_embedding import embed_batch
from backend.rag_extraction import extract_text_with_diagnostics
from backend.storage import minio_client as minio
from backend.storage import qdrant_client as qdrant
from backend.storage.database import SessionLocal

logger = logging.getLogger(__name__)
TOKEN_RE = re.compile(r"\w+", re.UNICODE)

celery_app = Celery("rag_worker", broker=settings.CELERY_BROKER_URL, backend=settings.CELERY_RESULT_BACKEND)
celery_app.conf.update(task_serializer="json", accept_content=["json"], result_serializer="json", timezone="UTC", enable_utc=True, task_track_started=True)


@worker_ready.connect
def preload_embedding_model(**kwargs):
    if settings.EMBEDDING_PROVIDER != "sentence-transformers":
        logger.info("Worker embedding provider %s; no local model preload needed", settings.EMBEDDING_PROVIDER)
        return
    try:
        from backend.rag_embedding import _get_model
        _get_model()
        logger.info("Embedding model preloaded in worker startup")
    except Exception as e:
        logger.warning(f"Could not preload embedding model: {e}")


def _update_status(
    db: Session,
    doc_id: int,
    status: str,
    error: str = None,
    chunks_count: int | None = None,
    pages_count: int | None = None,
    empty_pages_count: int | None = None,
    ocr_pages_count: int | None = None,
    chunks_dropped: int | None = None,
    ingest_duration_ms: int | None = None,
    embedding_model: str | None = None,
    embedding_dim: int | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if doc:
        doc.status = status
        doc.error_message = error
        if chunks_count is not None:
            doc.chunks_count = chunks_count
        if pages_count is not None:
            doc.pages_count = pages_count
        if empty_pages_count is not None:
            doc.empty_pages_count = empty_pages_count
        if ocr_pages_count is not None:
            doc.ocr_pages_count = ocr_pages_count
        if chunks_dropped is not None:
            doc.chunks_dropped = chunks_dropped
        if ingest_duration_ms is not None:
            doc.ingest_duration_ms = ingest_duration_ms
        if embedding_model is not None:
            doc.embedding_model = embedding_model
        if embedding_dim is not None:
            doc.embedding_dim = embedding_dim
        if chunk_size is not None:
            doc.chunk_size = chunk_size
        if chunk_overlap is not None:
            doc.chunk_overlap = chunk_overlap
        if embedding_model is not None or embedding_dim is not None:
            doc.pipeline_version = "v2"
        if status in ("completed", "failed"):
            doc.processed_at = datetime.now(timezone.utc)
        db.commit()

def _same_location(first: dict, second: dict) -> bool:
    return first.get("source") == second.get("source") and first.get("page") == second.get("page")


def _text_key(text: str) -> str:
    normalized = re.sub(r"\W+", "", (text or "").lower())
    return blake2b(normalized.encode("utf-8"), digest_size=12).hexdigest()


def _append_or_merge(cleaned: list[dict], chunk: dict, min_chars: int) -> bool:
    if cleaned and _same_location(cleaned[-1], chunk):
        cleaned[-1]["text"] = f"{cleaned[-1]['text']} {chunk['text']}".strip()
        return True
    return False


def _clean_chunks(chunks: list[dict], min_chars: int, dedup: bool) -> tuple[list[dict], int]:
    cleaned = []
    short_fallbacks = []
    dropped = 0
    pending_short: dict | None = None
    for chunk in chunks:
        text = " ".join((chunk.get("text") or "").split())
        if not TOKEN_RE.findall(text):
            dropped += 1
            continue
        clean_chunk = dict(chunk)
        clean_chunk["text"] = text

        if pending_short:
            if _same_location(pending_short, clean_chunk):
                clean_chunk["text"] = f"{pending_short['text']} {clean_chunk['text']}".strip()
                pending_short = None
            else:
                if not _append_or_merge(cleaned, pending_short, min_chars):
                    short_fallbacks.append(pending_short)
                    dropped += 1
                pending_short = None

        if len(clean_chunk["text"]) < min_chars:
            if cleaned and _same_location(cleaned[-1], clean_chunk):
                cleaned[-1]["text"] = f"{cleaned[-1]['text']} {clean_chunk['text']}".strip()
            else:
                pending_short = clean_chunk
            continue

        cleaned.append(clean_chunk)

    if pending_short:
        if cleaned and _same_location(cleaned[-1], pending_short):
            cleaned[-1]["text"] = f"{cleaned[-1]['text']} {pending_short['text']}".strip()
        else:
            short_fallbacks.append(pending_short)
            dropped += 1

    if not cleaned and short_fallbacks:
        cleaned = short_fallbacks
        dropped = max(0, dropped - len(cleaned))

    if dedup:
        unique = []
        seen = set()
        for chunk in cleaned:
            key = _text_key(chunk["text"])
            if key in seen:
                dropped += 1
                continue
            seen.add(key)
            unique.append(chunk)
        cleaned = unique

    for next_index, chunk in enumerate(cleaned):
        chunk["chunk_index"] = next_index
    return cleaned, dropped

def _bool_value(value: str | bool) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _runtime_ingest_settings(db: Session) -> tuple[int, int, int, bool, dict]:
    keys = [
        "CHUNK_SIZE",
        "CHUNK_OVERLAP",
        "RAG_CHUNK_MIN_CHARS",
        "RAG_DEDUP_CHUNKS",
        "OCR_ENABLED",
        "OCR_LANGS",
        "OCR_DPI",
        "OCR_MIN_TEXT_CHARS",
    ]
    rows = {row.key: row.value for row in db.query(SystemConfig).filter(SystemConfig.key.in_(keys)).all()}
    chunk_size = int(rows.get("CHUNK_SIZE", settings.CHUNK_SIZE))
    chunk_overlap = int(rows.get("CHUNK_OVERLAP", settings.CHUNK_OVERLAP))
    min_chars = int(rows.get("RAG_CHUNK_MIN_CHARS", settings.RAG_CHUNK_MIN_CHARS))
    dedup = _bool_value(rows.get("RAG_DEDUP_CHUNKS", settings.RAG_DEDUP_CHUNKS))
    chunk_size = max(50, chunk_size)
    chunk_overlap = max(0, min(chunk_overlap, chunk_size - 1))
    min_chars = max(40, min_chars)
    extraction_options = {
        "ocr_enabled": _bool_value(rows.get("OCR_ENABLED", settings.OCR_ENABLED)),
        "ocr_langs": rows.get("OCR_LANGS", settings.OCR_LANGS),
        "ocr_dpi": max(100, int(rows.get("OCR_DPI", settings.OCR_DPI))),
        "ocr_min_text_chars": max(1, int(rows.get("OCR_MIN_TEXT_CHARS", settings.OCR_MIN_TEXT_CHARS))),
    }
    return chunk_size, chunk_overlap, min_chars, dedup, extraction_options


@celery_app.task(name="ingest_document", bind=True, max_retries=2)
def ingest_document(self, filename: str, doc_id: int, force_reindex: bool = False):
    db = SessionLocal()
    started = time.perf_counter()
    try:
        _update_status(db, doc_id, "processing", chunks_count=0)
        file_content = minio.download_file(filename)
        chunk_size, chunk_overlap, min_chars, dedup_chunks, extraction_options = _runtime_ingest_settings(db)
        extracted, diagnostics = extract_text_with_diagnostics(filename, file_content, extraction_options)
        pages_count = diagnostics["pages_count"]
        if isinstance(extracted, list):
            chunks = chunk_pdf_pages(pages=extracted, source=filename, chunk_size=chunk_size, overlap=chunk_overlap)
        else:
            chunks = chunk_simple_text(text=extracted, source=filename, chunk_size=chunk_size, overlap=chunk_overlap)
        chunks, dropped_chunks = _clean_chunks(chunks, min_chars=min_chars, dedup=dedup_chunks)
        if not chunks:
            raise ValueError("No non-empty text chunks could be extracted from document")
        if dropped_chunks:
            logger.info(f"[{filename}] Dropped {dropped_chunks} empty/tiny chunks before embedding")

        if force_reindex:
            qdrant.delete_by_source(filename)

        vectors = embed_batch([c["text"] for c in chunks])
        qdrant.upsert_chunks(chunks, vectors)

        try:
            from backend.bm25_search import invalidate_cache

            invalidate_cache()
        except Exception as e:
            logger.warning(f"[{filename}] BM25 cache invalidation failed: {e}")

        duration_ms = int((time.perf_counter() - started) * 1000)
        _update_status(
            db,
            doc_id,
            "completed",
            chunks_count=len(chunks),
            pages_count=pages_count,
            empty_pages_count=diagnostics["empty_pages_count"],
            ocr_pages_count=diagnostics["ocr_pages_count"],
            chunks_dropped=dropped_chunks,
            ingest_duration_ms=duration_ms,
            embedding_model=settings.EMBEDDING_MODEL,
            embedding_dim=settings.EMBEDDING_DIM,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        return {"filename": filename, "status": "completed", "chunks": len(chunks), "force_reindex": force_reindex}
    except Exception as e:
        logger.exception(f"[{filename}] Ingest failed")
        duration_ms = int((time.perf_counter() - started) * 1000)
        _update_status(db, doc_id, "failed", error=str(e), ingest_duration_ms=duration_ms)
        if isinstance(e, S3Error) and e.code == "NoSuchKey":
            return {"filename": filename, "status": "failed", "error": str(e)}
        try:
            raise self.retry(exc=e, countdown=10)
        except self.MaxRetriesExceededError:
            return {"filename": filename, "status": "failed", "error": str(e)}
    finally:
        db.close()


@celery_app.task(name="ping")
def ping():
    return "pong"
