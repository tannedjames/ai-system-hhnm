"""Application configuration loaded from .env"""
import os
from typing import List
from pydantic import model_validator
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
    CORS_ALLOWED_ORIGINS: str = "*"

    # Server
    BACKEND_HOST: str = "0.0.0.0"
    BACKEND_PORT: int = 8000

    # Claude (via TrollLLM)
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_BASE_URL: str = "https://chat.trollllm.xyz"
    CLAUDE_MODEL: str = "claude-sonnet-4-6"
    CLAUDE_MAX_TOKENS: int = 2000
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://chat.trollllm.xyz/v1"
    OPENAI_USER_AGENT: str = "rag-system/1.0"
    LLM_MODEL: str = "claude-sonnet-4-6"

    # Auth
    JWT_SECRET_KEY: str = "change-this-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 120
    ADMIN_DEFAULT_USERNAME: str = "admin"
    ADMIN_DEFAULT_PASSWORD: str = "admin123"

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
    EMBEDDING_PROVIDER: str = "sentence-transformers"
    EMBEDDING_MODEL: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    EMBEDDING_DIM: int = 384  # MiniLM produces 384-dim vectors
    EMBEDDING_LOCAL_MODEL: str = ""
    EMBEDDING_DIMENSION: int = 0
    EMBEDDING_BATCH_SIZE: int = 32
    EMBEDDING_DEVICE: str = "cpu"
    CHUNK_SIZE: int = 500
    CHUNK_OVERLAP: int = 100
    RAG_CHUNK_MIN_CHARS: int = 120
    RAG_DEDUP_CHUNKS: bool = True
    RETRIEVAL_TOP_K: int = 5
    USE_RERANKER: bool = False
    MIN_RELEVANCE_SCORE: float = 0.0
    MAX_CONTEXT_CHUNKS: int = 8
    MAX_SOURCES_IN_ANSWER: int = 5
    PRELOAD_EMBEDDING_ON_STARTUP: bool = False
    OCR_ENABLED: bool = True
    OCR_LANGS: str = "eng+vie"
    OCR_DPI: int = 200
    OCR_MIN_TEXT_CHARS: int = 1
    PDF_MIN_PAGES_FOR_TEXT_GUARD: int = 8
    PDF_EMPTY_PAGE_RATIO_THRESHOLD: float = 0.7

    # Uploads
    MAX_UPLOAD_SIZE_MB: int = 50

    @model_validator(mode="after")
    def normalize_embedding_settings(self):
        provider = (self.EMBEDDING_PROVIDER or "").strip().lower()
        if provider in {"local", "huggingface", "sentence_transformers", "sentence-transformer"}:
            self.EMBEDDING_PROVIDER = "sentence-transformers"
        elif provider in {"openai", "openai_compatible", "openai-compatible"}:
            self.EMBEDDING_PROVIDER = "openai-compatible"
        elif provider:
            self.EMBEDDING_PROVIDER = provider

        if self.EMBEDDING_PROVIDER == "sentence-transformers" and self.EMBEDDING_LOCAL_MODEL:
            self.EMBEDDING_MODEL = self.EMBEDDING_LOCAL_MODEL
        if self.EMBEDDING_DIMENSION > 0:
            self.EMBEDDING_DIM = self.EMBEDDING_DIMENSION
        return self

    @property
    def allowed_file_types(self) -> List[str]:
        return ["pdf", "txt", "docx", "md"]

    @property
    def cors_allowed_origins_list(self) -> List[str]:
        if self.CORS_ALLOWED_ORIGINS.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.CORS_ALLOWED_ORIGINS.split(",") if origin.strip()]


# Singleton
settings = Settings()
