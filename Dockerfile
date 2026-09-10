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

# Bốn file ở thư mục gốc, KHÔNG PHẢI MỘT.
#   run.py           điểm vào.
#   scheduler.py     run.py `import scheduler` ngay ở đầu file — thiếu nó là
#                    container chết lúc khởi động, ModuleNotFoundError.
#   send_report.py   scheduler gọi tới khi REPORT_SCHEDULE khác "off".
#   report_layout.py send_report gọi tới để dựng nội dung thư.
#
# CẨN THẬN KHI SỬA DÒNG NÀY: healthcheck bên dưới chỉ `import config`, nên
# thiếu một trong bốn file thì container chết mà healthcheck vẫn báo khỏe.
# Kiểm bằng cách chạy thật: docker compose up rồi đọc docker logs.
COPY run.py scheduler.py send_report.py report_layout.py ./

# ---- Chạy bằng user thường, không phải root ----
RUN useradd --create-home --shell /bin/bash mooc \
    && chown -R mooc:mooc /app
USER mooc

# Đọc được config = .env đã gắn đúng và thư viện nạp được.
# Không gọi API ELIS để khỏi tốn request vô ích.
HEALTHCHECK --interval=5m --timeout=30s --start-period=30s --retries=3 \
    CMD python -c "import sys; sys.path.insert(0,'src'); import config" || exit 1

# Ghi rõ "loop" cho tường minh, dù run.py nay đã mặc định chạy liên tục.
# Container PHẢI chạy tiến trình sống mãi: nếu nó xử lý một mẻ rồi thoát thì
# restart policy sẽ dựng lại liên tục, thành vòng lặp KHỞI ĐỘNG chứ không
# phải vòng lặp poll.
CMD ["python", "run.py", "loop"]
