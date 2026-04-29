import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import jwt
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, Field, HttpUrl
from redis import Redis
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import (
    create_access_token,
    decode_access_token,
    is_default_secret_key,
    is_secret_key_strong,
    verify_password,
)
from celery_app import REDIS_URL, celery_app
from db import (
    Document,
    SessionLocal,
    SystemConfig,
    User,
    create_document,
    create_user,
    delete_document,
    delete_user,
    get_document,
    get_documents,
    get_system_config,
    get_user,
    get_user_by_email,
    init_db,
    list_users,
    seed_default_admin,
    update_document_status,
    update_system_config,
    update_user,
)
from rag import (
    ask_rag as run_rag_query,
    build_citations,
    build_prompt,
    collection_exists,
    delete_document_chunks,
    get_llm,
    load_rag,
    retrieve_with_scores,
)
from storage import bucket_exists, delete_file, init_bucket, upload_file
from tasks import ingest_document_task

load_dotenv()

UPLOAD_DIR = Path(os.getenv("RAG_STAGING_DIR", "/tmp/rag-staging"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".txt", ".md", ".html", ".htm"}
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
DEFAULT_CORS_ORIGINS = [
    "http://localhost",
    "http://127.0.0.1",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

app = FastAPI(title="RAG API")
qdrant_db = None
redis_client = Redis.from_url(REDIS_URL)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def _get_allowed_origins() -> list[str]:
    raw = os.getenv("CORS_ALLOW_ORIGINS", "")
    if not raw.strip():
        return DEFAULT_CORS_ORIGINS
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _require_security_baseline():
    if not is_secret_key_strong():
        raise RuntimeError("SECRET_KEY must be at least 32 characters")

    if APP_ENV in {"production", "prod"} and is_default_secret_key():
        raise RuntimeError("SECRET_KEY default value is not allowed in production")

    default_admin_password = os.getenv("ADMIN_PASSWORD", "admin123")
    if APP_ENV in {"production", "prod"} and default_admin_password == "admin123":
        raise RuntimeError("Default ADMIN_PASSWORD is not allowed in production")


allowed_origins = _get_allowed_origins()
allow_credentials = "*" not in allowed_origins


app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Question(BaseModel):
    question: str
    top_k: int | None = Field(default=None, ge=1, le=20)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserCreate(BaseModel):
    email: str
    password: str
    full_name: str | None = None
    role: str = "user"
    is_active: bool = True


class UserUpdate(BaseModel):
    full_name: str | None = None
    password: str | None = None
    role: str | None = None
    is_active: bool | None = None


class WebDocumentCreate(BaseModel):
    url: HttpUrl
    title: str | None = None


class TextDocumentCreate(BaseModel):
    title: str
    content: str


class SettingsUpdate(BaseModel):
    chunk_size: int | None = Field(default=None, ge=100, le=4000)
    chunk_overlap: int | None = Field(default=None, ge=0, le=2000)
    top_k: int | None = Field(default=None, ge=1, le=20)
    llm_model: str | None = Field(default=None, min_length=1, max_length=120)


def get_db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _sanitize_filename(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-_.")
    return cleaned[:120] or "document"


def get_rag_store():
    global qdrant_db

    try:
        if qdrant_db is None and collection_exists():
            qdrant_db = load_rag()
    except Exception:
        return None

    return qdrant_db


def refresh_rag_store():
    global qdrant_db
    qdrant_db = None
    return get_rag_store()


def _user_to_dict(user: User):
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
    }


def _document_to_dict(doc: Document):
    return {
        "id": doc.id,
        "filename": doc.filename,
        "original_name": doc.original_name,
        "object_key": doc.object_key,
        "file_type": doc.file_type,
        "file_size": doc.file_size,
        "source_type": doc.source_type,
        "source_uri": doc.source_uri,
        "status": doc.status,
        "chunk_count": doc.chunk_count,
        "ingest_task_id": doc.ingest_task_id,
        "error_message": doc.error_message,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
    }


def _settings_to_dict(config: SystemConfig):
    return {
        "chunk_size": config.chunk_size,
        "chunk_overlap": config.chunk_overlap,
        "top_k": config.top_k,
        "llm_model": config.llm_model,
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
    }


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db_session),
):
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_access_token(token)
        user_id = int(payload.get("sub"))
    except (jwt.InvalidTokenError, TypeError, ValueError):
        raise credentials_error

    user = get_user(db, user_id)
    if user is None or not user.is_active:
        raise credentials_error

    return user


