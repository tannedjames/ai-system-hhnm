"""Celery tasks (skeleton — implementation lands in Phase 2)."""
import logging
from celery import Celery

from backend.config import settings

logger = logging.getLogger(__name__)

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
)


@celery_app.task(name="ping")
def ping() -> str:
    """Smoke test: celery -A worker.tasks call ping"""
    logger.info("ping task executed")
    return "pong"


@celery_app.task(name="process_document")
def process_document(filename: str, path: str) -> dict:
    """Phase 2: extract text, chunk, embed, index into ChromaDB."""
    logger.info(f"TODO process_document: {filename} at {path}")
    return {"status": "stub", "filename": filename}
