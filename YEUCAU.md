Nhiệm Vụ
Hệ thống RAG (Retrieval-Augmented Generation)
Hệ thống hỏi–đáp dựa trên tài liệu nội bộ: ingest tài liệu (PDF, web, văn bản…), chunking, embedding, lưu vector, truy vấn ngữ nghĩa và sinh câu trả lời có trích dẫn nguồn. Queue nền dùng cho ingest / tái embedding khi cập nhật kho tri thức.

Tham khảo (chọn stack phù hợp): LangChain — RAG tutorial, LlamaIndex, hoặc tự lắp pipeline với API LLM + vector DB đã nêu ở mục yêu cầu kỹ thuật.

Trường hợp 1: Trợ lý nội bộ doanh nghiệp (hỏi đáp trên quy trình, hướng dẫn nội bộ, wiki).
Trường hợp 2: Hỗ trợ tra cứu học thuật / tài liệu môn học (hỏi theo nội dung giáo trình, bài tập, đề cương đã được cung cấp).
Công Việc
1. Phát triển ứng dụng
Tùy nhiệm vụ đã chọn ở mục 1, triển khai đầy đủ chức năng end-to-end. Với nhánh khuôn mặt / biển số: xây dựng xử lý hình ảnh trong thời gian thực. Với nhánh RAG: xây dựng luồng ingest → truy vấn → trả lời (có thể hiển thị streaming), kèm quản lý kho tài liệu. Cần TỐI THIỂU các image (Docker image / container tương ứng từng phần), gồm các thành phần sau:

Frontend người dùng: với vision — hiển thị luồng xử lý ảnh thời gian thực và kết quả phù hợp use case; với RAG — giao diện đặt câu hỏi, hiển thị câu trả lời và (tối thiểu) nguồn trích dẫn / đoạn liên quan.
Frontend quản trị (admin): thêm/xóa người dùng, quyền, cấu hình hệ thống; với RAG — quản lý tài liệu / kho tri thức (tải lên, xóa, tái index), trạng thái ingest.
Backend: logic nghiệp vụ, API kết nối các phần (vision hoặc RAG).
Lưu trữ: tận dụng tối thiểu ba loại:
Database (SQL, NoSQL, …)
Object storage (S3, file storage, MinIO, …) — lưu file gốc / snapshot tài liệu
Vector database (Qdrant, Pinecone, Milvus, FAISS, …) — embedding cho RAG hoặc đặc trưng cần vector hoá
Message / event queue cho tác vụ nền (ví dụ: chunking → embedding → lưu vector; tái xử lý khi đổi tài liệu, những tác vụ như vậy cần được chạy trong nền thay vì phải để người dùng chờ API hoạt động xong, làm ảnh hưởng đến UX).
Load balancer (nginx) điều phối request tới frontend, backend và dịch vụ khác (nếu cần).
Điểm cộng (không bắt buộc) - Sinh viên có thể chọn một hoặc nhiều phương án sau:

Xây dựng luồng CI/CD hoàn chỉnh
Triển khai hệ thống lên Kubernetes sử dụng Helm chart
Xây dựng luồng giám sát (monitoring) (vd: Kibana, Grafana, Prometheus, …)
Quản lý phiên bản, cấu hình cho mô hình và pipeline dữ liệu
Xây dựng luồng backup dữ liệu (vd: backup DB, backup vector database, backup tài liệu, …) (Một số công cụ tham khảo vd: Velero (backup K8s), PG Back Web (backup Postgres DB to S3), ...)
Xây dựng luồng cache đa lớp (client, application, DB) (vd: Redis, Memcached, …)
Hệ thống hoạt động hiệu quả (Cho phép hỗ trợ nhiều người dùng cùng lúc, latency thấp, accuracy của mô hình cao, Đảm bảo backup dữ liệu)
Triển khai hệ thống lên Internet (có domain, SSL (nginx + letsencrypt), …) (có thể dùng tunnel: Cloudflare, Ngrok, …)
2. Đóng gói trong Docker
Dùng Docker Compose để quản lý và đóng gói toàn bộ hệ thống.

3. Đẩy code lên GitHub
Thực hiện theo mục Thông tin chung: repo private, mời thành viên nhóm và thêm quyền đọc cho tài khoản giảng viên như đã nêu.

Đảm bảo README mô tả kiến trúc, cách chạy và đủ thông tin để tái hiện hệ thống (phù hợp tiêu chí Tài liệu trong rubric).