def get_admin_user(current_user: User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


def _enqueue_ingest(doc_id: int):
    db = SessionLocal()

    try:
        doc = get_document(db, doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="Document not found")
        if doc.status == "deleting":
            raise HTTPException(status_code=409, detail="Document is being deleted")

        task = ingest_document_task.delay(doc_id)
        doc = update_document_status(
            db,
            doc_id,
            status="pending",
            error_message=None,
            chunk_count=0,
            ingest_task_id=task.id,
        )
        return doc
    except HTTPException:
        raise
    except Exception as exc:
        update_document_status(
            db,
            doc_id,
            status="failed",
            error_message=f"Failed to queue ingest: {exc}",
            ingest_task_id=None,
        )
        raise HTTPException(status_code=503, detail="Failed to queue ingest task") from exc
    finally:
        db.close()


@app.on_event("startup")
def startup():
    global qdrant_db

    _require_security_baseline()
    init_db()
    init_bucket()
    seed_default_admin()

    try:
        qdrant_db = load_rag()
        print("Loaded existing vector collection")
    except Exception:
        qdrant_db = None
        print("No vector collection yet")


@app.get("/health")
def health():
    db_status = "ok"
    minio_status = "ok"
    qdrant_status = "unknown"
    redis_status = "unknown"

    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    try:
        if not bucket_exists():
            minio_status = "error"
    except Exception:
        minio_status = "error"

    try:
        qdrant_status = "ok" if collection_exists() else "empty"
    except Exception:
        qdrant_status = "error"

    try:
        redis_client.ping()
        redis_status = "ok"
    except Exception:
        redis_status = "error"

    overall = (
        "ok"
        if db_status == "ok"
        and minio_status == "ok"
        and qdrant_status != "error"
        and redis_status == "ok"
        else "error"
    )
    return {
        "status": overall,
        "components": {
            "postgres": db_status,
            "minio": minio_status,
            "qdrant_collection": qdrant_status,
            "redis": redis_status,
        },
    }


@app.post("/auth/login", response_model=TokenResponse)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db_session)):
    user = get_user_by_email(db, form_data.username)
    if user is None or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User is inactive")

    access_token = create_access_token(str(user.id), user.role)
    return TokenResponse(access_token=access_token)


@app.get("/me")
def me(current_user: User = Depends(get_current_user)):
    return _user_to_dict(current_user)


@app.get("/users")
def users_list(
    _admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db_session),
):
    return [_user_to_dict(user) for user in list_users(db)]


@app.get("/settings")
def settings_get(
    _admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db_session),
):
    return _settings_to_dict(get_system_config(db))


@app.patch("/settings")
def settings_update(
    payload: SettingsUpdate,
    _admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db_session),
):
    current = get_system_config(db)
    next_chunk_size = payload.chunk_size if payload.chunk_size is not None else current.chunk_size
    next_chunk_overlap = (
        payload.chunk_overlap if payload.chunk_overlap is not None else current.chunk_overlap
    )
    if next_chunk_overlap >= next_chunk_size:
        raise HTTPException(
            status_code=400,
            detail="chunk_overlap must be smaller than chunk_size",
        )

    updated = update_system_config(
        db,
        chunk_size=payload.chunk_size if payload.chunk_size is not None else current.chunk_size,
        chunk_overlap=(
            payload.chunk_overlap if payload.chunk_overlap is not None else current.chunk_overlap
        ),
        top_k=payload.top_k if payload.top_k is not None else current.top_k,
        llm_model=payload.llm_model if payload.llm_model is not None else current.llm_model,
    )
    return _settings_to_dict(updated)


@app.post("/users")
def users_create(
    payload: UserCreate,
    _admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db_session),
):
    email = payload.email.strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email")
    if payload.role not in {"admin", "user"}:
        raise HTTPException(status_code=400, detail="Role must be admin or user")
    if len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    try:
        user = create_user(
            db,
            email=email,
            password=payload.password,
            full_name=payload.full_name,
            role=payload.role,
            is_active=payload.is_active,
        )
        return _user_to_dict(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email already exists")


@app.patch("/users/{user_id}")
def users_update(
    user_id: int,
    payload: UserUpdate,
    current_admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db_session),
):
    user = get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    new_role = payload.role if payload.role is not None else user.role
    if new_role not in {"admin", "user"}:
        raise HTTPException(status_code=400, detail="Role must be admin or user")
    if payload.password is not None and len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    if user.id == current_admin.id and payload.is_active is False:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own admin account")
    if user.id == current_admin.id and new_role != "admin":
        raise HTTPException(status_code=400, detail="You cannot remove your own admin role")

    updated = update_user(
        db,
        user_id,
        full_name=payload.full_name if payload.full_name is not None else user.full_name,
        password=payload.password if payload.password is not None else None,
        role=new_role,
        is_active=payload.is_active if payload.is_active is not None else user.is_active,
    )
    return _user_to_dict(updated)


