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
    API_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"

    # Server
    BACKEND_HOST: str = "0.0.0.0"
    BACKEND_PORT: int = 8000

    # Anthropic / TrollLLM
    ANTHROPIC_API_KEY: str
    ANTHROPIC_BASE_URL: str = "https://chat.trollllm.xyz"
    CLAUDE_MODEL: str = "claude-sonnet-4-6"
    CLAUDE_MAX_TOKENS: int = 2000

    # Uploads
    MAX_UPLOAD_SIZE_MB: int = 100
    UPLOAD_DIR: str = "./data/raw"

    # Vector DB
    CHROMA_DB_PATH: str = "./chroma_data"
    EMBEDDING_MODEL: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    CHUNK_SIZE: int = 500
    CHUNK_OVERLAP: int = 100

    # Redis / Celery
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/0"

    @property
    def allowed_file_types(self) -> List[str]:
        """Return allowed file types as a list"""
        return ["pdf", "txt", "docx", "md"]


# Singleton
settings = Settings()

# Ensure directories exist
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
os.makedirs(settings.CHROMA_DB_PATH, exist_ok=True)