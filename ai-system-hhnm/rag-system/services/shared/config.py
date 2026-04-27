"""
RAG System — Shared Configuration
===================================
Centralized settings shared by all microservices.
Reads from the project-root .env file via pydantic-settings.
"""

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve the .env at the rag-system root (two levels up from services/shared/)
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


class SharedSettings(BaseSettings):
    """Settings shared across all RAG microservices."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_PATH),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- PostgreSQL ---
    POSTGRES_USER: str = "rag_user"
    POSTGRES_PASSWORD: str = "rag_password"
    POSTGRES_DB: str = "rag_db"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432

    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # --- Redis ---
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = "rag_redis_pass"

    # --- Celery ---
    CELERY_BROKER_URL: str = "redis://:rag_redis_pass@localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://:rag_redis_pass@localhost:6379/1"

    # --- MinIO ---
    MINIO_HOST: str = "localhost"
    MINIO_API_PORT: int = 9000
    MINIO_ROOT_USER: str = "minio_admin"
    MINIO_ROOT_PASSWORD: str = "minio_secret_123"
    MINIO_BUCKET_NAME: str = "rag-documents"
    MINIO_SECURE: bool = False

    @property
    def MINIO_ENDPOINT(self) -> str:
        return f"{self.MINIO_HOST}:{self.MINIO_API_PORT}"

    # --- Qdrant ---
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    QDRANT_COLLECTION: str = "rag_documents"

    # --- Ollama ---
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_LLM_MODEL: str = "qwen3.5:4b"

    # --- Embedding Model ---
    EMBEDDING_MODEL_NAME: str = "Qwen/Qwen3-Embedding-0.6B"
    EMBEDDING_DIM: int = 1024
    SPARSE_MODEL_NAME: str = "Qdrant/bm25"

    # --- Reranker Model ---
    RERANKER_MODEL_NAME: str = "Qwen/Qwen3-Reranker-0.6B"

    # --- Service URLs ---
    EMBEDDING_SERVICE_URL: str = "http://localhost:8001"
    RERANKER_SERVICE_URL: str = "http://localhost:8002"
    RAG_SERVICE_URL: str = "http://localhost:8003"


# Singleton instance
shared_settings = SharedSettings()
