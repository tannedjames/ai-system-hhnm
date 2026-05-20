"""Application configuration loaded from .env"""
import os
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # API
    API_TITLE: str = "RAG Academic Search API"
    API_VERSION: str = "2.0.0"
    ENVIRONMENT: str = "development"

    # Server
    BACKEND_HOST: str = "0.0.0.0"
    BACKEND_PORT: int = 8000

    # Claude (via TrollLLM)
    ANTHROPIC_API_KEY: str
    ANTHROPIC_BASE_URL: str = "https://chat.trollllm.xyz"
    CLAUDE_MODEL: str = "claude-sonnet-4-6"
    # Bigger budget so synthesis answers ("summarize all the docs") are not truncated.
    CLAUDE_MAX_TOKENS: int = 4000

    # PostgreSQL
    DATABASE_URL: str = "postgresql://raguser:ragpass@localhost:5432/rag_db"

    # Qdrant
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_COLLECTION: str = "documents"

    # MinIO
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "documents"
    MINIO_SECURE: bool = False

    # Redis / Celery
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/0"

    # RAG settings
    EMBEDDING_MODEL: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    EMBEDDING_DIM: int = 384  # MiniLM produces 384-dim vectors
    CHUNK_SIZE: int = 500
    CHUNK_OVERLAP: int = 100
    # Default number of chunks returned by retrieval. Increased from 5 -> 8 because
    # 5 was systematically too narrow for broad/synthesis questions. hybrid_search
    # will widen this further when it detects a synthesis-style question.
    RETRIEVAL_TOP_K: int = 8

    # Uploads
    MAX_UPLOAD_SIZE_MB: int = 100

    @property
    def allowed_file_types(self) -> List[str]:
        return ["pdf", "txt", "docx", "md"]


# Singleton
settings = Settings()
