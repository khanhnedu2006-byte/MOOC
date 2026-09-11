# MOOC — Hệ thống xác minh chứng chỉ khóa học bằng AI

Hệ thống đọc ảnh chứng chỉ mà nhân viên nộp lên eLIS, đối chiếu với dữ liệu
eLIS đã đăng ký (tên nhân viên, tên khóa học, thời gian), rồi tự nộp kết luận
**APPROVED / REJECTED** ngược về eLIS.

Mục tiêu: bỏ bước duyệt tay từng chứng chỉ, nhưng **không** duyệt bừa — mọi ca
hệ thống không tự tin đều để lại dấu vết đủ để người tra lại.

Ba loại ca **không** đi theo đường thường, và cả ba đều được chặn **trước khi
tốn một lượt LLM nào**:

| Loại | Nguyên nhân | Kết cục |
|---|---|---|
| Hỏng kỹ thuật | Azure/LLM/eLIS lỗi | Ở lại `WAITING`, thử lại mãi, quá ngưỡng thì gửi mail |
| Bỏ qua | Không xác minh được danh tính người học | Ở lại `WAITING`, **không** thử lại, chờ người duyệt |
| Nộp trùng | Khóa này của nhân viên này đã được duyệt | `REJECTED` ngay, không quét |

---

## Mục lục

