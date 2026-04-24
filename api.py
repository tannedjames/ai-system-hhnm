from fastapi import FastAPI, HTTPException, UploadFile, File
from pydantic import BaseModel
import os
import shutil
from dotenv import load_dotenv

from db import Document, SessionLocal, init_db
from storage import init_bucket, upload_file
load_dotenv()
from rag import load_rag, build_rag_from_folder, ask_rag

# Setup upload directory
UPLOAD_DIR = "data"
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI()

qdrant_db = None


class Question(BaseModel):
    question: str


@app.on_event("startup")
def startup():
    global qdrant_db

    init_db()
    init_bucket()

    try:
        qdrant_db = load_rag()
        print("✅ Loaded existing DB")
    except:
        print("⚠️ No vector DB yet")
        qdrant_db = None


@app.get("/health")
def health():
    if qdrant_db is None:
        return {"status": "error", "message": "DB not initialized"}
    return {"status": "ok"}


@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    file_path = f"data/{file.filename}"

    with open(file_path, "wb") as f:
        f.write(await file.read())

    # Upload to MinIO
    object_key = upload_file(file_path, file.filename)

    # Save metadata to Postgres
    db = SessionLocal()
    doc = Document(filename=file.filename, object_key=object_key)
    db.add(doc)
    db.commit()
    db.close()

    return {"message": f"{file.filename} uploaded"}

@app.get("/documents")
def list_docs():
    db = SessionLocal()
    docs = db.query(Document).all()
    db.close()

    return [
        {
            "id": d.id,
            "filename": d.filename,
            "object_key": d.object_key
        }
        for d in docs
    ]

@app.post("/ingest")
def ingest():
    """Rebuild the vector database from all PDFs in the data directory"""
    global qdrant_db

    try:
        qdrant_db = build_rag_from_folder(UPLOAD_DIR)
        return {"status": "success", "message": "Vector DB reindexed from all PDFs"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/ask")
async def ask(q: Question):
    if qdrant_db is None:
        raise HTTPException(status_code=503, detail="Database not initialized. Upload PDFs and call /ingest first.")
    
    if not q.question.strip():
        raise HTTPException(status_code=400, detail="Empty question")

    try:
        answer, docs = ask_rag(qdrant_db, q.question)

        return {
            "answer": answer,
            "sources": [
                {
                    "source": d.metadata.get("source"),
                    "page": d.metadata.get("page"),
                    "preview": d.page_content[:200]
                }
                for d in docs
            ]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))