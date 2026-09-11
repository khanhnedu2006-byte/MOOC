# Job AI scan chứng chỉ MOOC — vòng lặp nền nối với ELIS.
#
# Build:  docker compose build
# Chạy :  docker compose up -d
#
# KHÔNG nhúng .env vào image. Key được gắn lúc chạy (xem docker-compose.yml).
# Lý do: image có thể bị push lên registry; key nằm trong layer thì ai kéo về
# cũng moi ra được, kể cả khi lệnh sau đó đã xóa file.

FROM python:3.13-slim

# ---- Gói hệ thống ----
# libmagic1: python-magic cần thư viện này để đoán loại file theo NỘI DUNG.
#            Thiếu nó, src/file_utils.py chết ngay lúc import.
# tzdata   : để log in đúng giờ Việt Nam thay vì UTC.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libmagic1 \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Ho_Chi_Minh

# PYTHONUNBUFFERED: in log ra ngay. Thiếu nó thì `docker logs` trống trơn
#     hàng phút dù job vẫn đang chạy.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    KMP_DUPLICATE_LIB_OK=TRUE

WORKDIR /app

# ---- Cài thư viện trước, copy code sau ----
# Tách hai bước để tận dụng cache: sửa code không phải cài lại thư viện.
COPY requirements-job.txt .
RUN pip install --no-cache-dir -r requirements-job.txt

# ---- Code ----
COPY src/ ./src/
COPY database/ ./database/

# Bốn file ở thư mục gốc:
#   run.py           điểm vào
#   scheduler.py     run.py import ngay ở đầu file
#   send_report.py   scheduler gọi khi REPORT_SCHEDULE khác "off"
#   report_layout.py send_report gọi để dựng nội dung thư
#
# Healthcheck bên dưới chỉ `import config`, nên thiếu một trong bốn file thì
# container chết mà healthcheck vẫn báo khỏe. Kiểm bằng docker compose logs.
COPY run.py scheduler.py send_report.py report_layout.py ./

# ---- Chạy bằng user thường, không phải root ----
#
# uid/gid phải khớp tài khoản chủ sở hữu các file gắn từ ngoài vào (.env,
# mooc_log.db, .report_state.json, .alert_state.json). Mặc định 1000 đúng với
# Docker Desktop và server mà tài khoản đầu tiên là 1000.
#
# Lệch uid ra lỗi không giống lỗi quyền:
#   - đọc .env: PermissionError: [Errno 13] Permission denied: '.env'
#   - ghi DB:   sqlite3.OperationalError: attempt to write a readonly database
#     (kể cả khi mooc_log.db ghi được — SQLite còn tạo file -journal trong
#     cùng thư mục, mà /app thuộc user trong image)
#
# Đổi bằng build arg, đừng dùng `user:` trong compose: `user:` chỉ đổi tiến
# trình chứ không đổi chủ sở hữu /app.
#   docker compose build --build-arg APP_UID=$(id -u) --build-arg APP_GID=$(id -g)
ARG APP_UID=1000
ARG APP_GID=1000
RUN if ! getent group ${APP_GID} >/dev/null; then groupadd -g ${APP_GID} mooc; fi \
    && useradd -u ${APP_UID} -g ${APP_GID} --create-home --shell /bin/bash mooc \
    && chown -R ${APP_UID}:${APP_GID} /app
USER ${APP_UID}:${APP_GID}

# Đọc được config = .env đã gắn đúng và thư viện nạp được.
# Không gọi API ELIS để khỏi tốn request vô ích.
HEALTHCHECK --interval=5m --timeout=30s --start-period=30s --retries=3 \
    CMD python -c "import sys; sys.path.insert(0,'src'); import config" || exit 1

# Ghi rõ "loop" cho tường minh, dù run.py nay đã mặc định chạy liên tục.
# Container PHẢI chạy tiến trình sống mãi: nếu nó xử lý một mẻ rồi thoát thì
# restart policy sẽ dựng lại liên tục, thành vòng lặp KHỞI ĐỘNG chứ không
# phải vòng lặp poll.
CMD ["python", "run.py", "loop"]