1. [Kiến trúc và nguyên lý ba tầng](#1-kiến-trúc-và-nguyên-lý-ba-tầng)
2. [Cài đặt](#2-cài-đặt)
3. [Luồng 1 — Job sản xuất `run.py`](#3-luồng-1--job-sản-xuất-runpy)
4. [Luồng 2 — Luật so khớp](#4-luồng-2--luật-so-khớp)
5. [Luồng 3 — Ca không đi theo đường thường](#5-luồng-3--ca-không-đi-theo-đường-thường)
6. [Luồng 4 — Báo cáo qua email](#6-luồng-4--báo-cáo-qua-email)
7. [Luồng 5 — Đánh giá độ chính xác (evaluation)](#7-luồng-5--đánh-giá-độ-chính-xác-evaluation)
8. [Luồng 6 — Kiểm tra khả năng trích xuất của LLM2](#8-luồng-6--kiểm-tra-khả-năng-trích-xuất-của-llm2)
9. [Luồng 7 — Công cụ chạy tay](#9-luồng-7--công-cụ-chạy-tay)
10. [Luồng 8 — Chạy bằng Docker](#10-luồng-8--chạy-bằng-docker)
11. [Cấu hình đầy đủ (`.env`)](#11-cấu-hình-đầy-đủ-env)
12. [Cơ sở dữ liệu log](#12-cơ-sở-dữ-liệu-log)
13. [Cấu trúc thư mục](#13-cấu-trúc-thư-mục)
14. [Test](#14-test)
15. [Lưu ý bảo mật](#15-lưu-ý-bảo-mật)
16. [App desktop trên Windows (`main_app.py`)](#16-app-desktop-trên-windows-main_apppy)
17. [Chạy nền trên server Linux](#17-chạy-nền-trên-server-linux)

---

## 1. Kiến trúc và nguyên lý ba tầng

### 1.1. Sơ đồ tổng thể

```
        ┌──────────────────────── eLIS ────────────────────────┐
        │  ① getCert          → danh sách chứng chỉ WAITING     │
        │  ② download         → file chứng chỉ (base64)         │
        │  ③ ProcessStatus    → nộp APPROVED / REJECTED         │
        └───────────────────────────────────────────────────────┘
                     │                              ▲
                     ▼                              │
        ┌──────────────────────────────────────────────────────┐
        │                    run.py (điều phối)                 │
        │   sắp hàng đợi · lọc cooldown · lô hóa · ghi log       │
        └──────────────────────────────────────────────────────┘
                     │                              ▲
                     ▼                              │
        ┌──────────────────────────────────────────────────────┐
        │                 pipeline.py (một chứng chỉ)           │
        └──────────────────────────────────────────────────────┘
```

### 1.2. Ba lần so bên trong `pipeline.py`

```
Ảnh chứng chỉ
     │
     ▼
[Tầng 1] Gemma vision (LLM1) đọc thẳng ảnh
     │
     ├── Tên KHỚP và khóa học KHỚP ──► kiểm tra ngày ──► APPROVED  (dừng, không tốn Azure)
     │                                       └─ ngoài khoảng ──► REJECTED
     │
     └── Không khớp
              │
              ▼
        [Tầng 2] Azure Document Intelligence (OCR) → LLM2 đọc lại từ text
              │
              ▼
        [Tầng 3] So LLM1 với LLM2 (so CHẶT, bằng nhau tuyệt đối)
              │
              ├── GIỐNG nhau ──► REJECTED
              │      (hai máy độc lập đọc ra cùng một thứ, và thứ đó khác dữ
              │       liệu eLIS → tin rằng ảnh thật sự sai, không phải máy đọc sai)
              │
              └── KHÁC nhau ──► so LLM2 với dữ liệu eLIS
                       ├── khớp ──► APPROVED  ("LLM1 đọc sai, Azure đọc lại đúng")
                       └── không khớp ──► REJECTED
```

### 1.3. Các nhãn kết luận

| Nhãn | `stage` | Nghĩa | Nộp về eLIS |
|---|---|---|---|
| `APPROVED` | `llm1`, `llm2` | Chứng chỉ hợp lệ | Có |
| `REJECTED` | `llm1`, `llm2`, `llm1_vs_llm2` | Sai tên, sai tên khóa học, ngày không hợp lệ | Có |
| `REJECTED` | `duplicate` | Nhân viên nộp trùng khóa học | Có |
| `WAITING` | `TECHNICAL_STAGES` | Hệ thống chưa xử lý được (lỗi kỹ thuật) | Không|
| `WAITING` | `SKIP_STAGE` | Không xác minh được danh tính người học | Không |

Hai dòng Waiting khác nhau ở việc thử lại: ca kỹ thuật được thử lại mãi vì sự cố sẽ khỏi, ca bỏ qua thì không.

---

## 2. Cài đặt

```powershell
# 1. Môi trường
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 2. Cấu hình
copy .env.example .env
# Mở .env và điền: FPT_API_KEY, AZURE_ENDPOINT, AZURE_KEY, ELIS_API_KEY

# 3. Kiểm tra kết nối eLIS trước khi chạy job (chỉ đọc, không đổi gì)
python run.py status
```

**Windows:** `python-magic-bin` được cài tự động (`requirements.txt` có điều
kiện `sys_platform == "win32"`). Nếu vẫn báo `failed to find libmagic`, cài tay:
`pip install python-magic-bin`.

---

## 3. Luồng 1 — Job sản xuất `run.py`

Đây là luồng chính, chạy thật với eLIS.

```powershell
python run.py           # loop  — chạy liên tục (mặc định)
python run.py once      # xử lý một lượt rồi thoát
python run.py retry     # như once, nhưng BỎ QUA giãn cách thử lại
python run.py status    # CHỈ XEM hàng đợi — không xử lý, không tốn LLM
```

### 3.1. `loop` — chạy liên tục

Luật nghỉ: **còn việc thì làm tiếp ngay, hết việc mới nghỉ `POLL_INTERVAL_SECONDS`**.

"Còn việc" đo bằng **số eLIS thật sự nhận** (`accepted_count`), không phải số
đã quét (`scanned_count`). Lý do: khi API ③ hỏng, bản ghi vẫn ở `WAITING` nên
vòng `getCert` sau trả về đúng những item đó. Lấy `scanned_count` làm mốc thì
job quay vòng không nghỉ, tải lại và gọi LLM lại cùng một tập chứng chỉ cho tới
khi eLIS sống lại — vừa tốn tiền vừa không ai để ý vì nhìn log vẫn thấy "đang
chạy".

Dừng bằng `Ctrl+C` 

### 3.2. `status` — lệnh chẩn đoán miễn phí

Khi một chứng chỉ nằm im trên eLIS, màn hình eLIS chỉ hiện **tên khóa học**, còn log chỉ hiện **user_course_id**. Lệnh này nối hai thứ đó lại, hỏi thẳng API 1 nên là dữ liệu chứ không phải suy luận.

```
user_course_id                         Nhân viên       Khóa học                    Trạng thái
79879305-7f93-4350-94c9-eca68c32f05e   Bùi Đức Hòa     Learning Microsoft 365...   hỏng 3/5 — chờ thêm ~1.2 tiếng
```

### 3.3. Vòng đời một chứng chỉ trong một lượt

```
1 getCert?status=WAITING 
      │
      ▼
split_duplicates()  ── nhân viên này đã được duyệt khóa này chưa?
      └─ RỒI → REJECTED "Cán bộ nộp trùng khóa học", nộp 3 ngay.
      │
      ▼
filter_queue() — GIỮ NGUYÊN thứ tự eLIS trả về
      ├─ đã bị BỎ QUA ở vòng trước → loại khỏi hàng đợi, im lặng
      ├─ gặp ca còn giãn cách       → DỪNG tại đó, mọi ca sau CHỜ THEO
      └─ đã hỏng ≥ TECHNICAL_ALERT_AFTER → GỬI EMAIL cảnh báo,
                                            và VẪN ở nguyên vị trí cũ
      │
      ▼   xử lý TỪNG chứng chỉ một, không chia lô
kiểm nộp trùng LẦN HAI ── bắt ca trùng nhau ngay trong cùng vòng này
      └─ trùng → REJECTED, nộp 3, không tải file
      │
      ▼
② download-certificates (1 cặp id)
      ├─ lỗi        → ghi log WAITING (download_error), DỪNG CẢ VÒNG
      └─ thiếu file → ghi log WAITING (no_file),      DỪNG CẢ VÒNG
      │
      ▼
(tùy chọn) lưu vào cert_archive/  ← đặt TRƯỚC khi scan
      │
      ▼
file_utils.read_as_images()   ← kiểm MIME bằng nội dung thật, PDF render 200 DPI
      │
      ▼
pipeline.process()
      ├─ APPROVED              → nộp ③, GHI NHỚ vào chỉ mục lịch sử
      ├─ REJECTED              → nộp ③ (không ghi nhớ — chưa được duyệt)
      ├─ BỎ QUA (SKIP_STAGE)   → ghi log WAITING, KHÔNG nộp gì
      └─ hỏng kỹ thuật         → ghi log WAITING, KHÔNG nộp, DỪNG CẢ VÒNG
```

**Có HAI cửa kiểm nộp trùng.** Cửa đầu vòng lọc cả danh sách;
cửa thứ hai bắt trường hợp hai bản ghi trùng nhau cùng nằm trong một vòng — lúc
cửa đầu chạy thì chưa cái nào được duyệt nên cả hai đều lọt.

**Ca nộp trùng và ca bỏ qua KHÔNG chặn hàng đợi** — chúng là chuyện của riêng
một chứng chỉ, không phải sự cố cả lô. Chỉ ca hỏng kỹ thuật mới chặn.

### 3.4. Trường thông tin nộp về eLIS

`build_result_dto()` gửi đúng một trường bình luận:

| Trường | Nội dung | Người đọc |
|---|---|---|
| `comment` | Kết luận cho học viên. APPROVED → `"Hợp lệ"`. Sai nghiệp vụ → nêu đúng trường sai. Nộp trùng → `"Cán bộ nộp trùng khóa học"`. | Học viên |

**Ca hỏng kỹ thuật và ca bỏ qua không đi qua hàm này** — chúng thoát sớm ở
`handle_one_certificate()` và không gọi API ③ lần nào.

### 3.5. Mã dùng để đối chiếu ≠ mã gửi về eLIS

| Trường | Nguồn | Dùng để |
|---|---|---|
| `employee_id` | `getCert.employeeId` (vd `00332383`) | **Gửi ngược về eLIS** ở API ③ |
| `employee_code` | phần trước `@` của `employeeEmail` (vd `hoabd3`) | **Đối chiếu với tên in trên ảnh** |

Tách hai trường vì nhiều chứng chỉ in username thay cho tên thật. Nếu
`employeeEmail` thiếu `@` hoặc rỗng, hệ thống ghi cảnh báo rõ ràng — không có
cảnh báo đó thì chứng chỉ in username sẽ bị `REJECTED` với lý do "tên không
khớp", trông y hệt trường hợp nhân viên nộp nhầm.

---

## 4. Luồng 2 — Luật so khớp

Mọi chuỗi đều đi qua `process_data.normalize()` trước khi so:

```
NFC → unidecode (bỏ dấu) → chữ thường → mọi ký tự không phải chữ/số thành
khoảng trắng → gộp khoảng trắng liên tiếp
```

Nhờ đó `"Nguyễn Văn A"`, `"NGUYEN-VAN-A"` và `"nguyen_van_a"` cho ra cùng một chuỗi.

### 4.1. So TÊN người nhận

`match_name_or_code()` — khớp **tên nhân viên HOẶC mã nhân viên**, chỉ cần một
trong hai đúng.

- **Khớp tên** (`same_word_set`): tách thành **tập hợp TỪ** rồi so tuyệt đối,
  bỏ qua thứ tự. Xử lý được ca đảo surname/given name:
  `"A NGUYEN VAN"` = `"NGUYEN VAN A"` → cùng tập `{a, nguyen, van}` → khớp.
  So **chặt** từng từ, không chịu lỗi OCR trong từ (`"nguyen"` ≠ `"nguyeen"`).

- **Khớp mã** (`match_code`): quét xem mã NV có xuất hiện như một **cụm từ liên
  tiếp** trong tên trên ảnh không. Mã là chuỗi máy nên phải khớp chính xác —
  `hungnt97` khác `hungnt98` là hai người, không dùng fuzzy. Quét theo cụm để
  bắt ca chứng chỉ in `"hungnt97 hungnt97"` hoặc lẫn chữ khác, đồng thời tránh
  khớp một phần (mã `nv` không khớp nhầm `nvidia`).

> **Ca "tên + email trong cùng một trường."** LLM đôi khi trích ra
> `"PHAM TUNG ANH anhpt34@fpt.com"`. Sau chuẩn hóa, chuỗi này thành tập
> `{pham, tung, anh, anhpt34, fpt, com}`. eLIS gửi `"Phạm Tùng Anh"` → tập
> `{pham, tung, anh}`. Hai tập **không bằng nhau** → nhánh khớp tên trượt.
> Nhưng nhánh **khớp mã** quét thấy từ `anhpt34` trùng khớp tuyệt đối
> `employee_code` → **APPROVED**. Đây là lý do `match_name_or_code` có hai
> nhánh chứ không chỉ một.

> **Luật tên đệm ĐÃ CHỐT: tên rút gọn thì BỎ QUA, không nới luật khớp.**
> `match_name` giữ nguyên độ chặt — `"Anh Le"` vẫn **không khớp**
> `"Lê Hoàng Anh"`. Nhưng thay vì từ chối oan, ca đó rơi vào nhánh bỏ qua
> (mục [5.6](#56-bỏ-qua--không-xác-minh-được-danh-tính)) và ở lại `WAITING`
> cho người duyệt xử lý.

### 4.2. So TÊN KHÓA HỌC — song ngữ

Chứng chỉ thường in tên khóa **song ngữ**, và LLM được yêu cầu tách làm hai
trường: `certificate_name` (tiếng Việt) và `certificate_name_alt` (tiếng Anh).

Nhưng eLIS lưu tên khóa ở **cả ba dạng** khác nhau tùy khóa:

| Dạng eLIS lưu | Ví dụ |
|---|---|
| chỉ tiếng Việt | `Bộ Quy định chính sách cần biết FPT` |
| chỉ tiếng Anh | `FPT Key Regulations and Policies` |
| **cả hai nối lại** | `Bộ Quy định chính sách cần biết FPT - FPT Key Regulations and Policies (English version)` |

Khớp một đường là đủ:

1. nửa thứ nhất → bắt ca eLIS lưu một ngôn ngữ
2. nửa thứ hai → bắt ca eLIS lưu ngôn ngữ kia
3. **ghép hai nửa** → bắt ca eLIS lưu cả hai

Prompt cũng yêu cầu chỉ lấy **đúng tên khóa**, không lấy cả câu bao quanh:
với `"Python cơ bản" has successfully completed the course "Python fundamentals"`,
tên khóa là `Python cơ bản` / `Python fundamentals`, không phải cả câu.

**Hai chế độ so** (`COURSE_MATCH_MODE`):

| Chế độ | Luật | Ví dụ |
|---|---|---|
| `loose` (mặc định) | Tên eLIS chỉ cần là **tập con** của tên trên ảnh (ảnh được phép thừa) | eLIS `"khóa học code online"` khớp ảnh `"khóa học code online (code-bc-06)"` |
| `strict` | Phải trùng khớp hoàn toàn (cùng tập từ) | |

`loose` an toàn hơn substring: eLIS gửi `"Python nâng cao"` **không** khớp ảnh
`"Python"`, vì `nâng`/`cao` không có trong ảnh. Chỉ chấp nhận **ảnh thừa**,
không chấp nhận **eLIS thừa**.

### 4.3. So THỜI GIAN

Ngày hoàn thành trên chứng chỉ phải nằm trong `[VALID_FROM, VALID_TO]`. Kiểm
tra **riêng** khỏi tên/khóa học để ghi được lý do rõ ràng (`"Ngày không hợp lệ"`
thay vì `"Không khớp"` chung chung).

### 4.4. Lý do từ chối

`_mismatch_reason()` liệt kê **mọi** trường sai, không dừng ở trường đầu tiên:
Tên không khớp; Tên khóa học không khớp; Ngày không hợp lệ

---

## 5. Luồng 3 — Ca không đi theo đường thường

### 5.1. Thế nào là "hỏng kỹ thuật"

Là ca **hệ thống chưa xử lý được**, không phải nhân viên khai sai. Danh sách
`stage` được coi là hỏng kỹ thuật (`database.TECHNICAL_STAGES`):

| `stage` | Nguyên nhân |
|---|---|
| `llm1_error` | Gemma lỗi / hết hạn key / timeout/ hết tiền |
| `stage2_error` | Azure OCR hoặc LLM2 lỗi |
| `file_error` | File tải về không phải ảnh/PDF hợp lệ |
| `download_error` | API ② lỗi cả lô |
| `no_file` | eLIS không trả file cho chứng chỉ này |
| `soft_fail_zip` | Gói file trả về hỏng |
| `system_error` | Bỏ cuộc sau khi hết lượt thử |

Các ca này **không bị nộp `REJECTED`** — chúng ở lại `WAITING` trên eLIS để còn
được xử lý lại.

### 5.2. Luật HR: lỗi hệ thống KHÔNG BAO GIỜ thành REJECTED
Ca hỏng kỹ thuật
   │
   ├─ KHÔNG nộp gì về eLIS, KHÔNG chuyển chỗ, và DỪNG CẢ VÒNG.
   │     Các chứng chỉ phía sau chưa tới lượt.
   │
   ├─ Nghỉ TECHNICAL_RETRY_COOLDOWN_MINUTES (mặc định 2 phút) rồi thử lại
   │     CHÍNH NÓ, ở CHÍNH VỊ TRÍ đó. Trong thời gian nghỉ: KHÔNG tải,
   │     KHÔNG gọi LLM cho bất kỳ ca nào.
   │
   ├─ Hỏng tới lần thứ TECHNICAL_ALERT_AFTER (mặc định 5, ~10 phút)
   │     → GỬI EMAIL CẢNH BÁO cho ALERT_MAIL_TO
   │     → và VẪN TIẾP TỤC THỬ LẠI như thường
   │
   └─ Không có nhánh nào khác. Chứng chỉ ở lại WAITING trên eLIS cho tới khi
      sự cố khắc phục xong — lúc đó nó tự được xử lý, không cần thao tác tay.

### 5.2b. Chặn đầu hàng: chưa xong 1 thì chưa tới lượt 2
Hàng đợi: 1, 2, 3, ... 10     — chứng chỉ 1 lỗi hệ thống

Vòng 1   : thử 1 → hỏng.  DỪNG VÒNG, không đụng 2, 3, 4.
Trong 2' : cả hàng đợi nghỉ. Không tải, không gọi LLM cho ai.
Sau 2'   : thử 1 → hỏng lần 2. Lại dừng.
...
Lần 5    : hỏng lần 5 → GỬI EMAIL, và vẫn thử tiếp mãi.
Khi 1 xong: vòng đó chạy tiếp luôn 2, 3, 4...
Sau lần 5 mà vẫn hỏng thì gửi Email mới sau mỗi 1 tiếng

### Bảng phân loại lỗi LLM (`src/llm_error.py`)

`llm_vision` và `llm_text` gọi **cùng** một model qua **cùng** một endpoint
nên gặp y hệt các lỗi. Bảng nằm ở module riêng để hai file dùng chung một bản
— chép hai bản là cách chắc chắn để chúng lệch nhau, đúng chuyện đã xảy ra
với prompt của chính hai file đó.

| Nhận diện | Kết luận | Thử lại? |
|---|---|---|
| `402`, hoặc message chứa `insufficient_quota` / `insufficient balance` / `quota exceeded` / `billing` / `hết hạn mức` | **Hết tiền / hết hạn mức FPT** — phải nạp thêm | **Không** |
| `401` | Sai `FPT_API_KEY` (hoặc key bị thu hồi) | **Không** |
| `403` | Key không có quyền gọi model này | **Không** |
| `404` | Sai `FPT_MODEL` (chú ý chữ **B** hoa) hoặc `FPT_BASE_URL` | **Không** |
| message chứa `context_length_exceeded` / `maximum context length` | Chứng chỉ quá nhiều chữ, vượt giới hạn ngữ cảnh | **Không** |
| `429` **không** kèm dấu hiệu quota | Giới hạn tốc độ gọi model | **Có**, 3 lần |
| `408`, `500`, `502`, `503`, `504` | Lỗi phía FPT Cloud | **Có**, 3 lần |
| Không rút được mã | Rớt mạng / DNS / timeout socket | **Có**, 3 lần |

### Nhịp gửi

**Mail đầu ngay khi chạm ngưỡng, sau đó mỗi tiếng một mail nhắc lại**
chừng nào sự cố còn (`ALERT_COOLDOWN_HOURS`, mặc định 1).

| Tình huống | Có gửi không |
|---|---|
| Lần đầu chạm ngưỡng 5 lần hỏng | **Gửi ngay** — cooldown không làm chậm mail đầu |
| Vẫn sự cố đó, 2 phút sau | Không |
| Vẫn sự cố đó, 1 tiếng sau | **Gửi** — nhắc lại |
| Danh sách chứng chỉ đổi, cùng loại lỗi | Không |
| Xuất hiện `stage` **mới** | **Gửi ngay**, không đợi hết giãn cách |
| Lần gửi trước hỏng (SMTP lỗi) | Thử lại sau 15 phút, không phải ở vòng kế tiếp |

### 5.4. Thử lại ngay bằng tay

Khi biết Azure/eLIS đã khỏi và không muốn đợi hết giãn cách:

```powershell
python run.py retry
```

### 5.5. Ca hoãn không làm ngập log

Danh sách hoãn chỉ in ra khi **tập id thay đổi** — in mỗi vòng thì với chu kỳ
5 giây sẽ sinh hàng nghìn dòng giống hệt. Mỗi dòng in đủ: id, tên nhân viên,
**tên khóa học**, hỏng mấy lần, đã cảnh báo chưa, và còn bao lâu nữa.
---

### 5.6. Bỏ qua — không xác minh được danh tính

Ca **đọc được ảnh** nhưng không nối được tên đọc ra với nhân viên nào. Không
phải lỗi hệ thống, cũng không phải nhân viên khai sai — hệ thống đơn giản
không có căn cứ để kết luận. Từ chối là từ chối oan, nên đẩy sang người duyệt.

Toàn bộ luật nằm trong `pipeline._unverifiable_identity()`, chạy **bốn bước
theo đúng thứ tự**:

```
1. Tên hoặc mã NV khớp?                        -> KHÔNG bỏ qua, kết luận bình thường
2. Tên khóa học hoặc ngày cũng sai?             -> KHÔNG bỏ qua, để REJECTED xử
3. Ảnh in email NGOÀI fpt.com?                  -> BỎ QUA
4. Tên trên ảnh THIẾU họ hoặc tên đệm?          -> BỎ QUA
```
Hai luật nhận diện, đo trên 162 ca đánh giá thật:

| Luật | Hàm | Bắt được |
|---|---|---|
| Email ngoài công ty | `compare.external_email()` | 1/162 — `minhnt4487@gmail.com` (mã NV là `minhnt159`) |
| Thiếu họ / tên đệm | `compare.name_missing_words()` | 9/162 — `"Lê Tiến"` so với `"Lê Xuân Tiến"` |

**Ba điểm khác hẳn ca hỏng kỹ thuật:**

| | Hỏng kỹ thuật | Bỏ qua |
|---|---|---|
| Chặn hàng đợi | Có | **Không** |
| Tính vào ngưỡng gửi mail | Có | **Không** — `SKIP_STAGE` cố ý không nằm trong `TECHNICAL_STAGES` |
| Thử lại | 2 phút/lần, mãi mãi | **Không bao giờ** |

**Không gọi API ③.** Bản ghi eLIS giữ nguyên trạng thái sẵn có, hệ thống không
ghi gì vào trường `comment`. Thứ chặn nó quay lại vòng sau là **dòng log**
`SKIP_STAGE` trong `mooc_log.db`, qua `database.skipped_ids()` — hàm này lấy
dòng **mới nhất** theo `MAX(id)`, nên chứng chỉ nào về sau được xử lý bình
thường sẽ tự rơi khỏi danh sách bỏ qua.

### 5.7. Nộp trùng khóa học

Có **hai luồng** cùng đẩy chứng chỉ vào eLIS: hệ thống này (quét bằng AI), và
luồng đồng bộ tự động của FPT Elearning (đẩy thẳng, không xác minh). Cùng một
khóa của cùng một người vì thế có thể vào eLIS hai lần, thành **hai bản ghi
riêng với hai `user_course_id` khác nhau**.

**Khóa đối chiếu là EMAIL + TÊN KHÓA HỌC**, không phải `user_course_id`: hai
lần nộp là hai bản ghi riêng nên id luôn khác nhau, tra theo nó thì không bao
giờ khớp được cái gì. Kiểm chứng trên dữ liệu thật: không dòng nào thiếu email,
và không email nào ứng với hai mã nhân viên.

```
client.get_by_email(email)          GET getCert?employeeEmail=...
        │                           KHÔNG gửi kèm status
        ▼   run.completed_courses() lọc HAI lần trên dữ liệu trả về
   employeeEmail có khớp không?
   submitStatus == "APPROVED"?
        │
        ▼
   set(normalize(courseName))       đem so với courseName của ca WAITING
```
#### Hai phép lọc, và cả hai đều bắt buộc

**Lọc `submitStatus`**: Hỏi theo email thì eLIS trả về **mọi** bản ghi của người đó — kể cả chính chứng chỉ WAITING đang xử lý. Bỏ phép lọc này thì chứng chỉ nào cũng "trùng" với **chính nó**, và cả
hàng đợi bị từ chối tự động mà không có lỗi nào được ném ra.

#### Một cửa chặn, cộng bộ nhớ trong vòng

Kiểm ngay trước khi tải file, trong `handle_one_certificate()` — ca trùng
không tốn lượt LLM nào. Kèm theo là `_approved_this_round`: hai bản ghi trùng
nhau **cùng nằm trong một vòng** thì cái đầu vừa được nộp APPROVED nhưng eLIS
chưa chắc kịp phản ánh khi cái thứ hai hỏi. Bộ nhớ đó được **xóa ở đầu mỗi
vòng**, vì từ vòng sau eLIS đã có dữ liệu thật; không xóa thì một khóa vừa
duyệt bị coi là trùng mãi mãi và không ai truy ra vì sao.

---

## 6. Luồng 4 — Báo cáo qua email

### 6.1. Gửi tay

```powershell
python send_report.py                                      # XEM TRƯỚC 7 ngày gần nhất
python send_report.py --day 2026-08-17                     # đúng một ngày
python send_report.py --from 2026-08-01 --to 2026-08-31 --bucket week
python send_report.py --send                               # GỬI THẬT
```

**Mặc định là xem trước** — phải thêm `--send` mới gửi. Cố ý như vậy để chạy thử
không lỡ gửi mail. Bản xem trước ghi ra `bao_cao.html` (đổi bằng `--output`).

`--bucket` là mốc gom số liệu trong biểu đồ (`day` / `week` / `month`). Để trống
thì tự chọn theo độ dài kỳ. Đặt thô hơn kỳ báo cáo sẽ tự bị hạ xuống — gom 7
ngày theo tuần chỉ cho ra đúng một cột.

### 6.2. Gửi tự động

`scheduler.kiem_tra_va_gui()` được gọi ở cuối **mỗi vòng** của `run.py loop`.
Hàm này rẻ khi chưa tới hạn (chỉ đọc cấu hình + so giờ). Mốc đã gửi lưu ở
`.report_state.json` để không gửi trùng khi job khởi động lại.

| `REPORT_SCHEDULE` | Tần suất | Nội dung |
|---|---|---|
| `off` (mặc định) | không tự gửi | — |
| `daily` | mỗi ngày lúc `REPORT_TIME` | **7 ngày gần nhất** |
| `weekly` | mỗi tuần, thứ `REPORT_WEEKDAY` | **7 ngày gần nhất** |
| `monthly` | mỗi tháng, ngày `REPORT_MONTHDAY` | trọn **tháng trước** |

`daily` và `weekly` cùng nội dung, khác tần suất. Nội dung là 7 ngày chứ không
phải 1 ngày vì **biểu đồ một điểm thì không nói lên điều gì**.

### 6.3. Cấu hình SMTP

`SMTP_PASSWORD` luôn là **App Password**, không phải mật khẩu đăng nhập:

- **Gmail:** bật Xác minh 2 bước → `myaccount.google.com/apppasswords` → tạo App
  Password 16 ký tự, **dán liền, bỏ dấu cách**.
- **Office 365:** cần MFA + admin bật SMTP AUTH cho hộp thư.

Gmail **luôn ghi đè** trường `From` thành tài khoản đã đăng nhập — điền
`MAIL_FROM` khác cũng không có tác dụng.

Phần dựng nội dung và phần gửi tách rời nhau: đổi cách gửi (SMTP relay nội bộ,
Microsoft Graph…) chỉ cần viết thêm một hàm gửi, không đụng chỗ dựng nội dung.
Chỉ còn **một** bộ dựng báo cáo (`report_layout.py`) — báo cáo ngày là báo cáo
kỳ với `from = to`.

---


## 7. Luồng 5 — Chạy bằng Docker

```bash
docker compose up -d --build    # dựng image và chạy nền
docker compose logs -f          # xem log trực tiếp
docker compose down             # dừng hẳn
```

Chi tiết đầy đủ ở **[DOCKER.md](DOCKER.md)**. Vài điểm quan trọng:

- **`mooc_log.db` phải tồn tại trước khi build** — `docker-compose.yml` gắn nó
  theo kiểu bind-mount **file**. Chưa có thì Docker tạo một **thư mục** trùng
  tên và SQLite báo lỗi khó hiểu. Tạo trước bằng `python run.py once`.
- **CMD truyền `loop` tường minh**, không dựa vào giá trị mặc định. Nếu mặc định
  đổi thành `once`, container sẽ xử lý một mẻ rồi thoát và `restart: unless-stopped`
  dựng lại liên tục — thành vòng lặp *khởi động container* chứ không phải vòng
  lặp poll.
- **Key không nằm trong image.** `.env` bị `.dockerignore` chặn, được gắn vào
  lúc chạy. Nhúng vào image thì key nằm vĩnh viễn trong layer.
- **Container dùng `requirements-job.txt`**, bỏ `gradio`/`pytest`/`rapidfuzz`/
  `tenacity` — image giảm từ 461 MB xuống 168 MB.
- **`data/` nằm trong `.dockerignore`** — đó là dữ liệu cá nhân của nhân viên.

Chạy lệnh một lần trong container mà không đụng job đang chạy nền:

```bash
docker compose run --rm job python run.py status
```

---

## 8. Cấu hình đầy đủ (`.env`)

Cấu hình đọc bằng `pydantic-settings` từ file `.env` **và** biến môi trường.
Toàn bộ định nghĩa nằm ở `src/config.py`. Thứ tự ưu tiên đầy đủ:

```
tham số  >  biến môi trường  >  kho khóa Windows  >  .env  >  file secrets
```

Kho khóa chỉ giữ bốn khóa bí mật và chỉ có tác dụng trên Windows — xem mục 16.3.
Mọi tham số dưới đây (trừ bốn khóa đó) còn **sửa được ngay trong app desktop**,
có hiệu lực từ vòng sau mà không cần khởi động lại — xem mục 16.2.

### 8.1. FPT AI Marketplace (Gemma — LLM1 & LLM2)

| Biến | Mặc định | Giải thích |
|---|---|---|
| `FPT_API_KEY` | **bắt buộc** | Key FPT AI Marketplace. Thiếu → chương trình dừng ngay lúc khởi động |
| `FPT_BASE_URL` | `https://mkp-api.fptcloud.com` | Endpoint kiểu OpenAI-compatible |
| `FPT_MODEL` | `gemma-4-31B-it` | Model dùng cho **cả** LLM1 (đọc ảnh) và LLM2 (đọc text OCR) |
| `LLM_TEMPERATURE` | `0.2` | Thấp vì đây là việc **trích xuất**, không phải sáng tác. Tăng lên chỉ làm kết quả dao động giữa các lần chạy |
| `LLM_MAX_TOKENS` | `2048` | Đủ cho JSON trích xuất; chứng chỉ nhiều chữ vẫn nằm trong hạn |

### 8.2. Azure Document Intelligence (OCR — tầng 2)

| Biến | Mặc định | Giải thích |
|---|---|---|
| `AZURE_ENDPOINT` | **bắt buộc** | Endpoint tài nguyên Document Intelligence |
| `AZURE_KEY` | **bắt buộc** | Key tài nguyên |

> **Giới hạn gói Free (F0): 500 trang/tháng, 1 request/giây.** Khi chạm trần,
> tầng 2 hỏng và ca đó thành hỏng kỹ thuật. Cách xử lý đúng là **nâng gói**,
> không phải làm yếu tầng 2 đi — tầng 2 chính là lớp bảo vệ chống ca "LLM1 đọc
> sai nhưng nghe hợp lý".

### 8.3. eLIS

| Biến | Mặc định | Giải thích |
|---|---|---|
| `ELIS_BASE_URL` | `https://apitest.fpt.com/uat-elis-gw` | Gốc cho API ① `getCert` và ③ `ProcessUserCourseStatus` |
| `ELIS_FILE_BASE_URL` | `https://apitest.fpt.com/uat-elis-gw` | Gốc cho API ② `download-certificates`. Tách riêng vì eLIS có thể đặt dịch vụ file ở host khác |
| `ELIS_API_KEY` | `""` | Gửi trong header `apikey` |
| `ENV` | `UAT` | Nhãn ghi chú môi trường đang trỏ tới (`UAT` / `Production`) |

### 8.4. Luật nghiệp vụ

| Biến | Mặc định | Giải thích |
|---|---|---|
| `COURSE_MATCH_MODE` | `loose` | `loose` = tên eLIS chỉ cần là **tập con** của tên trên ảnh. `strict` = phải trùng khớp hoàn toàn. Xem [4.2](#42-so-tên-khóa-học--song-ngữ) |
| `VALID_FROM` | `2026-01-01` | Ngày sớm nhất chấp nhận. **Phải đổi khi sang năm mới** |
| `VALID_TO` | `2026-09-30` | Ngày muộn nhất chấp nhận |

### 8.5. Tham số vận hành

| Biến | Mặc định | Giải thích |
|---|---|---|
| `POLL_INTERVAL_SECONDS` | `5` | Chỉ áp dụng khi **hết việc**. Còn việc thì job làm liên tục, không nghỉ |
| `RETRY_COUNT` | `3` | Số lần gọi lại **một request eLIS** khi gặp lỗi tạm thời (502, timeout). Khác hoàn toàn với `TECHNICAL_ALERT_AFTER` |
| `RETRY_DELAY_SECONDS` | `5` | Nghỉ giữa các lần gọi lại đó |
| `TIMEOUT_SECONDS` | `60` | **Hiện không có tác dụng** — `src/client.py` viết cứng `timeout=30` cho API ①, `60` cho ② và ③. Sửa giá trị trong `.env` sẽ không đổi gì. Hoặc nối dây vào `client.py`, hoặc bỏ biến này đi |

> **`BATCH_SIZE` đã bị xóa.** Hệ thống xử lý từng chứng chỉ một, không chia lô.
> Để lại dòng đó trong `.env` cũng vô hại — không mã nào đọc nó nữa.

### 8.6. Kho lưu chứng chỉ

| Biến | Mặc định | Giải thích |
|---|---|---|
| `SAVE_CERTIFICATES` | `0` (**tắt**) | `1` = giữ lại ảnh + thông tin `getCert` mỗi lần tải về, để chạy lại bộ đánh giá mà không cần eLIS (**dữ liệu thật chỉ đi qua eLIS đúng một lần**). Mặc định **tắt** vì chứng chỉ thật chứa tên/mã/email nhân viên |
| `ARCHIVE_DIR` | `cert_archive` | Thư mục kho. Đã nằm trong `.gitignore` |

### 8.7. Thử lại ca hỏng kỹ thuật

| Biến | Mặc định | Giải thích |
|---|---|---|
| `TECHNICAL_RETRY_COOLDOWN_MINUTES` | `2` | Nghỉ bao lâu trước khi thử lại cùng một chứng chỉ. Đủ để eLIS/Azure chập vài giây tự qua, mà sự cố khỏi lúc nào thì chậm nhất 2 phút sau chứng chỉ được xử lý |
| `TECHNICAL_ALERT_AFTER` | `5` | Hỏng tới lần thứ mấy thì **gửi email cảnh báo**. Mỗi vòng thử đúng một lần, nên 5 lần = 5 vòng ≈ 10 phút. **Không phải mốc bỏ cuộc** — máy vẫn thử tiếp ca đó, và hàng đợi vẫn chặn tại nó |

### 8.7b. Email cảnh báo lỗi hệ thống

| Biến | Mặc định | Giải thích |
|---|---|---|
| `ALERT_MAIL_TO` | `...@fpt.com` | Người nhận cảnh báo, nhiều người cách nhau dấu phẩy. **Khác `MAIL_TO`** — xem [5.3](#53-email-cảnh-báo-srcalertpy) |
| `ALERT_COOLDOWN_HOURS` | `1` | Nhịp **nhắc lại** khi sự cố vẫn còn: mỗi tiếng một thư. **Không làm chậm thư đầu tiên.** Bắt buộc phải có, nếu không một sự cố kéo dài sẽ sinh hàng trăm thư giống hệt nhau |

### 8.7. Email

| Biến | Mặc định | Giải thích |
|---|---|---|
| `SMTP_HOST` | `smtp.office365.com` | Gmail dùng `smtp.gmail.com` |
| `SMTP_PORT` | `587` | STARTTLS |
| `SMTP_USER` | `""` | Cũng nhận tên `SMTP_USERNAME` — hai tên là **một** |
| `SMTP_PASSWORD` | `""` | **Luôn là App Password**, không phải mật khẩu đăng nhập |
| `MAIL_FROM` | `""` | Địa chỉ From. Rỗng = dùng `SMTP_USER`. Gmail **luôn ghi đè** trường này |
| `MAIL_TO` | `""` | Người nhận. Cũng nhận tên `MANAGER_EMAIL` hoặc `MAIL_DEN` |

### 8.8. Lịch báo cáo tự động

| Biến | Mặc định | Giải thích |
|---|---|---|
| `REPORT_SCHEDULE` | `off` | `off` \| `daily` \| `weekly` \| `monthly`. Xem [6.2](#62-gửi-tự-động) |
| `REPORT_TIME` | `18:00` | Giờ gửi (HH:MM, giờ VN) |
| `REPORT_WEEKDAY` | `4` | Chỉ dùng cho `weekly`. `0`=Thứ Hai … `4`=Thứ Sáu … `6`=Chủ nhật |
| `REPORT_MONTHDAY` | `1` | Chỉ dùng cho `monthly` (báo cáo cho **tháng trước**) |
| `REPORT_BUCKET` | `""` (tự chọn) | Mốc gom số liệu trong biểu đồ: `day` \| `week` \| `month`. Đặt thô hơn kỳ báo cáo sẽ **tự bị hạ xuống** — gom 7 ngày theo tuần chỉ ra đúng một cột |

---

## 9. Cơ sở dữ liệu log

SQLite một file: **`mooc_log.db`** ở gốc dự án. Không cần server, không thêm
thư viện. Bảng `process_log`, mỗi chứng chỉ đã xử lý là một dòng.

| Cột | Nội dung |
|---|---|
| `id` | Khóa tự tăng. **Không chỉ để đánh số** — `skipped_ids()` và `technical_failure_detail()` dùng `MAX(id)` để lấy dòng mới nhất của mỗi chứng chỉ. Không dùng `MAX(created_at)` vì cột đó chỉ chính xác tới giây, hai dòng trong cùng một giây sẽ hòa nhau |
| `created_at` | Thời điểm xử lý |
| `user_course_id` | Id bản ghi eLIS — nối với màn hình eLIS |
| `employee_id` | Mã NV do eLIS cấp (vd `00332383`) — thứ **gửi ngược về eLIS** |
| `employee_code` | Username từ email (vd `hoabd3`) — thứ dùng **đối chiếu với ảnh** |
| `name_on_image` | Tên AI đọc được từ ảnh |
| `certificate_name` | Tên khóa AI **đọc được từ ảnh** |
| `course_name` | Tên khóa **eLIS đăng ký** (`getCert.courseName`) — khác cột trên |
| `date_on_image` | Ngày AI đọc được |
| `verdict` | `APPROVED` / `REJECTED` / `WAITING` |
| `reason` | Lý do (đã sạch, không chứa tên tầng) |
| `stage` | Xem bảng dưới |
| `provider` | Nhà cung cấp chứng chỉ (Udemy, Coursera…) |
| `elis_sent_ok` | `1`=eLIS nhận, `0`=eLIS từ chối, `NULL`=chưa gửi |
| `elis_message` | Thông điệp eLIS trả về khi từ chối |

Các giá trị `stage` có thể gặp:

| `stage` | Nghĩa | `verdict` đi kèm |
|---|---|---|
| `llm1` | Gemma đọc ảnh và kết luận ngay | `APPROVED` / `REJECTED` |
| `llm2` | Tầng 2 (Azure + LLM2) kết luận | `APPROVED` / `REJECTED` |
| `llm1_vs_llm2` | Hai model đồng thuận, và khác input | `REJECTED` |
| `duplicate` | Nhân viên đã được duyệt khóa này | `REJECTED` |
| `skipped_external_email` | Không xác minh được danh tính (mục 5.6) | `WAITING` |
| `llm1_error`, `stage2_error`, `file_error`, `download_error`, `no_file`, `soft_fail_zip`, `system_error` | Hỏng kỹ thuật (`TECHNICAL_STAGES`) | `WAITING` |
---

## 10. Cấu trúc thư mục

```
MOOC/
├── run.py                    # ★ Job sản xuất: loop / once / retry / status
├── run_local.py              # Chạy thử một ảnh bằng tay
├── web_demo.py               # Giao diện Gradio
├── send_report.py            # Dựng + gửi báo cáo email
├── scheduler.py              # Kiểm tra tới giờ gửi báo cáo chưa
├── report_layout.py          # Bộ dựng HTML báo cáo (DUY NHẤT)
├── main_app.py               # ★ App desktop Windows: khay + cửa sổ + bộ đếm
│
├── src/
│   ├── config.py             # ★ Toàn bộ cấu hình (.env) + get_llm()
│   ├── client.py             # Gọi 3 API eLIS
│   ├── pipeline.py           # ★ Ba lần so cho MỘT chứng chỉ
│   ├── compare.py            # ★ Luật so khớp tên / mã / khóa học + nhận diện ca bỏ qua
│   ├── process_data.py       # normalize(), code_from_email(), date_in_range()
│   ├── llm_vision.py         # Prompt + gọi LLM1 (đọc ảnh)
│   ├── llm_text.py           # Prompt + gọi LLM2 (đọc text OCR) — giữ ĐỒNG BỘ với llm_vision
│   ├── llm_error.py          # ★ Phân loại lỗi LLM + retry (hai file trên dùng chung)
│   ├── ocr_azure.py          # Azure Document Intelligence
│   ├── file_utils.py         # Kiểm MIME thật + render PDF → ảnh
│   ├── schemas.py            # Verdict, InputInfo, ExtractedInfo, ProcessResult
│   ├── archive.py            # Lưu chứng chỉ vào kho
│   ├── alert.py              # ★ Email cảnh báo lỗi hệ thống (gộp + chặn trùng)
│   ├── charts.py             # Biểu đồ cho báo cáo
│   ├── app_runner.py         # ★ Luồng chạy job của app desktop (KHÔNG có Tk)
│   ├── settings_file.py      # ★ Sửa cấu hình lúc chạy + ghi ngược vào .env
│   └── vault.py              # ★ Kho khóa Windows (Credential Manager)
│
├── database/
│   ├── database.py           # SQLite: ghi log, đếm hỏng kỹ thuật, tra ca đã bỏ qua
│   └── report.py             # Truy vấn số liệu cho báo cáo
│
├── evaluation/
│   ├── match_images.py       # Ghép Excel ↔ thư mục ảnh
│   ├── run_eval.py           # ★ check / template / from-archive / run / export-*
│   ├── run_llm2.py           # Chỉ chạy OCR+LLM2, xuất 4 cột
│   ├── dataset.py            # Đọc/ghi file nhãn (ghi nguyên tử)
│   ├── extraction_score.py   # Chấm điểm ĐỌC
│   ├── decision_score.py     # Chấm điểm KẾT LUẬN
│   ├── export_errors.py      # Excel ca lệch cho HR
│   └── export_compare.py     # Bảng HUMAN vs AI (CSV 6 cột)
│
├── tools/
│   ├── autostart.ps1         # Cài/gỡ tác vụ tự chạy khi đăng nhập Windows
│   ├── mooc.service          # ★ Dịch vụ systemd — chạy nền trên server Linux
│   └── preflight.sh          # Kiểm server trước khi bật dịch vụ
│
├── tests/                    # pytest
├── data/                     # Dữ liệu thật (gitignored)
├── cert_archive/             # Kho chứng chỉ (gitignored)
├── mooc_log.db               # Log SQLite (gitignored)
├── config_changes.log        # Sổ ai đổi cấu hình gì, lúc nào (gitignored)
├── Dockerfile · docker-compose.yml · DOCKER.md
├── .env                      # Key thật (gitignored)
├── .env.example              # Mẫu — ĐƯỢC git theo dõi, xem mục 15
├── pyproject.toml            # Cấu hình ruff
├── requirements.txt          # Đầy đủ (lập trình ở máy)
└── requirements-job.txt      # Gọn (container chạy job)
```

**`llm_vision.py` và `llm_text.py` phải giữ đồng bộ.** Hai file dùng chung một
bộ luật trích xuất (lấy tên ở khoảng giữa trang chứ không phải chỗ chữ ký, tách
tên khóa theo ngôn ngữ, giữ nguyên ký tự phi Latin, bỏ qua đồng hồ trên
taskbar…). Sửa một bên mà quên bên kia thì tầng 3 sẽ so hai kết quả sinh ra từ
hai bộ luật khác nhau. `tests/test_prompt.py` canh việc này.

---

## 11. Test

```powershell
pytest              # toàn bộ — 443 test
ruff check .        # lint
```

Test **không** gọi API thật — mọi hàm gọi API được truyền vào pipeline dưới dạng
tham số (dependency injection), nên test thay bằng hàm giả.
---

## 12. App desktop trên Windows (`main_app.py`)

Bản Docker ở mục 10 dành cho server. Mục này dành cho cách chạy thứ hai: một
app chạy trên máy người vận hành, mở lên là job chạy, giống UniKey.

```powershell
pip install keyring pystray     # chỉ cần một lần
python main_app.py
```

App **không** chứa luật nghiệp vụ nào. Mọi phán quyết vẫn do `run.py` đưa ra;
`main_app.py` gọi `run.process_one_round()` trong một luồng nền và gắn thêm một
`logging.Handler` để nhật ký chảy vào cửa sổ. `run.py` không sửa một dòng nào —
chạy `python run.py loop` vẫn cho ra đúng kết quả và đúng nhật ký như trước.

Nhưng app **không chỉ** gọi mỗi `process_one_round`. Nó còn tự làm bốn việc, và
biết rõ bốn việc đó thì mới sửa được đúng chỗ khi có sự cố:

Cả bốn nằm trong `JobRunner._one_round` — ở `src/app_runner.py`, không phải
`main_app.py`. Tách ra vì `main_app.py` `import tkinter` mà máy CI Linux thường
không cài `python3-tk`; để chung thì cả bộ test không import nổi, và đúng phần
dễ sai nhất này sẽ không có test nào canh (`tests/test_app_runner.py`).

| App tự làm gì | Vì sao |
|---|---|
| Gọi `client.get_pending_list` lấy hàng đợi rồi truyền `items` vào `process_one_round` | `RoundResult` không nói hàng đợi dài bao nhiêu, mà đó là con số đầu tiên người ta muốn nhìn |
| Ghi sổ `alert.api_failed` / `api_succeeded` cho API ① | Hệ quả của việc trên: tự gọi API thì phải tự ghi sổ, y như `run.py` làm. Lệch chỗ này là email cảnh báo sai |
| Hỏi `database.skipped_ids()` và `count_by_verdict()` | Hai ô số "Bỏ qua" và "Từ chối" không nằm trong `RoundResult` |
| Gọi `scheduler.check_and_send()` sau mỗi vòng | Đúng như `run_forever` làm. Thiếu nó là báo cáo định kỳ im lặng không gửi |

### 12.1 Ba tab

| Tab | Có gì |
|---|---|
| **Bảng điều khiển** | 5 ô số (hàng đợi · eLIS đã nhận · từ chối · bỏ qua · đang hoãn), nhật ký cuộn theo dòng mới, nút Tạm dừng / Chạy vòng ngay / Thử lại ca đang hoãn / Mở thư mục log |
| **Ca bỏ qua** | Danh sách chứng chỉ để nguyên WAITING vì không xác minh được danh tính, đọc từ `mooc_log.db` |
| **Cấu hình** | Bốn ô nhập khóa bí mật (ghi vào kho khóa Windows) + **31 ô sửa được** cho mọi cấu hình còn lại |

### 12.2 Sửa cấu hình ngay trong app

Mọi tham số trong `.env` — trừ bốn khóa bí mật.

### 12.3 Khóa bí mật để trong kho khóa Windows

Bốn giá trị `FPT_API_KEY`, `AZURE_KEY`, `ELIS_API_KEY`, `SMTP_PASSWORD` cất
được vào Credential Manager thay vì để chữ thường trong `.env`. Windows mã hóa
bằng DPAPI, khóa gắn với tài khoản đang đăng nhập. Code ở `src/vault.py`.

**Thứ tự ưu tiên khi đọc cấu hình** (`Settings.settings_customise_sources`):

```
tham số  >  biến môi trường  >  KHO KHÓA  >  .env  >  file secrets
```

---
## 13. Chạy nền trên server Linux

Mục 12 là app chạy trên máy người vận hành. Mục này là cách chạy thật: job
nằm trên server công ty, chạy 24/7, tự bật lại khi chết và khi server khởi
động lại. HR không vào server — HR vào **eLIS** xem kết quả; việc của server
chỉ là đẩy phán quyết lên đó đều đặn.

### 13.0 Quyết định trước: CHỈ MỘT chỗ được chạy

Job đọc hàng đợi WAITING rồi nộp kết quả. Hai chỗ cùng chạy nghĩa là mỗi
chứng chỉ tốn **hai lượt Gemma + Azure**, và cả hai cùng gọi API ③ cho một
bản ghi.

Khóa socket trong `main_app.py` chỉ chặn hai bản *app desktop* trên cùng một
máy. Nó **không** biết gì về container Docker, và cũng không biết gì về job
đang chạy trên server. Trước khi bật trên server, tắt hết những chỗ khác:

```bash
docker compose down                      # nếu đang chạy Docker ở máy nào đó
# và đóng hẳn app desktop (Thoát từ khay, không phải bấm X)
```

### 13.1 Cách nhanh nhất: Docker trên server

Repo đã có sẵn `Dockerfile` và `docker-compose.yml`, và `restart:
unless-stopped` lo luôn phần tự bật lại khi container chết hoặc khi server
khởi động lại. Không phải cài Python, không phải cài `libmagic1`, không phải
đụng tới systemd.

```bash
# --- Trên MÁY BẠN: .env không nằm trong git nên phải chép riêng ---
scp .env khanhnn72@<server>:~/MOOC/.env

# --- Trên SERVER ---
cd ~/MOOC
chmod 600 .env

# BA FILE NÀY PHẢI TỒN TẠI TRƯỚC KHI `up`. Xem 17.2 để biết vì sao.
touch mooc_log.db
echo '{}' > .report_state.json
echo '{}' > .alert_state.json

# Kiểm IP allowlist TRƯỚC (chỉ ĐỌC, không xử lý, không tốn tiền LLM)
docker compose run --rm job python run.py status

# Chạy
docker compose up -d --build
docker compose logs -f
```

Log phải ra `Chạy liên tục. Hết việc thì hỏi lại mỗi 5 giây.` rồi tới
`Có N chứng chỉ chờ duyệt` hoặc `Không còn chứng chỉ chờ duyệt`. Container
`Up` mà log không có hai dòng đó thì **chưa xong** — healthcheck chỉ làm
`import config` nên nó báo khỏe cả khi job đang chết.

### 13.2 Vận hành hằng ngày

```bash
docker compose ps                       # còn chạy không
docker compose logs --tail 100          # log gần nhất
docker compose logs --since 24h | grep -E "APPROVED|REJECTED"
docker compose restart                  # sau khi sửa .env
docker compose up -d --build            # sau khi git pull
docker compose down                     # dừng hẳn
```

Xem hàng đợi mà không đụng job đang chạy nền:

```bash
docker compose run --rm job python run.py status
```

Sửa `.env` xong phải `restart`: cấu hình chỉ đọc một lần lúc tiến trình chạy
lên. (Sửa nóng không cần restart là tính năng của app desktop — mục 16.2 —
trên server không có.)

--|
| **eLI### 17.5 HR nhìn thấy gì

HR **không** cần vào server. Có hai đường:

| Đường | Cách bật |
|---|-S** — mỗi chứng chỉ đổi từ WAITING sang APPROVED/REJECTED kèm lý do | Tự động, không phải làm gì |
| **Email báo cáo định kỳ** — số liệu tổng hợp gửi thẳng hộp thư | Đặt `REPORT_SCHEDULE=daily` và `MAIL_TO=<email HR>` trong `.env`, rồi `docker compose restart` |

Ca **bỏ qua** (không xác minh được danh tính) ở lại WAITING và cần người
duyệt xử lý tay trên eLIS — hiện chưa nằm trong báo cáo định kỳ, xem phụ lục
việc còn treo. Nếu HR chỉ nhìn báo cáo, họ sẽ không biết những ca đó đang chờ
mình.

---