@app.delete("/users/{user_id}")
def users_delete(
    user_id: int,
    current_admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db_session),
):
    if user_id == current_admin.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own admin account")

    user = delete_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return {"status": "deleted", "user_id": user_id}


@app.post("/upload")
@app.post("/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    _admin: User = Depends(get_admin_user),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    file_name = os.path.basename(file.filename)
    extension = Path(file_name).suffix.lower()
    if extension not in ALLOWED_UPLOAD_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_UPLOAD_EXTENSIONS))
        raise HTTPException(status_code=400, detail=f"Unsupported extension. Allowed: {allowed}")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    temp_path = UPLOAD_DIR / f"upload-{uuid4().hex}-{_sanitize_filename(file_name)}"
    with temp_path.open("wb") as handle:
        handle.write(file_bytes)

    object_key = f"documents/files/{uuid4().hex}-{_sanitize_filename(file_name)}"
    try:
        upload_file(str(temp_path), object_key)

        db = SessionLocal()
        try:
            doc = create_document(
                db,
                filename=file_name,
                original_name=file_name,
                object_key=object_key,
                file_type=file.content_type,
                file_size=len(file_bytes),
                source_type="file",
                source_uri=None,
                status="pending",
            )
        finally:
            db.close()
    finally:
        temp_path.unlink(missing_ok=True)

    doc = _enqueue_ingest(doc.id)
    refresh_rag_store()
    return {"message": f"{file_name} uploaded", "document": _document_to_dict(doc)}


@app.post("/documents/web")
def create_web_document(
    payload: WebDocumentCreate,
    _admin: User = Depends(get_admin_user),
):
    parsed = urlparse(str(payload.url))
    title = payload.title.strip() if payload.title else parsed.netloc
    safe_name = _sanitize_filename(title or "web-document")
    filename = f"{safe_name}.txt"
    object_key = f"documents/web/{uuid4().hex}-{filename}"

    db = SessionLocal()
    try:
        doc = create_document(
            db,
            filename=filename,
            original_name=title or filename,
            object_key=object_key,
            file_type="text/plain",
            file_size=None,
            source_type="web",
            source_uri=str(payload.url),
            status="pending",
        )
    finally:
        db.close()

    doc = _enqueue_ingest(doc.id)
    return {"status": "queued", "document": _document_to_dict(doc)}


@app.post("/documents/text")
def create_text_document(
    payload: TextDocumentCreate,
    _admin: User = Depends(get_admin_user),
):
    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="Title cannot be empty")
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty")

    file_name = f"{_sanitize_filename(payload.title)}.txt"
    object_key = f"documents/text/{uuid4().hex}-{file_name}"
    temp_path = UPLOAD_DIR / f"text-{uuid4().hex}-{file_name}"
    temp_path.write_text(payload.content, encoding="utf-8")

    try:
        upload_file(str(temp_path), object_key)
    finally:
        temp_path.unlink(missing_ok=True)

    db = SessionLocal()
    try:
        doc = create_document(
            db,
            filename=file_name,
            original_name=payload.title.strip(),
            object_key=object_key,
            file_type="text/plain",
            file_size=len(payload.content.encode("utf-8")),
            source_type="text",
            source_uri="inline",
            status="pending",
        )
    finally:
        db.close()

    doc = _enqueue_ingest(doc.id)
    refresh_rag_store()
    return {"status": "queued", "document": _document_to_dict(doc)}


@app.get("/documents")
def list_docs(
    _admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db_session),
):
    docs = get_documents(db)
    return [_document_to_dict(doc) for doc in docs]


