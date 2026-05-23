"""PostgreSQL database session management."""
import logging
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, Session
from backend.config import settings
from backend.models import Base

logger = logging.getLogger(__name__)

# Create engine
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Create all tables if they don't exist."""
    try:
        Base.metadata.create_all(bind=engine)
        _apply_lightweight_migrations()
        logger.info("Database tables ready")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise


def _apply_lightweight_migrations():
    with engine.begin() as conn:
        inspector = inspect(conn)
        tables = inspector.get_table_names()
        if "documents" in tables:
            cols = {c["name"] for c in inspector.get_columns("documents")}
            migrations = [
                ("source_url", "ALTER TABLE documents ADD COLUMN source_url TEXT"),
                ("embedding_model", "ALTER TABLE documents ADD COLUMN embedding_model VARCHAR(255)"),
                ("embedding_dim", "ALTER TABLE documents ADD COLUMN embedding_dim INTEGER"),
                ("chunk_size", "ALTER TABLE documents ADD COLUMN chunk_size INTEGER"),
                ("chunk_overlap", "ALTER TABLE documents ADD COLUMN chunk_overlap INTEGER"),
                ("pages_count", "ALTER TABLE documents ADD COLUMN pages_count INTEGER"),
                ("empty_pages_count", "ALTER TABLE documents ADD COLUMN empty_pages_count INTEGER"),
                ("ocr_pages_count", "ALTER TABLE documents ADD COLUMN ocr_pages_count INTEGER"),
                ("chunks_dropped", "ALTER TABLE documents ADD COLUMN chunks_dropped INTEGER"),
                ("ingest_duration_ms", "ALTER TABLE documents ADD COLUMN ingest_duration_ms INTEGER"),
                ("pipeline_version", "ALTER TABLE documents ADD COLUMN pipeline_version VARCHAR(50)"),
            ]
            for name, sql in migrations:
                if name not in cols:
                    conn.execute(text(sql))

        if "system_config" not in tables:
            conn.execute(text("""
                CREATE TABLE system_config (
                    id SERIAL PRIMARY KEY,
                    key VARCHAR(120) UNIQUE NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP
                )
            """))


def get_db() -> Session:
    """Get a database session (FastAPI dependency)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
