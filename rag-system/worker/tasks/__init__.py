"""Celery task modules."""
from worker.tasks.ingest import celery_app, ingest_document, ping

__all__ = ["celery_app", "ingest_document", "ping"]
