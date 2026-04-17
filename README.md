# 📚 RAG Knowledge Base System

> Hệ thống Retrieval-Augmented Generation (RAG) end-to-end — hỏi đáp thông minh dựa trên kho tài liệu nội bộ, tích hợp đầy đủ pipeline ingest → embed → query → answer với giao diện người dùng và quản trị viên.

---

## 📋 Mục lục

- [Tiến độ dự án (Progress)](#tiến-độ-dự-án-progress)
- [Kiến trúc hệ thống](#kiến-trúc-hệ-thống)
- [Cấu trúc thư mục hiện tại](#cấu-trúc-thư-mục-hiện-tại)
- [Hướng dẫn chạy hệ thống (Local)](#hướng-dẫn-chạy-hệ-thống-local)
- [Các công việc cần làm (To-Do)](#các-công-việc-cần-làm-to-do)
- [Thành viên nhóm](#thành-viên-nhóm)

---

## Tiến độ dự án (Progress)

### ✅ Đã hoàn thành (Infrastructure & Foundation)
- **Lưu trữ (Storage & Database):**
  - Đã thiết lập **PostgreSQL 15** cho metadata, người dùng, tài liệu.
  - Đã thiết lập **MinIO** cho Object Storage (lưu file gốc PDF, DOCX,...).
  - Đã thiết lập **Qdrant** làm Vector Database lưu trữ embeddings.
- **Message Broker & Queue:**
  - Đã thiết lập **Redis** và kết nối thành công với **Celery Worker** xử lý nền.
- **Microservices & API Foundation:**
  - Đã đóng gói các dịch vụ hạ tầng trong `docker-compose.yml`.
  - Định hình cấu trúc thư mục cho **FastAPI Backend**, kết nối thành công với database, MinIO, Qdrant và Redis thông qua API `/health`.
  - Định hình cấu trúc **Celery Worker** và chạy thử nghiệm thành công nghiệm vụ đa luồng (dummy task).
- **Môi trường phát triển:**
  - Cấu hình Python 3.11.7.
  - Sử dụng `psycopg2-binary` và `sqlalchemy` kết nối cơ sở dữ liệu.

---

## Kiến trúc hệ thống

```
                        ┌─────────────────────────────────────────────────────┐
                        │                  NGINX Load Balancer                 │ (To-Do)
                        │              (Port 80 / 443 với SSL)                │
                        └────────┬──────────────────┬──────────────────┬──────┘
                                 │                  │                  │
                    ┌────────────▼────┐   ┌─────────▼──────┐  ┌───────▼──────┐
                    │  User Frontend  │   │ Admin Frontend │  │   Backend    │
                    │   (Next.js)     │   │   (Next.js)    │  │  (FastAPI)   │
                    │   Port: 3000    │   │   Port: 3001   │  │  Port: 8000  │
                    │    (To-Do)      │   │    (To-Do)     │  │ (Đang code)  │
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
                    │   │    (Done)    │   │   (Done)     │   │     (Done)       │  │
                    │   └─────────────┘   └─────────────┘   └──────────────────┘  │
                    │                                                                │
                    │   ┌─────────────┐   ┌─────────────┐   ┌──────────────────┐  │
                    │   │   Qdrant    │   │   Celery     │   │  Celery Worker   │  │
                    │   │  (Port 6333) │   │   Broker     │   │  (Ingest Tasks)  │  │
                    │   │    (Done)    │   │  (Redis)     │   │     (Done)       │  │
                    │   └─────────────┘   └─────────────┘   └──────────────────┘  │
                    └───────────────────────────────────────────────────────────────┘
```

---

## Cấu trúc thư mục hiện tại

```
rag-system/
├── docker-compose.yml              # Orchestration toàn bộ hệ thống lưu trữ
├── .env.example                    # Template biến môi trường
├── .gitignore                      # Git ignore file
├── db/
│   └── init.sql                    # Script khởi tạo schema PostgreSQL ban đầu (Users, Docs)
└── backend/
    ├── requirements.txt            # Package dependencies (FastAPI, SQLAlchemy, Celery, ...)
    └── app/
        ├── main.py                 # FastAPI app với /health test connection
        ├── worker.py               # Celery worker configuration + dummy queue task
        └── core/
            └── config.py           # Quản lý settings từ biến môi trường
```

---

## Hướng dẫn chạy nghiệm thu hạ tầng (Local)

**Bước 1: Khởi động hệ thống Docker (Databases, MinIO, Redis)**
```bash
cd ai-system-hhnm/rag-system
cp .env.example .env
docker compose up -d
```

**Bước 2: Cài đặt thư viện Python (yêu cầu Python 3.11.7)**
```bash
cd backend
conda create -y -n rag-311 python=3.11.7
conda activate rag-311
pip install -r requirements.txt
```

**Bước 3: Chạy FastAPI server**
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
Truy cập: [http://localhost:8000/health](http://localhost:8000/health) để thấy trạng thái "healthy" của Postges, Redis, MinIO, Qdrant.

**Bước 4: Chạy Celery Worker (Cửa sổ terminal mới)**
```bash
conda activate rag-311
cd ai-system-hhnm/rag-system/backend
celery -A app.worker:celery_app worker --loglevel=info --pool=solo
```

---

## Các công việc cần làm (To-Do)

Để đáp ứng đầy đủ tài liệu `YEUCAU.md`, hệ thống sẽ tiếp tục phát triển các phần sau:

### 1. NGINX Load Balancer
- Tạo file cấu hình `nginx.conf` đóng vai trò reverse proxy định tuyến traffic đến Frontend User, Frontend Admin, và Backend API.

### 2. Xây dựng Frontend
- **Frontend người dùng (User):** Giao diện chat (RAG), hỗ trợ streaming câu trả lời và hiển thị trích dẫn nguồn văn bản (Next.js/React).
- **Frontend quản trị viên (Admin):** Giao diện quản lý danh sách tải lên tài liệu, theo dõi trạng thái background worker (đang xử lý/thành công), quản lý User.

### 3. Pipeline RAG và Logic Cốt Lõi (Backend FastAPI & Celery)
- **Ingest Pipeline (Nền - Celery):** Tích hợp code LangChain để đọc tài liệu (.pdf, .txt...), chia nhỏ đoạn văn bản (chunking), vector hóa (embedding) thông qua thư viện cụ thể và upsert vào Qdrant.
- **Retrieve & Generate (Realtime - FastAPI):** Endpoint xử lý câu hỏi người dùng, tương tác Qdrant tìm chunks liên quan, và gọi LLM (Large Language Model) sinh câu trả lời có trích dẫn.

### 4. Điểm Cộng (Các tính năng thêm)
- Xây dựng file backup toàn bộ dữ liệu.
- Định cấu hình Redis làm tầng cache (Application Cache) để tăng tốc độ phản hồi câu hỏi trùng lặp.
- Thiết lập CI/CD, SSL/domain.

---

## Thành viên nhóm

| STT | Họ và tên | MSSV | Nhiệm vụ |
|-----|-----------|------|---------|
| 1 | Nguyễn Minh Huy | 24022356 | |
| 2 | Cao Huy Hòa | 2021XXXXXX | |
| 3 | Phạm Vũ Nam | 2021XXXXXX | |
| 4 | Đinh Quang Minh | 24022398 |  |

> **Giảng viên hướng dẫn:** [Tên giảng viên] — Đã được thêm quyền đọc vào repository.
