"""SQLAlchemy database models."""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime, Text, Float
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Document(Base):
    """Tracks uploaded documents and their ingest status."""
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(255), unique=True, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    file_type = Column(String(10), nullable=False)
    status = Column(String(20), default="pending")  # pending | processing | completed | failed
    chunks_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    uploaded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    processed_at = Column(DateTime, nullable=True)


class Conversation(Base):
    """Stores Q&A history."""
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    sources = Column(Text, nullable=True)  # JSON string of source citations
    model_used = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
