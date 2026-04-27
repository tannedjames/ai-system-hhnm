"""
Ingest Worker — Celery Tasks
================================
Document ingestion pipeline:
  1. Download file from MinIO
  2. Parse text (PDF / DOCX / TXT)
  3. Chunk with RecursiveCharacterTextSplitter
  4. Call Embedding Service for hybrid vectors
  5. Upsert to Qdrant (dense + sparse)
  6. Update PostgreSQL document status

Run:
    conda activate rag-ingest
    celery -A worker:celery_app worker --loglevel=info --pool=solo
"""

import sys
import os
import uuid
import logging
import tempfile

import httpx
from celery import Celery
from langchain_text_splitters import RecursiveCharacterTextSplitter

# ---------------------------------------------------------------------------
# Add the parent 'services' directory to sys.path
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from shared.config import shared_settings as settings

from parsers import parse_file

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Celery Application
# ---------------------------------------------------------------------------
celery_app = Celery(
    "rag_ingest_worker",
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
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=86400,
)

# ---------------------------------------------------------------------------
# Text splitter
# ---------------------------------------------------------------------------
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=200,
    length_function=len,
    separators=["\n\n", "\n", ". ", " ", ""],
)


# ---------------------------------------------------------------------------
# Helper: MinIO client
# ---------------------------------------------------------------------------
def _get_minio_client():
    from minio import Minio

    return Minio(
        endpoint=settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ROOT_USER,
        secret_key=settings.MINIO_ROOT_PASSWORD,
        secure=settings.MINIO_SECURE,
    )


# ---------------------------------------------------------------------------
# Helper: PostgreSQL connection
# ---------------------------------------------------------------------------
def _get_db_engine():
    from sqlalchemy import create_engine

    return create_engine(settings.DATABASE_URL, pool_pre_ping=True)


# ---------------------------------------------------------------------------
# Helper: Update document status in PostgreSQL
# ---------------------------------------------------------------------------
def _update_document_status(
    document_id: str, status: str, chunk_count: int = 0
):
    from sqlalchemy import text

    engine = _get_db_engine()
    with engine.connect() as conn:
        conn.execute(
            text(
                "UPDATE documents SET status = :status, chunk_count = :count, "
                "updated_at = NOW() WHERE id = :doc_id"
            ),
            {"status": status, "count": chunk_count, "doc_id": document_id},
        )
        conn.commit()
    engine.dispose()


# ---------------------------------------------------------------------------
# Helper: Call Embedding Service
# ---------------------------------------------------------------------------
def _embed_texts_hybrid(texts: list[str]) -> dict:
    """Call the Embedding Service to get dense + sparse vectors."""
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(
            f"{settings.EMBEDDING_SERVICE_URL}/embed/hybrid",
            json={"texts": texts},
        )
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Helper: Qdrant client
# ---------------------------------------------------------------------------
def _get_qdrant_client():
    from qdrant_client import QdrantClient

    return QdrantClient(host=settings.QDRANT_HOST, port=settings.QDRANT_PORT)


# ---------------------------------------------------------------------------
# Helper: Ensure Qdrant collection exists
# ---------------------------------------------------------------------------
def _ensure_collection():
    from qdrant_client.models import (
        Distance,
        SparseVectorParams,
        VectorParams,
    )

    client = _get_qdrant_client()
    collections = [c.name for c in client.get_collections().collections]

    if settings.QDRANT_COLLECTION not in collections:
        logger.info(f"Creating Qdrant collection: {settings.QDRANT_COLLECTION}")
        client.create_collection(
            collection_name=settings.QDRANT_COLLECTION,
            vectors_config={
                "dense": VectorParams(
                    size=settings.EMBEDDING_DIM,
                    distance=Distance.COSINE,
                ),
            },
            sparse_vectors_config={
                "sparse": SparseVectorParams(),
            },
        )
        logger.info("Collection created successfully.")
    else:
        logger.info(f"Collection '{settings.QDRANT_COLLECTION}' already exists.")

    client.close()


