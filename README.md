# 📚 RAG Knowledge Base System

> Hệ thống Retrieval-Augmented Generation (RAG) end-to-end — hỏi đáp thông minh dựa trên kho tài liệu nội bộ, tích hợp đầy đủ pipeline ingest → embed → query → answer với giao diện người dùng và quản trị viên.

---

## Tiến độ dự án (Progress)

### ✅ Đã hoàn thành (Infrastructure & Foundation)
- **Lưu trữ (Storage & Database) - Đã chạy:**
  - Thiết lập **PostgreSQL 15** (Metadata, người dùng, tài liệu).
  - Thiết lập **MinIO** cho Object Storage (Lưu file tài liệu gốc tải lên).
  - Thiết lập **Qdrant** làm Vector Database (Lưu trữ document embeddings).
- **Message Broker & Queue - Đã chạy:**
  - Thiết lập **Redis** và kết nối thành công với **Celery Worker** để xử lý nền bất đồng bộ.
- **Backend API Foundation - Đã chạy:**
  - Hệ thống `docker-compose.yml` định nghĩa đẩy đủ các Docker container cho hạ tầng.
  - Khởi tạo FastAPI app với checkpoint kết nối (`/health`) check toàn vẹn DB, Redis, MinIO, Qdrant.
  - Sử dụng **Python 3.11.7**, `psycopg2-binary`, và `SQLAlchemy`.

---

### 🚧 Các công việc cần làm tiếp theo (To-Do)

Để hoàn thiện kiến trúc, hệ thống cần tiếp tục code các hạng mục sau:

#### 1. NGINX Load Balancer
- Tạo cấu hình `nginx.conf` thiết lập Reverse Proxy cho API và Frontend.

#### 2. Xây dựng Frontend UI (Next.js/React)
- **Frontend User:** Xây dựng màn hình chat streaming (hiển thị typing) và truy xuất nguồn trích dẫn.
- **Frontend Admin:** Xây dựng dashboard thêm/xóa file tài liệu, giám sát tiến trình Ingest, cấp quyền người dùng.

#### 3. Phát triển Pipeline RAG chuyên sâu (Backend FastAPI)
- **Celery Ingest Pipeline:** Khai báo hàm bóc tách văn bản (PDF, DOCX) → Chunking (chia text) → Vector hóa (Embedding model) → Upsert vào Qdrant DB.
- **FastAPI Retrieval:** API nhận câu hỏi → Qdrant tìm chunks có độ tương đồng → Đưa vào LLM Prompt (LangChain API/OpenAI) → Trả về streaming + Citation.

#### 4. Điểm Cộng Bổ Sung (Tùy chọn)
- Chạy script backup DB định kỳ (PostgreSQL, MinIO).
- Triển khai caching cho các câu hỏi trùng lặp với Redis.

---

## Hướng dẫn chạy thử nghiệm hạ tầng hiện tại (Local)

**1. Khởi động Docker Compose:**
```bash
cd ai-system-hhnm/rag-system
cp .env.example .env
docker compose up -d
```

**2. Cài đặt môi trường Python (Yêu cầu Python 3.11.7):**
```bash
cd backend
conda create -y -n rag-311 python=3.11.7
conda activate rag-311
pip install -r requirements.txt
```

**3. Chạy FastAPI Server (API Health Testing):**
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
Truy cập: [http://localhost:8000/health](http://localhost:8000/health) để thấy trạng thái xanh (healthy) toàn bộ.

**4. Chạy Celery Queue Worker (Background Tasks):**
```bash
conda activate rag-311
cd ai-system-hhnm/rag-system/backend
celery -A app.worker:celery_app worker --loglevel=info --pool=solo
```

---

## Thành viên nhóm

| STT | Họ và tên | MSSV | Nhiệm vụ |
|-----|-----------|------|---------|
| 1 | Nguyễn Minh Huy | 24022356 | |
| 2 | Cao Huy Hòa | 24022332 | |
| 3 | Phạm Vũ Nam | 24022416 | |
| 4 | Đinh Quang Minh | 24022398 | |

> **Giảng viên hướng dẫn:** [Tên giảng viên] — Đã được thêm quyền đọc vào repository.