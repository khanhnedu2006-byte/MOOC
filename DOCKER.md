# Chạy job bằng Docker

## Ba lệnh cần nhớ

```bash
docker compose up -d --build    # dựng image và chạy nền
docker compose logs -f          # xem log trực tiếp (Ctrl+C để thoát, job vẫn chạy)
docker compose down             # dừng hẳn
```

Lần đầu build mất vài phút vì phải tải thư viện. Những lần sau, nếu chỉ sửa
code mà không đụng `requirements-job.txt` thì build lại chỉ mất vài giây —
Docker dùng lại lớp đã cài thư viện.

## Trước khi build

`mooc_log.db` phải tồn tại ở gốc dự án, vì `docker-compose.yml` gắn nó theo
kiểu bind-mount FILE. Nếu file chưa có, Docker sẽ tạo một **thư mục** trùng
tên và SQLite sẽ báo lỗi khó hiểu.

Tạo trước bằng cách chạy `python run.py once` một lần ở ngoài, hoặc:

```powershell
python -c "import sys; sys.path.insert(0,'src'); from database import database; database.init_db()"
```

Hai file trạng thái cũng là bind-mount FILE nên cũng phải tồn tại trước:

```powershell
if (!(Test-Path .report_state.json)) { '{}' | Out-File -Encoding utf8 .report_state.json }
if (!(Test-Path .alert_state.json))  { '{}' | Out-File -Encoding utf8 .alert_state.json }
```

`.alert_state.json` giữ mốc "đã gửi cảnh báo lỗi hệ thống". Mất nó mỗi lần
dựng lại container là chuyện đáng lo hơn nghe tưởng: sự cố hạ tầng thường làm
container crash-loop, và `restart: unless-stopped` dựng lại liên tục — mỗi lần
dựng lại lại gửi thêm một thư cảnh báo, đúng lúc hệ thống đang hỏng nhất.

## Các lệnh khác hay dùng

```bash
docker compose restart              # khởi động lại (sau khi sửa .env)
docker compose ps                   # xem còn chạy không
docker compose logs --tail 100      # xem 100 dòng log cuối
docker stats mooc-elis-job          # xem CPU/RAM đang dùng

# Chạy lệnh một lần trong container (không đụng job đang chạy nền):
docker compose run --rm job python run.py once
docker compose run --rm job python run.py status   # chỉ XEM hàng đợi, không xử lý
```

`status` an toàn để chạy lúc job đang chạy nền: nó chỉ gọi API ① rồi in ra,
không tải file, không gọi LLM, không nộp gì về eLIS.

## Những điểm đã xử lý sẵn

**CMD truyền `loop`.** `run.py` nhận chế độ qua tham số vị trí. Vẫn nên ghi rõ
`loop` trong CMD thay vì dựa vào giá trị mặc định: nếu mặc định đổi thành
`once`, container sẽ xử lý một mẻ rồi thoát, và `restart: unless-stopped` dựng
lại liên tục — thành vòng lặp KHỞI ĐỘNG CONTAINER chứ không phải vòng lặp poll.
Mỗi lần dựng lại còn phải nạp lại toàn bộ thư viện, tốn hơn nhiều `time.sleep`.

**Volume trỏ đúng `mooc_log.db` ở gốc dự án.** `database/database.py` đặt
`DB_PATH = Path(__file__).parent.parent / "mooc_log.db"`, tức GỐC dự án chứ
không phải trong thư mục `database/`. Gắn nhầm thư mục `database/` thì log vẫn
mất mỗi lần dựng lại container.

**Key không nằm trong image.** `.env` bị `.dockerignore` chặn, được gắn vào lúc
chạy. Nhúng vào image thì key nằm vĩnh viễn trong layer — kể cả khi có lệnh xóa
ở bước sau, ai kéo image về vẫn moi ra được.

**Gắn cả FILE `.env`, không chỉ `env_file`.** `pydantic-settings` đọc trực tiếp
file `.env` chứ không chỉ đọc biến môi trường, nên phải có cả hai.

**Ảnh chứng chỉ không lọt vào image.** `data/` nằm trong `.dockerignore` — đó là
dữ liệu cá nhân của nhân viên.

**Dùng `requirements-job.txt`, không phải `requirements.txt`.** Bản job bỏ
`gradio` (chỉ `web_demo.py` cần, kéo theo ~20 package), `pytest`, `rapidfuzz`
(không chỗ nào dùng), và `tenacity` (code hiện tự viết retry trong
`run.goi_co_retry`). Đo thực tế: thư viện giảm từ 461MB xuống 168MB.

**`libmagic1` cài qua apt.** Trên Windows bạn dùng `python-magic-bin`, nhưng
trong container Linux gói đó không cài được.

**Chạy bằng user thường**, không phải root.

**Log tự giới hạn** (5 file × 10MB), chạy dài ngày không đầy ổ.

## Lưu ý về IP allowlist

eLIS chặn theo IP. Container chạy trên máy bạn thì dùng chung IP với máy, nên
đã được allowlist sẵn.

Khi chuyển sang server công ty thì phải xin eLIS thêm IP của server đó, nếu
không sẽ dính HTTP 403.

## Khi có gì đó sai

```bash
docker compose logs --tail 50        # xem lỗi gần nhất
docker compose config                # kiểm tra compose file có hợp lệ không
docker compose run --rm job python run.py status   # chẩn đoán từ TRONG container
```

Sửa `requirements-job.txt` mà build vẫn dùng bản cũ:

```bash
docker compose build --no-cache
```
