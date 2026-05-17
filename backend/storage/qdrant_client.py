"""Qdrant vector database client."""
import logging
from typing import List, Dict, Any
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
)

from backend.config import settings

logger = logging.getLogger(__name__)

# Singleton client
client = QdrantClient(url=settings.QDRANT_URL)


def init_collection():
    """Create the documents collection if it doesn't exist."""
    try:
        collections = client.get_collections().collections
        names = [c.name for c in collections]

        if settings.QDRANT_COLLECTION not in names:
            client.create_collection(
                collection_name=settings.QDRANT_COLLECTION,
                vectors_config=VectorParams(
                    size=settings.EMBEDDING_DIM,
                    distance=Distance.COSINE,
                ),
            )
            logger.info(f"Created Qdrant collection: {settings.QDRANT_COLLECTION}")
        else:
            logger.info(f"Qdrant collection ready: {settings.QDRANT_COLLECTION}")
    except Exception as e:
        logger.error(f"Failed to init Qdrant: {e}")
        raise


def upsert_chunks(chunks: List[Dict[str, Any]], vectors: List[List[float]]):
    """
    Save chunks with their embeddings to Qdrant.

    Args:
        chunks: List of dicts with keys: text, source, page, chunk_index
        vectors: List of embedding vectors (one per chunk)
    """
    if len(chunks) != len(vectors):
        raise ValueError("chunks and vectors must have same length")

    points = [
        PointStruct(
            id=i,  # Will be replaced with proper IDs in caller
            vector=vector,
            payload=chunk,
        )
        for i, (chunk, vector) in enumerate(zip(chunks, vectors))
    ]

    # Use UUIDs or hashes for IDs to avoid collisions
    import uuid
    for point, chunk in zip(points, chunks):
        # Deterministic ID: hash of source + chunk_index
        point.id = str(uuid.uuid5(
            uuid.NAMESPACE_DNS,
            f"{chunk['source']}_{chunk['chunk_index']}"
        ))

    client.upsert(
        collection_name=settings.QDRANT_COLLECTION,
        points=points,
    )
    logger.info(f"Upserted {len(points)} vectors to Qdrant")

def search_chunks(query_vector: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
    """
    Find the most similar chunks to the query vector.

    Returns:
        List of dicts with keys: text, source, page, chunk_index, score
    """
    results = client.query_points(
        collection_name=settings.QDRANT_COLLECTION,
        query=query_vector,
        limit=top_k,
    ).points

    return [
        {
            "text": r.payload.get("text", ""),
            "source": r.payload.get("source", "unknown"),
            "page": r.payload.get("page", 0),
            "chunk_index": r.payload.get("chunk_index", 0),
            "score": float(r.score),
        }
        for r in results
    ]
def delete_by_source(source: str):
    """Delete all chunks for a specific source file."""
    client.delete(
        collection_name=settings.QDRANT_COLLECTION,
        points_selector=Filter(
            must=[FieldCondition(key="source", match=MatchValue(value=source))]
        ),
    )
    logger.info(f"Deleted all chunks for source: {source}")
