"""SQLAlchemy database models."""
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(80), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), default="user", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class SystemConfig(Base):
    __tablename__ = "system_config"
    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(120), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(255), unique=True, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    file_type = Column(String(10), nullable=False)
    status = Column(String(20), default="pending")
    chunks_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    uploaded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    processed_at = Column(DateTime, nullable=True)
    source_url = Column(Text, nullable=True)
    embedding_model = Column(String(255), nullable=True)
    embedding_dim = Column(Integer, nullable=True)
    chunk_size = Column(Integer, nullable=True)
    chunk_overlap = Column(Integer, nullable=True)
    pages_count = Column(Integer, nullable=True)
    empty_pages_count = Column(Integer, nullable=True)
    ocr_pages_count = Column(Integer, nullable=True)
    chunks_dropped = Column(Integer, nullable=True)
    ingest_duration_ms = Column(Integer, nullable=True)
    pipeline_version = Column(String(50), nullable=True)


class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    sources = Column(Text, nullable=True)
    model_used = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