# ---------------------------------------------------------------------------
# Celery Task: Ingest Document
# ---------------------------------------------------------------------------
@celery_app.task(name="tasks.ingest_document", bind=True, max_retries=3)
def ingest_document(self, document_id: str, minio_path: str, filename: str):
    """
    Full ingestion pipeline for a single document.

    Args:
        document_id: UUID of the document record in PostgreSQL.
        minio_path: Object path in MinIO (e.g., "uploads/abc.pdf").
        filename: Original filename (used for parsing and metadata).
    """
    try:
        self.update_state(state="PROGRESS", meta={"step": "downloading"})
        logger.info(f"[Ingest] Starting: {filename} (id={document_id})")

        # Update status to processing
        _update_document_status(document_id, "processing")

        # ----------------------------------------------------------
        # Step 1: Download from MinIO
        # ----------------------------------------------------------
        logger.info("[1/5] Downloading from MinIO...")
        minio_client = _get_minio_client()
        response = minio_client.get_object(
            settings.MINIO_BUCKET_NAME, minio_path
        )
        file_bytes = response.read()
        response.close()
        response.release_conn()
        logger.info(f"  → Downloaded {len(file_bytes)} bytes")

        # ----------------------------------------------------------
        # Step 2: Parse document
        # ----------------------------------------------------------
        self.update_state(state="PROGRESS", meta={"step": "parsing"})
        logger.info("[2/5] Parsing document...")
        text = parse_file(file_bytes, filename)
        logger.info(f"  → Extracted {len(text)} characters")

        if not text.strip():
            _update_document_status(document_id, "failed")
            return {
                "status": "failed",
                "error": "No text extracted from document",
                "document_id": document_id,
            }

        # ----------------------------------------------------------
        # Step 3: Chunk text
        # ----------------------------------------------------------
        self.update_state(state="PROGRESS", meta={"step": "chunking"})
        logger.info("[3/5] Chunking text...")
        chunks = text_splitter.split_text(text)
        logger.info(f"  → Created {len(chunks)} chunks")

        # ----------------------------------------------------------
        # Step 4: Embed chunks (batched)
        # ----------------------------------------------------------
        self.update_state(state="PROGRESS", meta={"step": "embedding"})
        logger.info("[4/5] Embedding chunks...")

        # Ensure collection exists
        _ensure_collection()

        BATCH_SIZE = 32
        all_dense = []
        all_sparse = []

        for i in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[i : i + BATCH_SIZE]
            result = _embed_texts_hybrid(batch)
            all_dense.extend(result["dense"])
            all_sparse.extend(result["sparse"])
            logger.info(
                f"  → Embedded batch {i // BATCH_SIZE + 1}/"
                f"{(len(chunks) + BATCH_SIZE - 1) // BATCH_SIZE}"
            )

        # ----------------------------------------------------------
        # Step 5: Upsert to Qdrant
        # ----------------------------------------------------------
        self.update_state(state="PROGRESS", meta={"step": "upserting"})
        logger.info("[5/5] Upserting to Qdrant...")

        from qdrant_client.models import PointStruct, SparseVector

        qdrant = _get_qdrant_client()

        points = []
        for i, (chunk, dense_vec, sparse_data) in enumerate(
            zip(chunks, all_dense, all_sparse)
        ):
            point_id = str(uuid.uuid4())
            points.append(
                PointStruct(
                    id=point_id,
                    vector={
                        "dense": dense_vec,
                        "sparse": SparseVector(
                            indices=sparse_data["indices"],
                            values=sparse_data["values"],
                        ),
                    },
                    payload={
                        "text": chunk,
                        "document_id": document_id,
                        "filename": filename,
                        "chunk_index": i,
                        "total_chunks": len(chunks),
                    },
                )
            )

        # Upsert in batches
        for i in range(0, len(points), 100):
            batch = points[i : i + 100]
            qdrant.upsert(
                collection_name=settings.QDRANT_COLLECTION,
                points=batch,
            )

        qdrant.close()

        # Update PostgreSQL
        _update_document_status(document_id, "completed", len(chunks))

        logger.info(
            f"[Ingest] Complete: {filename} — {len(chunks)} chunks indexed"
        )

        return {
            "status": "completed",
            "document_id": document_id,
            "filename": filename,
            "chunks": len(chunks),
            "characters": len(text),
        }

    except Exception as e:
        logger.error(f"[Ingest] Failed: {filename} — {e}", exc_info=True)
        try:
            _update_document_status(document_id, "failed")
        except Exception:
            pass
        raise self.retry(exc=e, countdown=30)
