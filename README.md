# 📚 RAG Knowledge Base System

> Hệ thống Retrieval-Augmented Generation (RAG) end-to-end — hỏi đáp thông minh dựa trên kho tài liệu nội bộ, tích hợp đầy đủ pipeline ingest → embed → query → answer với giao diện người dùng và quản trị viên.

---

## 📋 Mục lục

- [Tổng quan kiến trúc](#tổng-quan-kiến-trúc)
- [Thành phần hệ thống](#thành-phần-hệ-thống)
- [Cấu trúc thư mục](#cấu-trúc-thư-mục)
- [Yêu cầu hệ thống](#yêu-cầu-hệ-thống)
- [Hướng dẫn cài đặt & chạy](#hướng-dẫn-cài-đặt--chạy)
- [Biến môi trường](#biến-môi-trường)
- [Tài khoản mặc định](#tài-khoản-mặc-định)
- [API Reference](#api-reference)
- [Luồng hoạt động](#luồng-hoạt-động)
- [Điểm cộng đã triển khai](#điểm-cộng-đã-triển-khai)
- [Thành viên nhóm](#thành-viên-nhóm)

---

## Tổng quan kiến trúc

```
                        ┌─────────────────────────────────────────────────────┐
                        │                  NGINX Load Balancer                 │
                        │              (Port 80 / 443 với SSL)                │
                        └────────┬──────────────────┬──────────────────┬──────┘
                                 │                  │                  │
                    ┌────────────▼────┐   ┌─────────▼──────┐  ┌───────▼──────┐
                    │  User Frontend  │   │ Admin Frontend │  │   Backend    │
                    │   (Next.js)     │   │   (Next.js)    │  │  (FastAPI)   │
                    │   Port: 3000    │   │   Port: 3001   │  │  Port: 8000  │
                    └────────────┬────┘   └─────────┬──────┘  └───────┬──────┘
                                 │                  │                  │
                                 └──────────────────┴──────────────────┘
                                                    │
                    ┌───────────────────────────────▼───────────────────────────────┐
                    │                        Backend Services                        │
                    │                                                                │
                    │   ┌─────────────┐   ┌─────────────┐   ┌──────────────────┐  │
                    │   │  PostgreSQL  │   │    Redis     │   │     MinIO        │  │
                    │   │  (Port 5432) │   │  (Port 6379) │   │  (Port 9000)     │  │
                    │   │  Users, docs │   │  Cache/Queue │   │  File Storage    │  │
                    │   └─────────────┘   └─────────────┘   └──────────────────┘  │
                    │                                                                │
                    │   ┌─────────────┐   ┌─────────────┐   ┌──────────────────┐  │
                    │   │   Qdrant    │   │   Celery     │   │  Celery Worker   │  │
                    │   │  (Port 6333) │   │   Broker     │   │  (Ingest Tasks)  │  │
                    │   │  Vector DB  │   │  (Redis)     │   │                  │  │
                    │   └─────────────┘   └─────────────┘   └──────────────────┘  │
                    └───────────────────────────────────────────────────────────────┘
```

---

## Thành phần hệ thống

### 1. Frontend người dùng (`/frontend-user`)
- **Công nghệ:** Next.js 14, TailwindCSS, shadcn/ui
- **Chức năng:**
  - Giao diện chat hỏi đáp với AI
  - Hiển thị câu trả lời **streaming** theo thời gian thực
  - Hiển thị **nguồn trích dẫn** và đoạn văn liên quan (với tên tài liệu, trang, mức độ liên quan)
  - Lịch sử hội thoại
  - Đăng nhập / Đăng ký người dùng

### 2. Frontend quản trị (`/frontend-admin`)
- **Công nghệ:** Next.js 14, TailwindCSS, shadcn/ui
- **Chức năng:**
  - Quản lý người dùng: thêm, xóa, chỉnh sửa, phân quyền (admin / user)
  - Quản lý tài liệu: tải lên (PDF, DOCX, TXT, MD), xóa, tái index
  - Giám sát trạng thái ingest (pending → processing → completed / failed)
  - Cấu hình hệ thống: LLM model, chunk size, overlap, top-k retrieval
  - Thống kê sử dụng: số lượt truy vấn, tài liệu, người dùng

### 3. Backend API (`/backend`)
- **Công nghệ:** FastAPI, LangChain, Python 3.11
- **Chức năng:**
  - REST API + WebSocket (streaming)
  - Pipeline RAG: query → embed → retrieve → rerank → generate
  - Xác thực JWT
  - Phân quyền RBAC
  - Celery task dispatch

### 4. Celery Worker (`/worker`)
- **Công nghệ:** Celery, Python 3.11
- **Chức năng:**
  - Xử lý tác vụ nền: chunking → embedding → upsert vào Qdrant
  - Tái ingest khi tài liệu thay đổi
  - Retry logic cho các tác vụ thất bại

### 5. Lưu trữ

| Loại | Công nghệ | Mục đích |
|------|-----------|---------|
| SQL Database | PostgreSQL 15 | Users, documents metadata, conversations, permissions |
| Object Storage | MinIO | File gốc (PDF, DOCX, …), snapshot tài liệu |
| Vector Database | Qdrant | Embedding vectors cho RAG retrieval |
| Cache / Queue | Redis 7 | Session cache, Celery broker & result backend |

### 6. Load Balancer (`/nginx`)
- **Công nghệ:** Nginx 1.25
- **Chức năng:**
  - Reverse proxy tới frontend và backend
  - SSL termination (Let's Encrypt / self-signed)
  - Rate limiting
  - Gzip compression

---

## Cấu trúc thư mục

```
rag-system/
├── docker-compose.yml              # Orchestration toàn bộ hệ thống
├── docker-compose.override.yml     # Override cho môi trường dev
├── .env.example                    # Template biến môi trường
├── README.md
│
├── nginx/
│   ├── Dockerfile
│   ├── nginx.conf                  # Cấu hình chính
│   └── conf.d/
│       ├── user.conf               # Proxy tới frontend-user
│       ├── admin.conf              # Proxy tới frontend-admin
│       └── api.conf                # Proxy tới backend API
│
├── frontend-user/
│   ├── Dockerfile
│   ├── package.json
│   ├── next.config.js
│   └── src/
│       ├── app/
│       │   ├── (auth)/             # Login / Register pages
│       │   ├── chat/               # Chat interface
│       │   └── layout.tsx
│       ├── components/
│       │   ├── ChatWindow.tsx      # Streaming chat UI
│       │   ├── SourcePanel.tsx     # Hiển thị nguồn trích dẫn
│       │   └── MessageBubble.tsx
│       └── lib/
│           ├── api.ts
│           └── streaming.ts        # SSE / WebSocket handler
│
├── frontend-admin/
│   ├── Dockerfile
│   ├── package.json
│   └── src/
│       ├── app/
│       │   ├── dashboard/          # Tổng quan
│       │   ├── documents/          # Quản lý tài liệu
│       │   ├── users/              # Quản lý người dùng
│       │   └── settings/           # Cấu hình hệ thống
│       └── components/
│           ├── DocumentUploader.tsx
│           ├── IngestStatus.tsx    # Trạng thái ingest real-time
│           └── UserTable.tsx
│
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── alembic/                    # Database migrations
│   │   ├── versions/
│   │   └── env.py
│   └── app/
│       ├── main.py                 # FastAPI entry point
│       ├── api/
│       │   ├── v1/
│       │   │   ├── auth.py         # Login, register, refresh token
│       │   │   ├── chat.py         # Query endpoint (streaming)
│       │   │   ├── documents.py    # Upload, delete, list, re-index
│       │   │   ├── users.py        # CRUD users (admin only)
│       │   │   └── settings.py     # System config
│       ├── core/
│       │   ├── config.py           # Settings từ .env
│       │   ├── security.py         # JWT, password hashing
│       │   └── deps.py             # Dependency injection
│       ├── models/
│       │   ├── user.py             # SQLAlchemy models
│       │   ├── document.py
│       │   └── conversation.py
│       ├── rag/
│       │   ├── embedder.py         # Text → vector embedding
│       │   ├── retriever.py        # Qdrant similarity search
│       │   ├── reranker.py         # Cross-encoder reranking
│       │   ├── generator.py        # LLM streaming generation
│       │   └── pipeline.py         # End-to-end RAG pipeline
│       ├── storage/
│       │   ├── minio_client.py     # MinIO operations
│       │   ├── qdrant_client.py    # Qdrant operations
│       │   └── postgres.py         # SQLAlchemy session
│       └── tasks/
│           └── ingest.py           # Celery task definitions
│
├── worker/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── tasks/
│       ├── ingest_pipeline.py      # Chunking → embedding → upsert
│       ├── reindex.py              # Tái index tài liệu
│       └── cleanup.py             # Xóa vector khi xóa tài liệu
│
├── db/
│   └── init.sql                    # Khởi tạo schema PostgreSQL
│
└── scripts/
    ├── seed_admin.py               # Tạo tài khoản admin mặc định
    ├── healthcheck.sh              # Kiểm tra trạng thái services
    └── backup.sh                   # Backup DB + MinIO + Qdrant
```

---

## Yêu cầu hệ thống

| Thành phần | Phiên bản tối thiểu |
|------------|-------------------|
| Docker | 24.0+ |
| Docker Compose | 2.20+ |
| RAM | 8 GB (khuyến nghị 16 GB) |
| Disk | 20 GB trống |
| OS | Linux / macOS / Windows (WSL2) |

---

## Hướng dẫn cài đặt & chạy

### Bước 1: Clone repository

```bash
git clone https://github.com/<your-org>/rag-system.git
cd rag-system
```

### Bước 2: Tạo file cấu hình môi trường

```bash
cp .env.example .env
```

Chỉnh sửa `.env` theo môi trường của bạn (xem mục [Biến môi trường](#biến-môi-trường)).

### Bước 3: Build và khởi động hệ thống

```bash
# Build tất cả images
docker compose build

# Khởi động toàn bộ hệ thống (detached mode)
docker compose up -d

# Kiểm tra trạng thái các container
docker compose ps
```

### Bước 4: Khởi tạo database & tài khoản admin

```bash
# Chạy migration database
docker compose exec backend alembic upgrade head

# Tạo tài khoản admin mặc định
docker compose exec backend python scripts/seed_admin.py
```

### Bước 5: Truy cập hệ thống

| Dịch vụ | URL | Ghi chú |
|---------|-----|---------|
| User Frontend | http://localhost:3000 | Giao diện người dùng |
| Admin Frontend | http://localhost:3001 | Giao diện quản trị |
| Backend API | http://localhost:8000 | REST API |
| API Docs (Swagger) | http://localhost:8000/docs | Tài liệu API tương tác |
| MinIO Console | http://localhost:9001 | Quản lý object storage |
| Qdrant Dashboard | http://localhost:6333/dashboard | Quản lý vector DB |

### Lệnh quản lý thường dùng

```bash
# Xem log của một service
docker compose logs -f backend
docker compose logs -f worker

# Restart một service
docker compose restart backend

# Dừng toàn bộ hệ thống (giữ volume)
docker compose down

# Dừng và xóa toàn bộ dữ liệu (NGUY HIỂM)
docker compose down -v

# Scale worker (nếu cần xử lý nhiều tài liệu)
docker compose up -d --scale worker=3

# Chạy backup thủ công
docker compose exec backend bash /app/scripts/backup.sh
```

---

## Biến môi trường

Tạo file `.env` từ `.env.example` và điền các giá trị sau:

```env
# ─── PostgreSQL ───────────────────────────────
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=ragdb
POSTGRES_USER=raguser
POSTGRES_PASSWORD=change_me_strong_password

# ─── Redis ────────────────────────────────────
REDIS_URL=redis://redis:6379/0

# ─── MinIO ────────────────────────────────────
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=change_me_minio_secret
MINIO_BUCKET_DOCUMENTS=documents
MINIO_BUCKET_SNAPSHOTS=snapshots

# ─── Qdrant ───────────────────────────────────
QDRANT_HOST=qdrant
QDRANT_PORT=6333
QDRANT_COLLECTION=rag_documents

# ─── LLM (chọn một trong các provider) ────────
# Option 1: OpenAI
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

# Option 2: Local (Ollama)
# LLM_PROVIDER=ollama
# OLLAMA_BASE_URL=http://ollama:11434
# OLLAMA_MODEL=llama3.2

# ─── Embedding Model ──────────────────────────
EMBEDDING_MODEL=text-embedding-3-small   # hoặc "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIMENSION=1536

# ─── RAG Config ───────────────────────────────
CHUNK_SIZE=512
CHUNK_OVERLAP=64
RETRIEVAL_TOP_K=5
RERANKER_ENABLED=true

# ─── JWT Auth ─────────────────────────────────
JWT_SECRET_KEY=change_me_jwt_secret_very_long_random_string
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60
REFRESH_TOKEN_EXPIRE_DAYS=7

# ─── Admin mặc định (seed) ────────────────────
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=Admin@123456
```

---

## Tài khoản mặc định

Sau khi chạy `seed_admin.py`:

| Role | Email | Password |
|------|-------|----------|
| Admin | admin@example.com | Admin@123456 |

> **Lưu ý:** Đổi mật khẩu ngay sau lần đăng nhập đầu tiên.

---

## API Reference

Tài liệu API đầy đủ có tại: **http://localhost:8000/docs** (Swagger UI)

### Các endpoint chính

```
# Xác thực
POST   /api/v1/auth/register        Đăng ký tài khoản
POST   /api/v1/auth/login           Đăng nhập, nhận JWT
POST   /api/v1/auth/refresh         Làm mới access token

# Chat (RAG)
POST   /api/v1/chat/query           Hỏi đáp (response bình thường)
POST   /api/v1/chat/stream          Hỏi đáp (streaming SSE)
GET    /api/v1/chat/history         Lịch sử hội thoại

# Tài liệu (Admin)
GET    /api/v1/documents            Danh sách tài liệu
POST   /api/v1/documents/upload     Tải lên tài liệu (multipart)
DELETE /api/v1/documents/{id}       Xóa tài liệu + vector
POST   /api/v1/documents/{id}/reindex  Tái index tài liệu
GET    /api/v1/documents/{id}/status   Trạng thái ingest

# Người dùng (Admin)
GET    /api/v1/users                Danh sách người dùng
POST   /api/v1/users                Tạo người dùng
PUT    /api/v1/users/{id}           Cập nhật thông tin / quyền
DELETE /api/v1/users/{id}           Xóa người dùng

# Cấu hình (Admin)
GET    /api/v1/settings             Xem cấu hình hiện tại
PUT    /api/v1/settings             Cập nhật cấu hình
```

---

## Luồng hoạt động

### A. Luồng Ingest tài liệu (chạy nền)

```
Admin upload file
     │
     ▼
Backend nhận file
     │
     ├─► Lưu file gốc → MinIO (bucket: documents)
     ├─► Tạo record → PostgreSQL (status: pending)
     └─► Dispatch Celery task
              │
              ▼
         Celery Worker
              │
              ├─ 1. Parse file (PDF/DOCX/TXT → text)
              ├─ 2. Chunk text (size/overlap từ config)
              ├─ 3. Embed chunks (Embedding model)
              ├─ 4. Upsert vectors → Qdrant
              ├─ 5. Cập nhật status → PostgreSQL (completed)
              └─ 6. (Nếu lỗi) Retry & cập nhật status: failed
```

### B. Luồng RAG Query (streaming)

```
User gửi câu hỏi
     │
     ▼
Backend nhận query
     │
     ├─ 1. Embed query → vector
     ├─ 2. Similarity search → Qdrant (top-k chunks)
     ├─ 3. Rerank kết quả (cross-encoder)
     ├─ 4. Build prompt (system + context + query)
     ├─ 5. Stream LLM response → SSE
     └─ 6. Lưu conversation → PostgreSQL
              │
              ▼
         Frontend nhận stream
              │
              ├─ Hiển thị token dần dần
              └─ Hiển thị nguồn trích dẫn (sau khi stream xong)
```

---

## Điểm cộng đã triển khai

### Cache đa lớp (Redis)
- **Client cache:** HTTP cache headers (ETag, Cache-Control)
- **Application cache:** Redis cache cho kết quả embedding và truy vấn lặp lại
- **Session cache:** Lưu JWT session trong Redis

### Monitoring (Prometheus + Grafana)
```bash
# Khởi động thêm stack monitoring
docker compose --profile monitoring up -d
```
- Grafana: http://localhost:3100 (admin / admin)
- Prometheus: http://localhost:9090
- Dashboard có sẵn: API latency, Celery queue depth, DB connections

### Backup tự động
- Backup PostgreSQL → MinIO (hằng ngày lúc 02:00)
- Backup Qdrant snapshot → MinIO (hằng ngày lúc 02:30)
- Script thủ công: `docker compose exec backend bash /app/scripts/backup.sh`

### CI/CD (GitHub Actions)
```
.github/workflows/
├── ci.yml          # Lint, test khi push/PR
└── deploy.yml      # Build & push Docker images khi merge vào main
```

---

## Docker Images

| Service | Image | Port |
|---------|-------|------|
| nginx | `nginx:1.25-alpine` (custom) | 80, 443 |
| frontend-user | `node:20-alpine` (custom) | 3000 |
| frontend-admin | `node:20-alpine` (custom) | 3001 |
| backend | `python:3.11-slim` (custom) | 8000 |
| worker | `python:3.11-slim` (custom) | — |
| postgres | `postgres:15-alpine` | 5432 |
| redis | `redis:7-alpine` | 6379 |
| minio | `minio/minio:latest` | 9000, 9001 |
| qdrant | `qdrant/qdrant:latest` | 6333 |

---

## Thành viên nhóm

| STT | Họ và tên | MSSV | Nhiệm vụ |
|-----|-----------|------|---------|
| 1 | Nguyễn Văn A | 2021XXXXXX | Backend API, RAG Pipeline |
| 2 | Trần Thị B | 2021XXXXXX | Frontend User, Streaming UI |
| 3 | Lê Văn C | 2021XXXXXX | Frontend Admin, Docker Compose |
| 4 | Phạm Thị D | 2021XXXXXX | Celery Worker, Ingest Pipeline |

> **Giảng viên hướng dẫn:** [Tên giảng viên] — Đã được thêm quyền đọc vào repository.

---

## Giấy phép

MIT License — Xem file [LICENSE](./LICENSE) để biết thêm chi tiết.
