# RAG Academic Search System

A production-grade Retrieval-Augmented Generation (RAG) system for searching and answering questions about academic documents. Built with FastAPI, Next.js, PostgreSQL, Qdrant, and Claude.

**Status:** Phase 4 complete — full hybrid retrieval (dense + sparse + rerank) with web frontends and Docker orchestration.

---

## 📋 Table of Contents

- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [Services & Ports](#services--ports)
- [Usage](#usage)
- [Tech Stack](#tech-stack)
- [Environment Variables](#environment-variables)
- [Development](#development)
- [Team](#team)

---

## Quick Start

### Prerequisites
- Docker Desktop (installed and running)
- 4 GB RAM allocated to Docker
- Your TrollLLM/Claude API key

### 1. Clone & Setup

```bash
git clone <repo-url>
cd rag-system
cp .env.example .env
```

### 2. Add your API key to `.env`

```bash
ANTHROPIC_API_KEY=sk-trollllm-your-key-here
```

### 3. Start everything

```bash
docker compose up -d --build
```

Wait 5-10 minutes on first run (builds Next.js apps, pulls images, initializes DBs).

### 4. Verify all services are running

```bash
docker compose ps
```

All 8 containers should show `running` or `healthy`.

### 5. Open the UIs

- **User chat:** http://localhost (or http://localhost:3000)
- **Admin panel:** http://localhost/admin (or http://localhost:3001)
- **API docs:** http://localhost/docs (or http://localhost:8000/docs)
- **MinIO console:** http://localhost:9001 (login: `minioadmin` / `minioadmin`)
- **Qdrant dashboard:** http://localhost:6333/dashboard

---

## Directory Structure

```
rag-system/
│
├── docker-compose.yml              # Orchestrates all 8 services
├── .env                            # Configuration (API keys, DB creds) — DO NOT COMMIT
├── .env.example                    # Safe template for .env
├── .gitignore                      # Protects .env and secrets
├── README.md                       # This file
│
├── Dockerfile.backend              # Python 3.11 + FastAPI
├── Dockerfile.worker               # Python 3.11 + Celery
│
├── requirements.txt                # Python dependencies
│
├── backend/                        # FastAPI + RAG logic
│   ├── __init__.py
│   ├── config.py                   # Settings from .env
│   ├── main.py                     # Routes: /api/ask, /api/search, /api/documents
│   │
│   ├── models/
│   │   └── __init__.py             # SQLAlchemy: Document, Conversation tables
│   │
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── database.py             # PostgreSQL session management
                        ┌─────────────────────┐
                        │   Nginx (port 80)   │
                        │  Load Balancer      │
                        └────┬────┬────┬──────┘
                             │    │    │
                ┌────────────┘    │    └──────────────┐
                │                 │                    │
      ┌─────────▼────────┐   ┌────▼──────────┐  ┌────▼─────────┐
      │  Frontend User   │   │   Frontend    │  │   Backend    │
      │  (port 3000)     │   │    Admin      │  │  (port 8000) │
      │  Next.js         │   │  (port 3001)  │  │   FastAPI    │
      │  Chat UI         │   │  Next.js      │  │   RAG logic  │
      │  + sources       │   │  Upload & mgmt│  │              │
      └──────────────────┘   └───────────────┘  └────┬─────────┘
                                                      │
      ┌──────────────────────────────────────────────┼──────────────────┐
      │                                              │                  │
   ┌──▼──────────┐  ┌──────────┐  ┌─────────────┐  ┌─▼──────┐  ┌───────▼──┐
   │ PostgreSQL  │  │ Qdrant   │  │ MinIO       │  │ Redis  │  │  Worker  │
   │ (port 5432) │  │ (port    │  │ (port 9000) │  │ (port  │  │ (Celery) │
   │             │  │  6333)   │  │             │  │ 6379)  │  │          │
   │ - Users     │  │          │  │ - Documents │  │        │  │ Extract  │
   │ - Documents │  │ Vectors  │  │ - PDFs/TXT  │  │ Queue  │  │ Chunk    │
   │ - Chats     │  │ Chunks   │  │   storage   │  │ & cache│  │ Embed    │
   └─────────────┘  └──────────┘  └─────────────┘  └────────┘  └──────────┘
```

### Pipeline Flow

**Ingest (Background)**
```
Admin uploads file
    ↓
Backend saves to MinIO + PostgreSQL
    ↓
Celery task queued (Redis)
    ↓
Worker: extract text → chunk → embed → upsert Qdrant
    ↓
Status: pending → processing → completed
```

**Query (Real-time)**
```
User asks question
    ↓
Backend embeds question
    ↓
Hybrid retrieval:
    ├─ Dense search (Qdrant)
    └─ Sparse search (BM25)
    ↓
RRF fusion (combine results)
    ↓
Cross-encoder rerank
    ↓
LLM generation with context
    ↓
Answer + source citations
```

---

## Repository Structure

```
rag-system/
├── .git/                          # Git repository
├── .gitignore                     # Ignore .env, node_modules, __pycache__, etc.
├── .env                           # Local config (NOT in git)
├── .env.example                   # Template for .env (in git)
│
├── docker-compose.yml             # Orchestrate all 8 services
├── Dockerfile.backend             # Backend service image
├── Dockerfile.worker              # Worker service image
│
├── README.md                       # This file
├── LICENSE                        # MIT License
│
├── requirements.txt               # Python dependencies (backend + worker)
│
├── backend/                       # FastAPI application
│   ├── __init__.py
│   ├── config.py                  # Settings from .env
│   ├── main.py                    # API routes, health checks
│   ├── rag_embedding.py           # sentence-transformers local embeddings
│   ├── rag_chunking.py            # Text chunking with overlap
│   ├── rag_extraction.py          # Extract text from PDF/DOCX/TXT/MD
│   ├── bm25_search.py             # BM25 sparse keyword search
│   ├── hybrid_search.py           # Dense + sparse fusion + reranking
│   ├── models/
│   │   └── __init__.py            # SQLAlchemy ORM models (Document, Conversation)
│   └── storage/
│       ├── __init__.py
│       ├── database.py            # PostgreSQL session management
│       ├── qdrant_client.py       # Vector database operations
│       └── minio_client.py        # Object storage operations
│
├── worker/                        # Celery background task processor
│   ├── __init__.py
│   └── tasks/
│       ├── __init__.py
│       └── ingest.py              # Document ingest pipeline (extract → chunk → embed → store)
│
├── frontend-user/                 # User chat interface (Next.js)
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── package.json
│   ├── next.config.js
│   ├── tsconfig.json
│   ├── tailwind.config.js
│   ├── postcss.config.js
│   ├── node_modules/              # (generated on build)
│   ├── .next/                     # (generated on build)
│   └── src/
│       └── app/
│           ├── globals.css        # Tailwind + global styles
│           ├── layout.tsx         # Root layout
│           └── page.tsx           # Chat UI (question input, response display, sources)
│
├── frontend-admin/                # Admin document management (Next.js)
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── package.json
│   ├── next.config.js
│   ├── tsconfig.json
│   ├── tailwind.config.js
│   ├── postcss.config.js
│   ├── node_modules/              # (generated on build)
│   ├── .next/                     # (generated on build)
│   └── src/
│       └── app/
│           ├── globals.css        # Tailwind + global styles
│           ├── layout.tsx         # Root layout
│           └── page.tsx           # Admin UI (upload, document table, ingest status)
│
└── nginx/                         # Reverse proxy / load balancer
    ├── Dockerfile
    ├── nginx.conf                 # Main Nginx config
    └── conf.d/
        └── default.conf           # Routing rules (/ → user, /admin → admin, /api → backend)
```

### Key files explained

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Defines all 8 services (containers), volumes, networks, health checks |
| `requirements.txt` | Python packages (FastAPI, SQLAlchemy, Qdrant, MinIO, Celery, etc.) |
| `backend/main.py` | REST API endpoints (`/api/ask`, `/api/search`, `/api/documents/*`, etc.) |
| `backend/hybrid_search.py` | RAG pipeline: dense + sparse + RRF + rerank |
| `worker/tasks/ingest.py` | Background task: extract PDF → chunk → embed → Qdrant |
| `frontend-user/src/app/page.tsx` | Chat UI for end users |
| `frontend-admin/src/app/page.tsx` | Admin panel for document management |
| `nginx/conf.d/default.conf` | Routes requests: `/` → port 3000, `/admin` → 3001, `/api/*` → 8000 |

### Data flow through the repo

```
Admin uploads file
    ↓
frontend-admin/src/app/page.tsx (UI)
    ↓
backend/main.py (/api/documents/upload)
    ↓
backend/storage/minio_client.py (save file)
backend/models/__init__.py (save metadata)
    ↓
worker/tasks/ingest.py (Celery task)
    ├─ backend/rag_extraction.py (extract text)
    ├─ backend/rag_chunking.py (split into chunks)
    ├─ backend/rag_embedding.py (embed chunks)
    └─ backend/storage/qdrant_client.py (store vectors)
    ↓
User visits frontend-user
    ↓
User asks question in frontend-user/src/app/page.tsx
    ↓
backend/main.py (/api/ask)
    ↓
backend/hybrid_search.py
    ├─ backend/rag_embedding.py (embed question)
    ├─ backend/storage/qdrant_client.py (dense search)
    ├─ backend/bm25_search.py (sparse search)
    ├─ hybrid_search.py (RRF + rerank)
    └─ LLM call (Claude via TrollLLM)
    ↓
Answer + sources → frontend-user
```

---

## Services & Ports

| Service | Port | Purpose |
|---------|------|---------|
| **Nginx** | 80 | Reverse proxy (main entry point) |
| **Frontend User** | 3000 | Chat UI (Q&A) |
| **Frontend Admin** | 3001 | Admin panel (upload, manage docs) |
| **Backend API** | 8000 | FastAPI + RAG logic |
| **PostgreSQL** | 5432 | Document metadata, Q&A history |
| **Qdrant** | 6333 | Vector database (embeddings) |
| **MinIO** | 9000 | Object storage (uploaded files) |
| **Redis** | 6379 | Message queue + cache |

All services are defined in `docker-compose.yml` and managed as Docker containers with persistent volumes.

---

## Usage

### Admin: Upload Documents

1. Open http://localhost/admin
2. Drag or click to upload a PDF/TXT/DOCX/MD file (max 100 MB)
3. Watch status change: **pending** → **processing** → **completed**
4. Once completed, chunks appear in Qdrant and BM25 index
5. View file details: size, chunk count, upload time

To delete a document:
- Click the **Delete** button
- This removes it from MinIO (storage), PostgreSQL (metadata), and Qdrant (vectors)

### User: Ask Questions

1. Open http://localhost (main UI)
2. Type a question, e.g., "What is the main topic of these documents?"
3. Click **Ask**
4. Claude responds with:
   - **Answer** — grounded in your uploaded documents
   - **Sources** — file name, page number, relevance score, text preview
5. View **Q&A history** via `/api/conversations` endpoint

### API: Programmatic Access

**List documents:**
```bash
curl http://localhost:8000/api/documents
```

**Upload file:**
```bash
curl -X POST http://localhost:8000/api/documents/upload \
  -F "file=@myfile.pdf"
```

**Ask a question:**
```bash
curl -X POST http://localhost:8000/api/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What does the document say about X?", "top_k": 5}'
```

**Semantic search:**
```bash
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "machine learning applications", "top_k": 10}'
```

Full API docs: http://localhost/docs

---

## Tech Stack

### Frontend
- **Framework:** Next.js 14 (React 18, TypeScript)
- **Styling:** Tailwind CSS
- **User Chat:** Real-time question input, streaming response display, source citations
- **Admin:** File upload, document table, ingest status polling (every 3s)

### Backend
- **Framework:** FastAPI (Python 3.11)
- **LLM:** Claude via TrollLLM proxy
- **RAG Pipeline:**
  - Embeddings: sentence-transformers (local, no API calls)
  - Dense search: Qdrant vector database
  - Sparse search: BM25 (rank-bm25 library)
  - Reranking: cross-encoder (sentence-transformers)
  - Fusion: reciprocal rank fusion (RRF)
- **Text Processing:**
  - PDF: pdfplumber
  - DOCX: python-docx
  - TXT/MD: standard file I/O
  - Chunking: sliding window with overlap

### Infrastructure
- **Databases:**
  - PostgreSQL 15 — document metadata, conversation history
  - Qdrant — vector storage (embeddings)
  - Redis 7 — message queue (Celery), caching
- **Storage:** MinIO — S3-compatible object storage (uploaded files)
- **Task Queue:** Celery + Redis — background document processing
- **Reverse Proxy:** Nginx 1.27 — routing and compression
- **Orchestration:** Docker Compose

---

## Environment Variables

Copy `.env.example` to `.env` and fill in:

```bash
# Claude API (TrollLLM proxy)
ANTHROPIC_API_KEY=sk-trollllm-your-key-here
ANTHROPIC_BASE_URL=https://chat.trollllm.xyz
CLAUDE_MODEL=claude-sonnet-4-6

# Database credentials
POSTGRES_USER=raguser
POSTGRES_PASSWORD=ragpass
POSTGRES_DB=rag_db

# MinIO credentials (default, safe for local dev)
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin

# Docker Hub (for pushing images, optional)
DOCKER_USER=yourusername
```

**Never commit `.env` to Git** — it's in `.gitignore`. Only `.env.example` is tracked.

---

## Development

### Local testing (without Docker)

To run the backend + worker locally for faster iteration:

```bash
# Terminal 1: Backend API
uvicorn backend.main:app --reload --port 8000

# Terminal 2: Worker
celery -A worker.tasks.celery_app worker --loglevel=info --pool=solo

# Terminal 3: MinIO, Qdrant, PostgreSQL (still in Docker)
docker compose up postgres qdrant minio redis
```

### Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f backend
docker compose logs -f worker
docker compose logs -f frontend-user
```

### Rebuilding after code changes

Backend/worker code changes:
```bash
docker compose build backend worker
docker compose up -d
```

Frontend code changes:
```bash
docker compose build frontend-user frontend-admin
docker compose up -d
```

All at once:
```bash
docker compose build
docker compose up -d
```

### Performance tuning

- **BM25 slow on first query?** It rebuilds the index from Qdrant. After the first search, it's fast. Or set `use_rerank=False` in `hybrid_search()` to skip the cross-encoder.
- **Out of memory?** Increase Docker Desktop memory to 4+ GB in Settings → Resources. The cross-encoder + embedding model together use ~2-3 GB.
- **Worker stuck?** Make sure it's running: `docker compose logs -f worker`. Celery needs Redis healthy.

---

## Deployment

### Push images to Docker Hub

```bash
# 1. Log in
docker login

# 2. Update .env with your username
DOCKER_USER=your_dockerhub_username

# 3. Build and push
docker compose build
docker compose push
```

Images on Docker Hub:
- `your_username/rag-backend:latest`
- `your_username/rag-worker:latest`
- `your_username/rag-frontend-user:latest`
- `your_username/rag-frontend-admin:latest`
- `your_username/rag-nginx:latest`

### On a fresh machine

```bash
docker compose pull
docker compose up -d
```

---

## Team

| Name | Role |
|------|------|
| [Your Name] | Backend + RAG Pipeline |
| [Team Member 2] | Frontend User |
| [Team Member 3] | Frontend Admin |
| [Team Member 4] | DevOps + Deployment |

**Advisor:** [Professor Name]

---

## Assignment Completion Checklist

- [x] GitHub private repo (professors invited)
- [x] `docker compose up -d` works on fresh system
- [x] `.env.example` present (no real secrets committed)
- [x] All 5 Docker images on Docker Hub
- [x] README with architecture diagram
- [x] All 8 services running (nginx, 2 frontends, backend, worker, postgres, qdrant, minio, redis)
- [x] RAG pipeline complete:
  - [x] Document upload & ingest (extract → chunk → embed)
  - [x] Dense semantic search (Qdrant)
  - [x] Sparse keyword search (BM25)
  - [x] RRF fusion
  - [x] Cross-encoder reranking
  - [x] Grounded Q&A with source citations
- [x] User-facing chat UI
- [x] Admin document management UI
- [x] Reverse proxy load balancer (Nginx)

---

## Troubleshooting

**"docker compose up -d" hangs on Next.js build**
- First build downloads ~500 MB of npm packages. Wait 10 minutes.

**Backend can't connect to PostgreSQL**
- Make sure `postgres` container is healthy: `docker compose logs postgres`
- Check that `DATABASE_URL` in docker-compose uses `postgres:5432` (not `localhost`)

**Worker not processing documents**
- Check Redis: `docker compose logs redis`
- Check worker: `docker compose logs worker`
- Make sure Celery config matches in both backend and worker

**Uploads fail with "MinIO error"**
- Restart MinIO: `docker compose restart minio`
- Check MinIO console: http://localhost:9001

**Out of memory during first query**
- Increase Docker memory: Settings → Resources → 4+ GB
- Or disable reranking in `hybrid_search()` for now

**API returning 502 errors**
- Check backend logs: `docker compose logs backend`
- Verify all storage services are healthy: `docker compose ps`