@app.get("/documents/{doc_id}")
def get_doc(
    doc_id: int,
    _admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db_session),
):
    doc = get_document(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return _document_to_dict(doc)


@app.get("/documents/{doc_id}/task")
def get_doc_task_status(
    doc_id: int,
    _admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db_session),
):
    doc = get_document(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if not doc.ingest_task_id:
        return {"document_id": doc_id, "task_id": None, "state": "not_started"}

    task = celery_app.AsyncResult(doc.ingest_task_id)
    payload = {
        "document_id": doc_id,
        "task_id": doc.ingest_task_id,
        "state": task.state,
    }
    if task.state in {"SUCCESS", "FAILURE"}:
        payload["result"] = str(task.result)
    return payload


@app.post("/documents/{doc_id}/reindex")
def reindex_document(
    doc_id: int,
    _admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db_session),
):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.status == "deleting":
        raise HTTPException(status_code=409, detail="Document is being deleted")
    if doc.status in {"pending", "processing"} and doc.ingest_task_id:
        raise HTTPException(status_code=409, detail="Document is already being ingested")

    doc = _enqueue_ingest(doc_id)
    return {"status": "queued", "document": _document_to_dict(doc)}


@app.delete("/documents/{doc_id}")
def remove_document(
    doc_id: int,
    _admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db_session),
):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    task_id = doc.ingest_task_id
    update_document_status(
        db,
        doc.id,
        status="deleting",
        error_message=None,
        ingest_task_id=None,
    )

    if task_id:
        try:
            celery_app.control.revoke(task_id, terminate=True)
        except Exception:
            pass

    delete_document_chunks(doc.id)

    try:
        delete_file(doc.object_key)
    except Exception:
        pass

    delete_document(db, doc_id)
    refresh_rag_store()
    return {"status": "deleted", "document_id": doc_id}


@app.post("/ingest")
def ingest_all(
    _admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db_session),
):
    docs = db.query(Document).all()
    if not docs:
        raise HTTPException(status_code=400, detail="No documents available")

    queued = []
    skipped = []
    for doc in docs:
        if doc.status == "deleting":
            skipped.append(doc.id)
            continue
        if doc.status in {"pending", "processing"} and doc.ingest_task_id:
            skipped.append(doc.id)
            continue
        queued.append(doc.id)

    for doc_id in queued:
        _enqueue_ingest(doc_id)

    refresh_rag_store()
    return {
        "status": "queued",
        "queued_document_ids": queued,
        "skipped_document_ids": skipped,
    }


@app.post("/ask")
def ask(
    question: Question,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    qdrant = get_rag_store()
    if qdrant is None:
        raise HTTPException(
            status_code=503,
            detail="Vector database is not initialized. Ingest at least one document first.",
        )

    cleaned_question = question.question.strip()
    if not cleaned_question:
        raise HTTPException(status_code=400, detail="Empty question")

    config = get_system_config(db)
    top_k = question.top_k or config.top_k
    return run_rag_query(
        qdrant,
        cleaned_question,
        top_k=top_k,
        llm_model=config.llm_model,
    )


@app.post("/ask/stream")
async def ask_stream(
    question: Question,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    qdrant = get_rag_store()
    if qdrant is None:
        raise HTTPException(
            status_code=503,
            detail="Vector database is not initialized. Ingest at least one document first.",
        )

    cleaned_question = question.question.strip()
    if not cleaned_question:
        raise HTTPException(status_code=400, detail="Empty question")

    config = get_system_config(db)
    top_k = question.top_k or config.top_k
    results = retrieve_with_scores(qdrant, cleaned_question, top_k=top_k)
    docs = [doc for doc, _ in results]
    citations = build_citations(results)

    if not docs:
        async def empty_stream():
            yield "event: citations\ndata: []\n\n"
            payload = json.dumps({"text": "I don't have enough information"})
            yield f"event: token\ndata: {payload}\n\n"
            yield "event: done\ndata: {}\n\n"

        return StreamingResponse(
            empty_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    prompt = build_prompt(docs, cleaned_question)
    llm = get_llm(config.llm_model)

    async def event_generator():
        try:
            citations_payload = json.dumps(citations, ensure_ascii=False)
            yield f"event: citations\ndata: {citations_payload}\n\n"

            async for chunk in llm.astream(prompt):
                text_chunk = getattr(chunk, "content", None) or str(chunk)
                if not text_chunk:
                    continue
                token_payload = json.dumps({"text": text_chunk}, ensure_ascii=False)
                yield f"event: token\ndata: {token_payload}\n\n"

            yield "event: done\ndata: {}\n\n"
        except Exception as exc:
            error_payload = json.dumps({"message": str(exc)}, ensure_ascii=False)
            yield f"event: error\ndata: {error_payload}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
