"""
RAG System — Celery Worker Configuration
==========================================
Minimal Celery app connected to the Redis broker.
Includes a dummy task for testing the queue.

Start locally:
    celery -A app.worker:celery_app worker --loglevel=info --pool=solo
"""

from celery import Celery
from app.core.config import settings


# =============================================================================
# Celery Application
# =============================================================================
celery_app = Celery(
    "rag_worker",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

# Celery configuration
celery_app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",

    # Timezone
    timezone="UTC",
    enable_utc=True,

    # Task settings
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,

    # Result expiration (24 hours)
    result_expires=86400,
)


# =============================================================================
# Dummy Task — for testing that the Celery queue works
# =============================================================================
@celery_app.task(name="tasks.dummy_task", bind=True)
def dummy_task(self, message: str = "Hello from Celery!") -> dict:
    """
    A simple test task to verify the Celery worker is running
    and can communicate with the Redis broker.

    Usage (from Python shell or FastAPI endpoint):
        from app.worker import dummy_task
        result = dummy_task.delay("Test message")
        print(result.get(timeout=10))
    """
    import time

    self.update_state(state="PROGRESS", meta={"step": "Processing…"})
    time.sleep(2)  # Simulate work

    return {
        "status": "completed",
        "message": message,
        "worker": self.request.hostname,
    }


# =============================================================================
# Future task stubs (for later implementation)
# =============================================================================
@celery_app.task(name="tasks.ingest_document", bind=True)
def ingest_document(self, document_id: str, file_path: str) -> dict:
    """
    Placeholder for the document ingestion pipeline.
    Will later handle: parse → chunk → embed → upsert to Qdrant.
    """
    return {
        "status": "not_implemented",
        "document_id": document_id,
        "message": "Ingestion pipeline not yet implemented.",
    }
