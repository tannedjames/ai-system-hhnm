import os
import time
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, String, create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from auth import hash_password

DB_HOST = os.getenv("DB_HOST", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "rag_db")
DB_USER = os.getenv("DB_USER", "rag_user")
DB_PASS = os.getenv("DB_PASS", "rag_pass")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    bind=engine,
)
Base = declarative_base()
_UNSET = object()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    full_name = Column(String(255), nullable=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="user")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(500), nullable=False)
    original_name = Column(String(500), nullable=False)
    object_key = Column(String(500), nullable=False)
    file_type = Column(String(50), nullable=True)
    file_size = Column(BigInteger, nullable=True)
    source_type = Column(String(20), nullable=False, default="file")
    source_uri = Column(String(2000), nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    error_message = Column(String, nullable=True)
    chunk_count = Column(Integer, nullable=False, default=0)
    ingest_task_id = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class SystemConfig(Base):
    __tablename__ = "system_config"

    id = Column(Integer, primary_key=True)
    chunk_size = Column(Integer, nullable=False)
    chunk_overlap = Column(Integer, nullable=False)
    top_k = Column(Integer, nullable=False)
    llm_model = Column(String(120), nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


def _default_rag_settings() -> dict:
    return {
        "chunk_size": int(os.getenv("RAG_CHUNK_SIZE", "400")),
        "chunk_overlap": int(os.getenv("RAG_CHUNK_OVERLAP", "100")),
        "top_k": int(os.getenv("RAG_TOP_K", "3")),
        "llm_model": os.getenv("LLM_MODEL", "gemini-2.5-flash"),
    }


def _upgrade_schema():
    default_settings = _default_rag_settings()
    statements = [
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            email VARCHAR(255) UNIQUE NOT NULL,
            full_name VARCHAR(255),
            hashed_password VARCHAR(255) NOT NULL,
            role VARCHAR(20) NOT NULL DEFAULT 'user',
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email
        ON users(email)
        """,
        """
        ALTER TABLE documents
        ADD COLUMN IF NOT EXISTS original_name VARCHAR(500)
        """,
        """
        ALTER TABLE documents
        ADD COLUMN IF NOT EXISTS object_key VARCHAR(500)
        """,
        """
        ALTER TABLE documents
        ADD COLUMN IF NOT EXISTS file_type VARCHAR(50)
        """,
        """
        ALTER TABLE documents
        ADD COLUMN IF NOT EXISTS file_size BIGINT
        """,
        """
        ALTER TABLE documents
        ADD COLUMN IF NOT EXISTS source_type VARCHAR(20) NOT NULL DEFAULT 'file'
        """,
        """
        ALTER TABLE documents
        ADD COLUMN IF NOT EXISTS source_uri VARCHAR(2000)
        """,
        """
        ALTER TABLE documents
        ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'pending'
        """,
        """
        ALTER TABLE documents
        ADD COLUMN IF NOT EXISTS error_message TEXT
        """,
        """
        ALTER TABLE documents
        ADD COLUMN IF NOT EXISTS chunk_count INTEGER NOT NULL DEFAULT 0
        """,
        """
        ALTER TABLE documents
        ADD COLUMN IF NOT EXISTS ingest_task_id VARCHAR(255)
        """,
        """
        ALTER TABLE documents
        ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE
        """,
        """
        UPDATE documents
        SET original_name = filename
        WHERE original_name IS NULL
        """,
        """
        UPDATE documents
        SET object_key = filename
        WHERE object_key IS NULL
        """,
        """
        UPDATE documents
        SET updated_at = created_at
        WHERE updated_at IS NULL
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_documents_status
        ON documents(status)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_documents_source_type
        ON documents(source_type)
        """,
        """
        CREATE TABLE IF NOT EXISTS system_config (
            id INTEGER PRIMARY KEY,
            chunk_size INTEGER NOT NULL,
            chunk_overlap INTEGER NOT NULL,
            top_k INTEGER NOT NULL,
            llm_model VARCHAR(120) NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """,
    ]

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
        connection.execute(
            text(
                """
                INSERT INTO system_config (id, chunk_size, chunk_overlap, top_k, llm_model, updated_at)
                VALUES (1, :chunk_size, :chunk_overlap, :top_k, :llm_model, NOW())
                ON CONFLICT (id) DO NOTHING
                """
            ),
            default_settings,
        )


def init_db():
    for _ in range(10):
        try:
            Base.metadata.create_all(bind=engine)
            _upgrade_schema()
            print("DB connected")
            return
        except Exception as exc:
            print("Waiting for DB...", exc)
            time.sleep(2)

    raise RuntimeError("DB not ready")


def seed_default_admin():
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com").strip().lower()
    admin_password = os.getenv("ADMIN_PASSWORD", "admin123")
    admin_full_name = os.getenv("ADMIN_FULL_NAME", "Default Admin")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == admin_email).first()
        if user is None:
            user = User(
                email=admin_email,
                full_name=admin_full_name,
                hashed_password=hash_password(admin_password),
                role="admin",
                is_active=True,
            )
            db.add(user)
            db.commit()
            print(f"Seeded admin user: {admin_email}")
        elif user.role != "admin":
            user.role = "admin"
            user.updated_at = datetime.utcnow()
            db.commit()
            print(f"Promoted existing user to admin: {admin_email}")
    finally:
        db.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_user(
    db,
    *,
    email: str,
    password: str,
    full_name: str | None = None,
    role: str = "user",
    is_active: bool = True,
):
    user = User(
        email=email.strip().lower(),
        full_name=full_name,
        hashed_password=hash_password(password),
        role=role,
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_user_by_email(db, email: str):
    return db.query(User).filter(User.email == email.strip().lower()).first()


def get_user(db, user_id: int):
    return db.query(User).filter(User.id == user_id).first()


def list_users(db):
    return db.query(User).order_by(User.created_at.desc()).all()


def update_user(
    db,
    user_id: int,
    *,
    full_name=_UNSET,
    password=_UNSET,
    role=_UNSET,
    is_active=_UNSET,
):
    user = get_user(db, user_id)
    if user is None:
        return None

    if full_name is not _UNSET:
        user.full_name = full_name
    if password is not _UNSET and password:
        user.hashed_password = hash_password(password)
    if role is not _UNSET:
        user.role = role
    if is_active is not _UNSET:
        user.is_active = is_active

    user.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(user)
    return user


def delete_user(db, user_id: int):
    user = get_user(db, user_id)
    if user:
        db.delete(user)
        db.commit()
    return user


def create_document(
    db,
    *,
    filename: str,
    original_name: str,
    object_key: str,
    file_type: str | None,
    file_size: int | None,
    source_type: str = "file",
    source_uri: str | None = None,
    status: str = "pending",
):
    doc = Document(
        filename=filename,
        original_name=original_name,
        object_key=object_key,
        file_type=file_type,
        file_size=file_size,
        source_type=source_type,
        source_uri=source_uri,
        status=status,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def get_document(db, doc_id: int):
    return db.query(Document).filter(Document.id == doc_id).first()


def update_document_status(
    db,
    doc_id: int,
    *,
    status: str,
    error_message: str | None = None,
    chunk_count: int | None = None,
    ingest_task_id=_UNSET,
):
    doc = get_document(db, doc_id)
    if doc is None:
        return None

    doc.status = status
    doc.error_message = error_message
    doc.updated_at = datetime.utcnow()

    if chunk_count is not None:
        doc.chunk_count = chunk_count
    if ingest_task_id is not _UNSET:
        doc.ingest_task_id = ingest_task_id

    db.commit()
    db.refresh(doc)
    return doc


def get_documents(db):
    return db.query(Document).order_by(Document.created_at.desc()).all()


def delete_document(db, doc_id: int):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if doc:
        db.delete(doc)
        db.commit()
    return doc


def _ensure_system_config(db):
    config = db.query(SystemConfig).filter(SystemConfig.id == 1).first()
    if config is not None:
        return config

    defaults = _default_rag_settings()
    config = SystemConfig(
        id=1,
        chunk_size=defaults["chunk_size"],
        chunk_overlap=defaults["chunk_overlap"],
        top_k=defaults["top_k"],
        llm_model=defaults["llm_model"],
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


def get_system_config(db):
    return _ensure_system_config(db)


def update_system_config(
    db,
    *,
    chunk_size=_UNSET,
    chunk_overlap=_UNSET,
    top_k=_UNSET,
    llm_model=_UNSET,
):
    config = _ensure_system_config(db)
    if chunk_size is not _UNSET:
        config.chunk_size = int(chunk_size)
    if chunk_overlap is not _UNSET:
        config.chunk_overlap = int(chunk_overlap)
    if top_k is not _UNSET:
        config.top_k = int(top_k)
    if llm_model is not _UNSET:
        config.llm_model = str(llm_model).strip()
    config.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(config)
    return config
