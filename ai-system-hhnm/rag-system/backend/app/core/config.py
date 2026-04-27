"""
Centralized application settings.
Reads from environment variables / .env file via pydantic-settings.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration for the RAG backend, loaded from .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- App ---
    APP_NAME: str = "RAG Knowledge Base System"
    APP_ENV: str = "development"
    APP_DEBUG: bool = True

    # --- PostgreSQL ---
    POSTGRES_USER: str = "rag_user"
    POSTGRES_PASSWORD: str = "rag_password"
    POSTGRES_DB: str = "rag_db"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432

    @property
    def DATABASE_URL(self) -> str:
        """SQLAlchemy-compatible connection string."""
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # --- Redis ---
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = "rag_redis_pass"

    @property
    def REDIS_URL(self) -> str:
        return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/0"

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

    # --- Celery ---
    CELERY_BROKER_URL: str = "redis://:rag_redis_pass@localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://:rag_redis_pass@localhost:6379/1"

    # --- Ollama ---
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_LLM_MODEL: str = "qwen3.5:4b"

    # --- Qdrant Collection ---
    QDRANT_COLLECTION: str = "rag_documents"

    # --- Microservice URLs ---
    EMBEDDING_SERVICE_URL: str = "http://localhost:8001"
    RERANKER_SERVICE_URL: str = "http://localhost:8002"
    RAG_SERVICE_URL: str = "http://localhost:8003"


# Singleton instance — import this everywhere
settings = Settings()
