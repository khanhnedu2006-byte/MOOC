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

**Vì sao phải có tầng 2.** Model thị giác đọc sai vẫn sinh ra chữ trôi chảy.
Một tên khóa học dài và mạch lạc **không** phải bằng chứng model đọc đúng — độ
mạch lạc là thứ nó luôn tạo ra được. Chính ca "đọc sai nhưng nghe rất hợp lý"
mới là ca tầng 2 sinh ra để bắt.

**Vì sao tầng 3 so CHẶT (`==` sau chuẩn hóa), không nới lỏng.** Ở đây hai bên
đều là MÁY đọc cùng một tấm ảnh — không có yếu tố gõ tay. Hai máy đọc ra y hệt
nhau mới là bằng chứng đồng thuận đáng tin. Nới lỏng ở bước này làm hỏng chính
mục đích của nó.

**Vì sao tầng 1 dừng sớm khi khớp.** Azure là dịch vụ tính phí theo trang. Ca
đã khớp ở tầng 1 không cần đọc lại — tầng 2 chỉ chạy khi tầng 1 đã thất bại.
Hệ quả cần nhớ khi đọc số liệu: **tỉ lệ đúng của tầng 2 KHÔNG so sánh được với
tầng 1**, vì tầng 2 chỉ nhận những ca khó mà tầng 1 đã trượt (selection bias).

### 1.3. Các nhãn kết luận

| Nhãn | `stage` | Nghĩa | Nộp về eLIS |
|---|---|---|---|
| `APPROVED` | `llm1`, `llm2` | Chứng chỉ hợp lệ | Có |
| `REJECTED` | `llm1`, `llm2`, `llm1_vs_llm2` | Nội dung không khớp dữ liệu eLIS | Có |
| `REJECTED` | `duplicate` | Khóa này đã được duyệt trước đó | Có |
| `WAITING` | `TECHNICAL_STAGES` | **Hệ thống chưa xử lý được** (lỗi kỹ thuật) | **Không** |
| `WAITING` | `SKIP_STAGE` | **Không xác minh được danh tính** người học | **Không** |

Hai dòng `WAITING` cuối chỉ tồn tại trong log và trên màn hình — bản ghi eLIS
không bị chạm vào, nó giữ nguyên trạng thái chờ duyệt sẵn có. Nhãn này có mặt
vì nếu ghi những ca đó là `REJECTED`, người vận hành sẽ đọc log rồi đi báo học
viên "chứng chỉ bị từ chối", trong khi hệ thống chưa hề đánh giá được nội dung.

Hai dòng đó giống nhau ở chỗ không nộp gì, nhưng **khác nhau ở việc thử lại**:
ca kỹ thuật được thử lại mãi vì sự cố sẽ khỏi, ca bỏ qua thì không bao giờ —
cái sai nằm cứng trên ảnh, thử nghìn lần vẫn thế.

> **Luật HR: lỗi hệ thống KHÔNG BAO GIỜ thành `REJECTED`.** Ca hỏng kỹ thuật
> ở lại `WAITING` và được thử lại mãi, không giới hạn số lần; hỏng tới ngưỡng
> thì gửi email cảnh báo cho người vận hành. Chi tiết ở [mục 5](#5-luồng-3--ca-không-đi-theo-đường-thường).

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

**Yêu cầu bắt buộc trong `.env`:** `FPT_API_KEY`, `AZURE_ENDPOINT`, `AZURE_KEY`.
Thiếu một trong ba, chương trình dừng ngay khi khởi động (pydantic-settings
kiểm tra lúc nạp cấu hình) chứ không chạy được nửa chừng rồi mới hỏng.

**Windows:** `python-magic-bin` được cài tự động (`requirements.txt` có điều
kiện `sys_platform == "win32"`). Nếu vẫn báo `failed to find libmagic`, cài tay:
`pip install python-magic-bin`.

**Lỗi `OMP: Error #15`** (chỉ gặp trên Windows + conda): mọi điểm vào đã đặt
sẵn `KMP_DUPLICATE_LIB_OK=TRUE` **trước** mọi import nặng. Nếu tự viết script
mới, phải đặt dòng đó ở đầu file — đặt sau import là vô tác dụng vì DLL đã nạp
xong. `tests/test_diem_vao.py` canh việc này.

**IP allowlist:** eLIS chặn theo IP. Máy đang chạy phải nằm trong danh sách,
nếu không sẽ dính HTTP 403. Chuyển sang server công ty thì phải xin thêm IP.

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
chạy". Đo bằng `accepted_count` thì job tự hạ nhịp, và quay lại chạy hết tốc độ
ngay khi eLIS nhận được cái đầu tiên.

Dừng bằng `Ctrl+C` — đó là cách dừng bình thường, không đổ traceback.

### 3.2. `status` — lệnh chẩn đoán miễn phí

Khi một chứng chỉ nằm im trên eLIS, câu hỏi đầu tiên luôn là "cái nào?". Màn
hình eLIS chỉ hiện **tên khóa học**, còn log chỉ hiện **user_course_id**. Lệnh
này nối hai thứ đó lại, hỏi thẳng API ① nên là dữ liệu chứ không phải suy luận.

```
user_course_id                         Nhân viên       Khóa học                    Trạng thái
79879305-7f93-4350-94c9-eca68c32f05e   Bùi Đức Hòa     Learning Microsoft 365...   hỏng 3/5 — chờ thêm ~1.2 tiếng
```

Chỉ **đọc**: không tải file, không gọi LLM, không nộp gì. An toàn khi job đang
chạy nền.

### 3.3. Vòng đời một chứng chỉ trong một lượt

```
① getCert?status=WAITING (tối đa 100 item)
      │
      ▼
split_duplicates()  ── nhân viên này đã được duyệt khóa này chưa?
      └─ RỒI → REJECTED "Cán bộ nộp trùng khóa học", nộp ③ ngay.
               KHÔNG tải file, KHÔNG gọi LLM, KHÔNG chặn ca sau.
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
      └─ trùng → REJECTED, nộp ③, không tải file
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

**Có HAI cửa kiểm nộp trùng, không phải một.** Cửa đầu vòng lọc cả danh sách;
cửa thứ hai bắt trường hợp hai bản ghi trùng nhau cùng nằm trong một vòng — lúc
cửa đầu chạy thì chưa cái nào được duyệt nên cả hai đều lọt. Xem
[5.7](#57-nộp-trùng-khóa-học).

**Chỉ `APPROVED` mới được ghi vào chỉ mục lịch sử.** `REJECTED` nghĩa là chứng
chỉ chưa được công nhận, nên nhân viên nộp lại khóa đó là chuyện bình thường,
không phải nộp trùng.

**Không còn cơ chế chia lô.** `BATCH_SIZE` đã bị bỏ: mỗi vòng lấy từng chứng
chỉ ra xử lý rồi nộp ngay. Nộp ngay vì kết quả chưa nộp thì bản ghi vẫn
`WAITING`, vòng poll sau tải lại và gọi LLM lại — tốn thêm một lượt cho mỗi
cái. Gom cả mẻ rồi mới nộp còn khiến toàn bộ công đã làm phụ thuộc vào một
request duy nhất ở cuối; chỉ cần nó hỏng (rớt mạng, container restart) là mất
sạch.

**Hàng đợi chạy đúng thứ tự và chặn đầu hàng.** Xem
[5.2b](#52b-chặn-đầu-hàng-chưa-xong-1-thì-chưa-tới-lượt-2).

**Ca nộp trùng và ca bỏ qua KHÔNG chặn hàng đợi** — chúng là chuyện của riêng
một chứng chỉ, không phải sự cố cả lô. Chỉ ca hỏng kỹ thuật mới chặn.

**Vì sao lưu archive TRƯỚC khi scan:** nếu pipeline chết giữa chừng thì ảnh vẫn
còn — mà ca làm pipeline chết mới là ca đáng nghiên cứu nhất.

### 3.4. Trường thông tin nộp về eLIS

`build_result_dto()` gửi đúng một trường bình luận:

| Trường | Nội dung | Người đọc |
|---|---|---|
| `comment` | Kết luận cho học viên. APPROVED → `"Hợp lệ"`. Sai nghiệp vụ → nêu đúng trường sai. Nộp trùng → `"Cán bộ nộp trùng khóa học"`. | Học viên |

Tài liệu API ③ có thêm trường tùy chọn `comment_cer`, nhưng hệ thống **không
gửi**: không có bằng chứng nào cho thấy giao diện eLIS hiển thị nó, và gửi một
trường không ai đọc chỉ làm payload nặng thêm mà không giúp được ai.

Tên tầng xử lý (`llm1`, `llm2`…) **không** xuất hiện ở đây — chúng chỉ có nghĩa
với người bảo trì và đã nằm trong bảng log.

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
>
> Đây là lựa chọn có chủ đích chứ không phải chưa làm xong. Nới `match_name`
> thành so tập con giảm số ca từ chối oan từ 19 xuống 9, nhưng đổi lại
> `"Nguyễn Tuấn"` sẽ khớp với nhiều nhân viên khác nhau — tức mua 10 ca đúng
> bằng một lỗ hổng danh tính. Bỏ qua thì không mất ca nào mà cũng không mở
> lỗ hổng nào; cái giá là người duyệt phải xem 9 ca đó bằng mắt.

### 4.2. So TÊN KHÓA HỌC — song ngữ

Chứng chỉ thường in tên khóa **song ngữ**, và LLM được yêu cầu tách làm hai
trường: `certificate_name` (tiếng Việt) và `certificate_name_alt` (tiếng Anh).

Nhưng eLIS lưu tên khóa ở **cả ba dạng** khác nhau tùy khóa:

| Dạng eLIS lưu | Ví dụ |
|---|---|
| chỉ tiếng Việt | `Bộ Quy định chính sách cần biết FPT` |
| chỉ tiếng Anh | `FPT Key Regulations and Policies` |
| **cả hai nối lại** | `Bộ Quy định chính sách cần biết FPT - FPT Key Regulations and Policies (English version)` |

Nên `match_course_bilingual()` thử **đủ ba đường**, khớp một đường là đủ:

1. nửa thứ nhất → bắt ca eLIS lưu một ngôn ngữ
2. nửa thứ hai → bắt ca eLIS lưu ngôn ngữ kia
3. **ghép hai nửa** → bắt ca eLIS lưu cả hai

Thiếu bước 3 là lỗi đã xảy ra thật: chứng chỉ `TIENLX6` in đúng nguyên chuỗi
song ngữ mà eLIS lưu, model tách làm hai theo đúng yêu cầu, rồi không nửa nào
bằng chuỗi eLIS nữa → **từ chối oan một chứng chỉ hợp lệ, đọc đúng**.

Ba đường chỉ **nới thêm**, không bỏ đường nào — một ca đang `APPROVED` không thể
vì thay đổi này mà thành `REJECTED`.

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

```
Tên không khớp; Tên khóa học không khớp; Ngày không hợp lệ
```

Không kèm giá trị đọc được và không kèm tên tầng. Giá trị AI đọc được nằm ở
các cột `name_on_image` / `certificate_name` / `date_on_image` trong DB, tên
tầng nằm ở cột `stage` — cả hai đều **không** được gửi về eLIS.

---

## 5. Luồng 3 — Ca không đi theo đường thường

### 5.1. Thế nào là "hỏng kỹ thuật"

Là ca **hệ thống chưa xử lý được**, không phải nhân viên khai sai. Danh sách
`stage` được coi là hỏng kỹ thuật (`database.TECHNICAL_STAGES`):

| `stage` | Nguyên nhân |
|---|---|
| `llm1_error` | Gemma lỗi / hết hạn key / timeout |
| `stage2_error` | Azure OCR hoặc LLM2 lỗi |
| `file_error` | File tải về không phải ảnh/PDF hợp lệ |
| `download_error` | API ② lỗi cả lô |
| `no_file` | eLIS không trả file cho chứng chỉ này |
| `soft_fail_zip` | Gói file trả về hỏng |
| `system_error` | Bỏ cuộc sau khi hết lượt thử |

Các ca này **không bị nộp `REJECTED`** — chúng ở lại `WAITING` trên eLIS để còn
được xử lý lại. Nộp `REJECTED` ở đây là đóng vĩnh viễn một chứng chỉ mà hệ
thống chưa hề đánh giá được nội dung.

### 5.2. Luật HR: lỗi hệ thống KHÔNG BAO GIỜ thành REJECTED

> **Đây là luật do HR chốt, không phải lựa chọn kỹ thuật.** Lý do của HR: lỗi
> hệ thống thì **cả dãy cùng lỗi**. Nộp `REJECTED` trong tình huống đó là từ
> chối oan hàng loạt chứng chỉ hợp lệ chỉ vì hạ tầng chập mười phút.
>
> Nhánh "bỏ cuộc sau N lần → nộp REJECTED" **đã bị gỡ bỏ hoàn toàn**.

```
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
```

**Vì sao phải có email.** Nhánh bỏ cuộc cũ không phải vô cớ mà có: nó chặn
tình trạng chứng chỉ nằm `WAITING` vĩnh viễn mà **không ai biết**. Bỏ nó đi mà
không thay bằng gì thì lỗi hệ thống trở nên hoàn toàn im lặng — eLIS hiện
"đang chờ duyệt" mãi mãi, job cứ thử lại mỗi hai phút, không có gì báo cho
người vận hành. Email giữ lại phần "có người biết", bỏ phần "máy tự quyết sai".

**Vì sao giãn cách đổi từ 6 tiếng xuống 2 phút.** Mốc 6 tiếng hợp lý khi còn
nhánh bỏ cuộc, vì khi đó mỗi lượt thử là một bước tiến tới quyết định
`REJECTED` nên phải tiến thật chậm. Giờ không còn quyết định nào để tiến tới;
mục tiêu duy nhất là **bắt lại sớm nhất khi hạ tầng khỏe lại**, nên giãn cách
phải ngắn.

**Số lần đếm được lưu trong `mooc_log.db`**, nên nó sống qua các lần khởi động
lại container.

### 5.2b. Chặn đầu hàng: chưa xong 1 thì chưa tới lượt 2

Hàng đợi chạy **đúng thứ tự eLIS trả về** và **chặn tại ca đang hỏng**. Chứng
chỉ 1 hỏng thì cứ đợi rồi thử lại chính nó cho tới khi xong; 2, 3, 4 chưa tới
lượt.

```
Hàng đợi: 1, 2, 3, ... 10     — chứng chỉ 1 lỗi hệ thống

Vòng 1   : thử 1 → hỏng.  DỪNG VÒNG, không đụng 2, 3, 4.
Trong 2' : cả hàng đợi nghỉ. Không tải, không gọi LLM cho ai.
Sau 2'   : thử 1 → hỏng lần 2. Lại dừng.
...
Lần 5    : hỏng lần 5 → GỬI EMAIL, và vẫn thử tiếp mãi.
Khi 1 xong: vòng đó chạy tiếp luôn 2, 3, 4...
```

**Vì sao chặn thay vì chạy tiếp.** Đúng lập luận của HR: hỏng kỹ thuật là hỏng
**cả lô**. Chạy tiếp 2, 3, 4 ngay khi 1 vừa hỏng chỉ khiến chúng hỏng theo và
**đội số lần hỏng của chính chúng lên** vì một sự cố chúng chưa từng gây ra —
rồi cả bốn cái cùng chạm ngưỡng cảnh báo vì đúng một sự cố duy nhất.

Ca phía sau **không bị quét rồi vứt kết quả** — vòng dừng **trước** khi quét,
nên không tốn lượt LLM nào và không ghi thêm dòng hỏng nào cho chúng.

Đã bỏ **ba** cơ chế trước đó:

| Cơ chế cũ | Vì sao bỏ |
|---|---|
| Ca đã từng hỏng xếp xuống **cuối** hàng đợi | Xuống cuối thì gặp lại đúng sự cố đó. Không cứu được gì, mà làm mất thứ tự eLIS nên log khó đối chiếu với màn hình eLIS |
| Thử lại các ca hỏng thêm một lần ở **cuối vòng** | Lượt thứ hai diễn ra vài giây sau lượt đầu nên gặp lại đúng sự cố — tốn thêm một lượt LLM cho mỗi chứng chỉ. Giãn cách 2 phút của vòng sau làm việc đó tốt hơn, vì nó **thật sự** cho sự cố thời gian tự khỏi |
| Giữ nguyên chỗ nhưng **vẫn chạy tiếp** 2, 3, 4 | Vẫn đội số lần hỏng của 2, 3, 4 lên vì một sự cố không liên quan đến chúng |

**Bỏ cơ chế thứ hai còn sửa một chỗ sai lệch trong cấu hình.** Khi mỗi vòng thử
hai lần, `TECHNICAL_ALERT_AFTER=5` thật ra chỉ là **3 vòng** — không ai đọc
cấu hình mà đoán ra được điều đó. Giờ "hỏng 5 lần" đúng bằng **5 vòng ≈ 10
phút**, khớp với thứ cấu hình nói.

> ### ⚠️ Cái giá của chặn đầu hàng
> Luật này an toàn khi ca đứng đầu hỏng vì **hạ tầng** — lúc đó cả lô hỏng nên
> chặn không mất gì. Nhưng nếu nó hỏng vì **lý do của riêng nó**, cụ thể là
> `stage = file_error` (file chứng chỉ thật sự lỗi), thì thử lại bao nhiêu lần
> cũng vẫn lỗi và **nó chặn cả hàng đợi vô thời hạn**. Một nhân viên nộp nhầm
> file hỏng có thể làm cả phòng không được duyệt chứng chỉ.
>
> **Email cảnh báo ở lần thứ 5 là thứ duy nhất cứu được tình huống đó.** Nên
> `SMTP_USER` / `SMTP_PASSWORD` phải được điền và phải hoạt động — không có
> nó, cảnh báo chỉ ghi vào log và hàng đợi đứng im mà không ai biết.
>
> Nếu muốn `file_error` **không** chặn hàng (vì nó là lỗi của riêng một chứng
> chỉ, không phải lỗi hệ thống), đó là một thay đổi nhỏ — hỏi khi cần.

**Không còn `BATCH_SIZE`.** Cơ chế chia lô đã bị bỏ hẳn — mỗi vòng lấy từng
chứng chỉ ra tải, quét, nộp rồi mới sang cái tiếp theo. Luật chặn đầu hàng
trước đây chạy đúng nhất với `BATCH_SIZE=1`; giá trị lớn hơn vẫn dừng đúng chỗ
nhưng lô đã tải là chi phí bỏ ra cho những ca chưa tới lượt. Bỏ chia lô làm
điều đó thành không thể xảy ra, thay vì chỉ khuyến cáo đừng làm.

### 5.3. Email cảnh báo (`src/alert.py`)

Gửi tới `ALERT_MAIL_TO` (mặc định **hoabd5@fpt.com**) — **khác** `MAIL_TO` của
báo cáo định kỳ. Cảnh báo là việc phải xử lý ngay; báo cáo là số liệu đọc cuối
ngày. Trộn hai luồng vào một hộp thư thì cảnh báo bị chìm giữa báo cáo.

Nội dung thư:

- Nói rõ ngay đầu thư rằng những chứng chỉ này **không bị từ chối**, hệ thống
  vẫn đang thử lại, và sự cố khỏi thì chúng tự được xử lý. Thiếu câu này thì
  người nhận đọc "lỗi hệ thống, 40 chứng chỉ" rồi đi báo học viên rằng chứng
  chỉ bị từ chối — đúng thứ luật HR sinh ra để tránh.
- **Nguyên nhân theo `stage`, dịch ra tiếng người**: `stage2_error` không nói
  cho người vận hành biết đi sửa ở đâu, nên thư viết thẳng "hết hạn mức Azure
  (gói F0 chỉ 500 trang/tháng), sai key, hoặc dịch vụ lỗi".
- **Bảng liệt kê từng chứng chỉ**: nhân viên, khóa học, tầng lỗi, số lần hỏng,
  chi tiết lỗi.

**Gộp một thư cho cả loạt, không gửi từng cái.** Một lần Azure hết hạn mức làm
cả hàng đợi cùng vượt ngưỡng trong một vòng. Gửi từng chứng chỉ một là 50 thư
giống hệt nhau trong vài phút — người nhận sẽ lọc bỏ tất, và cảnh báo mất tác
dụng đúng lúc cần nhất. Một thư liệt kê đủ mọi ca vừa đúng lập luận của HR
(lỗi hệ thống là **một** sự cố, không phải 50) vừa đọc được.

### Khối "Trạng thái 3 API của eLIS"

Thư **luôn in đủ ba API**, kể cả khi chỉ một cái hỏng — "không nhắc tới" và
"vẫn tốt" là hai chuyện khác nhau, và ở giữa một sự cố thì suy đoán nhầm chỗ
đó rất tốn thời gian.

| API | Việc nó làm | Hệ quả khi chết |
|---|---|---|
| ① `getCert` | Lấy danh sách chờ duyệt | **Hệ thống đứng im** — không lấy được hàng đợi nên không xử lý được cái nào |
| ② `download-certificates` | Tải file chứng chỉ | Chứng chỉ ở lại WAITING, tự khỏi khi eLIS sống lại. **Nhẹ nhất** |
| ③ `ProcessUserCourseStatus` | Nộp kết quả duyệt | **Đang đốt tiền** — đã quét xong (đã trả phí Gemma + Azure) nhưng kết quả không nộp được, vòng sau quét lại từ đầu |

Cột "hệ quả" là phần quan trọng nhất: biết "API ① lỗi" vẫn chưa biết có phải
bỏ việc đang làm để xử lý ngay hay không. Ba API hỏng cho ra **ba mức khẩn cấp
khác hẳn nhau**.

Với mỗi API đang hỏng, thư ghi: **thất bại mấy lần liên tiếp**, **hỏng từ lúc
nào**, **lý do gốc** (nguyên văn thông báo lỗi, để dán vào ticket cho IT), và
**hệ quả**.

**Trước đây hai trong ba API không bao giờ báo được.** Cơ chế cảnh báo đếm số
dòng log có `stage` kỹ thuật, mà chỉ API ② mới ghi ra loại dòng đó:

| API | Trước | Nay |
|---|---|---|
| ① | Lỗi bay lên `run_forever`, không ghi DB → **im lặng**, còn in "Không còn chứng chỉ chờ duyệt" (sai sự thật) | Bắt tại chỗ, đếm, cảnh báo. Log nói đúng: "KHÔNG gọi được eLIS" |
| ② | Có báo | Vẫn báo, thêm tên API và lý do gốc |
| ③ | Chỉ đánh dấu `elis_sent_ok=0` — không phải stage kỹ thuật → **im lặng** trong khi đang quét lại vòng tròn | Đếm và cảnh báo |

**Một thư, không phải hai.** API ② chết thì nó **vừa** là lỗi API **vừa** làm
chứng chỉ hỏng — gửi hai thư riêng là nói hai lần về đúng một sự cố. Gộp lại
còn cho ra thứ hai thư riêng không có: **nguyên nhân nằm cạnh hậu quả**, trong
cùng một màn hình.

Khi API ① chết, thư ghi *"Bị ảnh hưởng: TOÀN BỘ hàng đợi (không lấy được danh
sách nên không đếm được)"* — không phải `0`. In số 0 ở đó khiến người nhận
tưởng sự cố vô hại và để tới mai mới xem: đúng ca nặng nhất lại bị hạ mức
khẩn cấp.

### Khối "Sự cố này KHÔNG tự khỏi"

Thư mặc định viết *"sự cố khắc phục xong thì chứng chỉ tự được xử lý, không
cần thao tác gì thêm"*. Câu đó đúng với Azure quá tải hay eLIS chập, nhưng
**sai** với hết tiền / sai key / file hỏng — sẽ không có ai khắc phục gì nếu
không được nói là phải đi làm gì.

Với những ca đó, thư đổi giọng thành một khối đỏ:

```
*** SỰ CỐ NÀY KHÔNG TỰ KHỎI — CẦN NGƯỜI XỬ LÝ ***
  - HẾT TIỀN hoặc hết hạn mức FPT AI Marketplace. Thử lại sẽ KHÔNG tự khỏi
    — phải nạp thêm hạn mức cho tài khoản thì hệ thống mới chạy lại được.

Hệ thống vẫn thử lại đều nhưng sẽ hỏng y như vậy cho tới khi việc trên
được làm xong.
```

Câu này **bỏ trùng**: Azure hết quota làm 40 chứng chỉ cùng hỏng vì đúng một
lý do, in 40 dòng giống hệt nhau thì không ai đọc hết.

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

**Cái bẫy của mã 429.** Các endpoint kiểu OpenAI dùng `429` cho **hai chuyện
trái ngược**: rate-limit (đợi vài giây là khỏi) và `insufficient_quota` (đợi
mãi cũng không khỏi). Phân loại `429` chỉ theo mã số là sai một nửa số ca —
phải đọc cả nội dung message. Phần lớn test trong `test_llm_error.py` canh
đúng chỗ này.

**Mã lỗi rút từ hai nguồn.** `.status_code` của exception SDK, và nếu không có
thì regex `Error code: (\d{3})` trong chuỗi. Phụ thuộc vào `.status_code` một
mình là phụ thuộc vào chi tiết nội bộ của thư viện, thứ đã đổi vài lần giữa
các phiên bản.

**Thêm retry cho LLM.** Trước đây `ocr_azure` thử lại 3 lần cho lỗi tạm thời
còn `llm_vision`/`llm_text` **không thử lại lần nào** — một cú `429` thoáng
qua ở tầng LLM làm chứng chỉ kẹt 2 phút, trong khi đúng cú đó ở tầng Azure tự
khỏi sau 2 giây. Giờ hai tầng dùng cùng một luật: 3 lần, giãn 2s rồi 5s, và
**không** thử lại lỗi vĩnh viễn.

Azure cũng được gắn cùng nhãn cho `401`/`403` — hết quota F0 thì phải **nâng
gói**, thử lại không tự khỏi cho tới đầu tháng sau.

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

**Vì sao phải chặn.** Job thử lại mỗi 2 phút và **mỗi vòng đều tính lại** ai đã
vượt ngưỡng — ca hỏng 5 lần thì vòng sau hỏng 6 lần, vẫn vượt. Không chặn thì
một sự cố kéo dài 6 tiếng sinh ra **180 thư giống hệt nhau**; người nhận tạo
rule lọc bỏ ngay trong ngày đầu, và từ đó cảnh báo mất tác dụng vĩnh viễn, kể
cả cho những sự cố sau.

**Vì sao vẫn phải nhắc lại.** Chặn vĩnh viễn thì một sự cố kéo dài cả tuần chỉ
được báo đúng một lần rồi im — mà thư đó người nhận có thể đã bỏ lỡ. Nhịp một
tiếng đủ thưa để không ai lọc bỏ, đủ dày để sự cố bị bỏ quên nổi lên lại trong
ca trực tiếp theo.

"Loại sự cố" nhận diện bằng **tập `stage` đang hỏng cộng mã những API đang
chết**, không phải bằng danh sách chứng chỉ. Thiếu vế thứ hai thì lúc API ①
chết (không có chứng chỉ nào để hỏng) khóa sẽ là chuỗi rỗng, và cơ chế chống
trùng nuốt luôn thư báo API hỏng. Danh sách đổi mỗi vòng (ca cũ xong, ca mới vào), nên lấy nó
làm mốc thì thư nào cũng là "sự cố mới" và cơ chế chặn thành vô dụng trong khi
vẫn trông như đang hoạt động.

Mốc đã gửi ghi xuống `.alert_state.json` ở gốc dự án (đã gitignore, đã gắn
volume trong `docker-compose.yml`). Giữ trong biến thì container restart là mất
mốc — mà sự cố hạ tầng thường làm container crash-loop, nên mỗi lần dựng lại sẽ
gửi thêm một thư.

`alert.send_alert()` **không bao giờ ném lỗi ra ngoài**: nó được gọi từ giữa
vòng xử lý chứng chỉ, một lỗi SMTP làm chết vòng đó nghĩa là sự cố mạng nhỏ
biến thành job ngừng chạy.

### 5.4. Thử lại ngay bằng tay

Khi biết Azure/eLIS đã khỏi và không muốn đợi hết giãn cách:

```powershell
python run.py retry
```

### 5.5. Ca hoãn không làm ngập log

Danh sách hoãn chỉ in ra khi **tập id thay đổi** — in mỗi vòng thì với chu kỳ
5 giây sẽ sinh hàng nghìn dòng giống hệt. Mỗi dòng in đủ: id, tên nhân viên,
**tên khóa học**, hỏng mấy lần, đã cảnh báo chưa, và còn bao lâu nữa.

Log in `hỏng 7 lần` chứ **không** in dạng phân số `7/5`: mẫu số gợi ý rằng tới
đó là dừng, mà giờ không còn mốc dừng nào. Người vận hành đọc "2/3" sẽ đi báo
học viên rằng chứng chỉ sắp bị từ chối — đúng thứ luật HR mới cấm.

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

**Bước 1 phải đứng trước bước 3.** Chứng chỉ in `doannv19@fpt.com` cũng là
email, nhưng `normalize()` cắt `@` và `.` thành khoảng trắng nên chuỗi thành
`"doannv19 fpt com"`, và `match_code` tìm thấy mã nhân viên nằm trong đó. Trong
162 ca đánh giá có 7 ca ảnh in email; 6 ca là email công ty và **đang chạy
đúng**. Hỏi email trước là nuốt luôn cả 6.

**Bước 2 tồn tại vì phép so tên khóa học không cần biết người đó là ai.** Chứng
chỉ ghi khóa khác thì không thỏa mãn đăng ký, bất kể chủ nhân là ai — kết luận
đó đứng vững trên bằng chứng của chính nó. Bỏ qua ở đây còn tệ hơn cho học
viên: bị từ chối thì họ đọc được `"Tên khóa học không khớp"` và biết đường nộp
lại, còn bị bỏ qua thì không nhận được gì. Đo trên bộ đánh giá: **6/10 ca**
vướng danh tính còn sai cả khóa học hoặc ngày.

Hai luật nhận diện, đo trên 162 ca đánh giá thật:

| Luật | Hàm | Bắt được |
|---|---|---|
| Email ngoài công ty | `compare.external_email()` | 1/162 — `minhnt4487@gmail.com` (mã NV là `minhnt159`) |
| Thiếu họ / tên đệm | `compare.name_missing_words()` | 9/162 — `"Lê Tiến"` so với `"Lê Xuân Tiến"` |

Luật thứ hai dùng **tập con thực sự** (`<`), không phải `<=`: hai tập bằng nhau
thì `match_name` đã bắt từ trước. Nó cũng **không** bắt chiều ngược lại (ảnh
thừa từ) — ảnh thừa từ có thể là chức danh, cũng có thể là tên người khác in
kèm, hai thứ đó không quy về một luật được.

**Ba điểm khác hẳn ca hỏng kỹ thuật:**

| | Hỏng kỹ thuật | Bỏ qua |
|---|---|---|
| Chặn hàng đợi | Có | **Không** |
| Tính vào ngưỡng gửi mail | Có | **Không** — `SKIP_STAGE` cố ý không nằm trong `TECHNICAL_STAGES` |
| Thử lại | 2 phút/lần, mãi mãi | **Không bao giờ** |

Điểm thứ hai quan trọng hơn vẻ ngoài: chu kỳ poll là 5 giây còn ngưỡng cảnh
báo là 5 lần, nên nếu xếp nhầm `SKIP_STAGE` vào `TECHNICAL_STAGES` thì **25
giây** sau người vận hành nhận mail báo động về một chứng chỉ mà hệ thống chẳng
làm gì sai.

**Không gọi API ③.** Bản ghi eLIS giữ nguyên trạng thái sẵn có, hệ thống không
ghi gì vào trường `comment`. Thứ chặn nó quay lại vòng sau là **dòng log**
`SKIP_STAGE` trong `mooc_log.db`, qua `database.skipped_ids()` — hàm này lấy
dòng **mới nhất** theo `MAX(id)`, nên chứng chỉ nào về sau được xử lý bình
thường sẽ tự rơi khỏi danh sách bỏ qua.

Đường ra duy nhất là người duyệt vào eLIS bấm duyệt hoặc từ chối; thao tác đó
đưa bản ghi rời `WAITING` và getCert thôi trả về nó.

> **Giới hạn đã biết, và CỐ Ý để nguyên:** ảnh in
> `"NGUYEN THUY LINH minhnt4487@gmail.com"` vẫn bị bỏ qua dù tên đúng nằm ngay
> đó, vì `match_name` so tập hợp từ tuyệt đối nên ba từ thừa làm phép so trượt.
> Chữa được — nới `match_name` thành so tập con — nhưng đó chính là thứ đã bị
> loại ở [4.1](#41-so-tên-người-nhận): nới ra thì `"Nguyễn Tuấn"` khớp với
> nhiều người. Hướng sai hiện tại an toàn (về tay người duyệt, không bị từ
> chối oan), nên giữ nguyên và ghi lại ở `tests/test_skip.py`.

### 5.7. Nộp trùng khóa học

> **LUẬT NGHIỆP VỤ (HR chốt): một khóa học chỉ được học MỘT LẦN.**
> Đã được duyệt khóa nào thì nộp lại khóa đó là trùng, dù cách nhau một ngày
> hay ba năm. **Không có cửa sổ thời gian** — luật kiểu "chỉ tính nếu duyệt
> trong vòng N tháng" trông như phòng xa nhưng chính là lỗ hổng.
> `tests/test_duplicate.py::test_duyet_TU_LAU_van_tinh_la_trung` chặn việc
> thêm vào: ai viết cửa sổ thời gian sẽ làm test đỏ và phải quay lại hỏi HR.

Có **hai luồng** cùng đẩy chứng chỉ vào eLIS: hệ thống này (quét bằng AI), và
luồng đồng bộ tự động của FPT Elearning (đẩy thẳng, không xác minh). Cùng một
khóa của cùng một người vì thế có thể vào eLIS hai lần, thành **hai bản ghi
riêng với hai `user_course_id` khác nhau**.

Đo trên 208.426 dòng dữ liệu thật: **80.217** bản ghi khóa nội bộ FPT được
duyệt mà không có tên người duyệt nào — đó là luồng tự động. **1.163** cặp
(nhân viên, khóa học) đã được duyệt từ hai lần trở lên; theo luật trên thì cả
1.163 đều là lọt lưới, không cái nào là ngoại lệ hợp lệ.

**Khóa đối chiếu là EMAIL + TÊN KHÓA HỌC**, không phải `user_course_id`: hai
lần nộp là hai bản ghi riêng nên id luôn khác nhau, tra theo nó thì không bao
giờ khớp được cái gì. Kiểm chứng trên dữ liệu thật: không dòng nào thiếu email,
và không email nào ứng với hai mã nhân viên.

Tên khóa học đưa qua `normalize()` trước khi so — dữ liệu thật có 6.268 cách
viết tên, sau chuẩn hóa còn 6.132. So thô là bỏ sót 129 nhóm chỉ lệch dấu cách
thừa hoặc hoa/thường.

#### Hỏi thẳng eLIS theo email

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

API ① nhận tham số `employeeEmail`, nên mỗi chứng chỉ chỉ tốn **một** request
và dữ liệu **luôn tươi** — không có chỉ mục để cũ đi, không có nhịp nạp lại để
chỉnh. Đo trên production: 38 nhân viên trong 9 giây, ~0,24 giây mỗi lần hỏi.

Ba tên tham số khác đã thử và đều **bị API bỏ qua**: `employeeId`,
`employee_id`, `employeeCode` — cả ba trả về 200 kèm nguyên 3.134 bản ghi chứ
không báo lỗi gì.

#### Hai phép lọc, và cả hai đều bắt buộc

**Lọc `submitStatus`** là chỗ nguy hiểm nhất của cả luật. Hỏi theo email thì
eLIS trả về **mọi** bản ghi của người đó — kể cả chính chứng chỉ WAITING đang
xử lý. Bỏ phép lọc này thì chứng chỉ nào cũng "trùng" với **chính nó**, và cả
hàng đợi bị từ chối tự động mà không có lỗi nào được ném ra.

Cạm bẫy đi kèm: hai trường nằm sát nhau trong cùng bản ghi.

```json
"status": "REGISTED",        ← trạng thái ĐĂNG KÝ HỌC, luôn là REGISTED
"submitStatus": "APPROVED",  ← trạng thái DUYỆT CHỨNG CHỈ, cái cần đọc
```

Đọc nhầm `status` thì phép lọc mất tác dụng hoàn toàn — không bản ghi nào có
`status == "APPROVED"` nên `completed_courses()` luôn rỗng và luật im lặng
ngừng hoạt động.

**Tự kiểm lại email** vì API bỏ qua tham số lạ trong im lặng. Nếu một ngày nào
đó `employeeEmail` cũng bị bỏ qua — đổi phiên bản API, đổi gateway, gõ sai tên
— thì cái trả về là lịch sử của **mọi người**, và mọi chứng chỉ sẽ bị từ chối
vì "trùng" với khóa của người lạ. Phát hiện bản ghi lạc thì ghi log lỗi rõ ràng
và trả về rỗng, tức **bỏ qua luật** chứ không kết luận bừa.

#### Một cửa chặn, cộng bộ nhớ trong vòng

Kiểm ngay trước khi tải file, trong `handle_one_certificate()` — ca trùng
không tốn lượt LLM nào. Kèm theo là `_approved_this_round`: hai bản ghi trùng
nhau **cùng nằm trong một vòng** thì cái đầu vừa được nộp APPROVED nhưng eLIS
chưa chắc kịp phản ánh khi cái thứ hai hỏi. Bộ nhớ đó được **xóa ở đầu mỗi
vòng**, vì từ vòng sau eLIS đã có dữ liệu thật; không xóa thì một khóa vừa
duyệt bị coi là trùng mãi mãi và không ai truy ra vì sao.

#### Hỏng thì MỞ, không đóng

Không tra được lịch sử (eLIS lỗi, API không lọc đúng, thiếu email hoặc tên
khóa) thì chứng chỉ **đi tiếp theo luồng thường**. Chiều ngược lại mới nguy:
coi lỗi mạng là "chưa từng duyệt" rồi từ chối hàng loạt thì một sự cố hạ tầng
biến thành hàng trăm từ chối oan.

> **Đã đo được gì, và chưa đo được gì.** Quét toàn bộ hàng đợi production
> (59 chứng chỉ, 38 nhân viên): **0 ca bị gắn nhãn trùng** — luật không bắt
> oan. Nhưng vì hàng đợi không có ca trùng nào, điều đó **chưa chứng minh luật
> bắt được**: một luật luôn trả `False` cũng cho ra đúng kết quả ấy. Muốn đo
> tỷ lệ bắt trúng thì chạy luật ngược lên các bản ghi `REJECTED` mà người
> duyệt đã ghi lý do có chữ "trùng" — khoảng 1.900 ca trong dữ liệu tháng 8.

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

> ⚠️ Báo cáo chứa **PII nhân viên** (tên, mã, khóa học). Chỉ gửi cho mentor,
> **không** gửi cho khách hàng eLIS. Gửi qua Gmail cá nhân cần được mentor duyệt
> trước khi dùng thật.

---

## 7. Luồng 5 — Đánh giá độ chính xác (evaluation)

Đây là luồng **offline**, không đụng eLIS. Dùng để đo hệ thống đúng bao nhiêu %
trước khi tin nó.

### 7.1. Bước 1 — Ghép Excel với thư mục ảnh

Bộ dữ liệu thật đến dưới dạng một file Excel (mỗi dòng một chứng chỉ, có email
nhân viên + tên khóa học) và một thư mục ảnh đặt tên `<mã NV>_<tên khóa học>.<đuôi>`.
**Hai bên không cùng thứ tự.**

```powershell
# Xem trước, chưa ghi gì
python -m evaluation.match_images --excel data\information.xlsx --images data\image --dry-run

# Ghi ra file nhãn
python -m evaluation.match_images --excel data\information.xlsx --images data\image

# Xuất thêm báo cáo ghép chi tiết
python -m evaluation.match_images --excel data\information.xlsx --images data\image --report evaluation\match_report.csv
```

| Tham số | Ý nghĩa |
|---|---|
| `--excel` | **Bắt buộc.** File `.xlsx` chứa email + tên khóa học |
| `--images` | **Bắt buộc.** Thư mục ảnh chứng chỉ |
| `--out` | File nhãn ghi ra (mặc định `evaluation/eval_set.csv`) |
| `--dry-run` | Chỉ in kết quả ghép, không ghi file |
| `--report` | Ghi thêm CSV chi tiết từng dòng ghép |
| `--col-email` / `--col-course` / `--col-name` | Chỉ định tên cột nếu tự đoán sai |

**Cách ghép:** dựng khóa từ **cả hai phía** rồi so tập hợp từ đã chuẩn hóa.

```
dòng Excel : normalize("hoabd3" + " " + "Learning Microsoft 365 Copilot")
tên file   : normalize("hoabd3_Learning_Microsoft_365_Copilot")
```

Ba cách đặt tên file dưới đây cho ra **cùng một khóa**:

```
hoabd3_Learning Microsoft 365 Copilot for Work.jpg
hoabd3_Learning_Microsoft_365_Copilot_for_Work.png
HOABD3-Learning-Microsoft-365-Copilot-for-Work.pdf
```

**Không tách tên file theo dấu `_` đầu tiên** — tên khóa học có thể chứa gạch
dưới, và một số username cũng có. Tách sai một lần là ghép sai cả bộ. Dựng khóa
từ toàn bộ chuỗi thì không cần biết ranh giới nằm ở đâu.

**Không tự động ghép gần đúng.** Ca không khớp tuyệt đối chỉ được **gợi ý** kèm
số đo độ giống, người phải tự xác nhận, và gợi ý chỉ trỏ **trong cùng một mã
nhân viên**. Lý do: ghép lệch một dòng thì mọi con số đánh giá sau đó vẫn ra
đẹp và vẫn sai — model đọc đúng ảnh nhưng bị chấm bằng nhãn của ảnh khác, cho
ra "sai tên 30%" trong khi thực tế nó đọc đúng. Người đọc sẽ đi sửa prompt cho
một lỗi không tồn tại. **Sai kiểu đó không có triệu chứng**, nên phải chặn từ gốc.

### 7.2. Bước 2 — Kiểm tra bộ dữ liệu (miễn phí)

```powershell
python -m evaluation.run_eval check
```

Trả lời **miễn phí** hai câu: "ảnh có đủ không" và "đã gán nhãn tới đâu". Không
có lệnh này thì cách duy nhất để biết là chạy hết cả bộ bằng LLM thật rồi đọc
lỗi ở cuối — tốn tiền cho một câu hỏi không cần LLM. **Luôn chạy `check` trước `run`.**

### 7.3. Bước 3 — Gán nhãn người

```powershell
python -m evaluation.run_eval template        # tạo file nhãn rỗng từ data/
python -m evaluation.run_eval from-archive    # tạo file nhãn từ cert_archive/ (dữ liệu THẬT)
python -m evaluation.run_eval fill-verdict    # điền nhãn
```

`from-archive` có mặt vì **dữ liệu thật chỉ đi qua eLIS đúng một lần** — bật
`SAVE_CERTIFICATES=1` để giữ lại ảnh, sau đó chạy lại bộ đánh giá bao nhiêu lần
cũng được mà không cần eLIS.

File nhãn được ghi bằng **ghi nguyên tử** (file tạm + `os.replace`) để không mất
nhãn tay khi lệnh bị ngắt giữa chừng.

### 7.4. Bước 4 — Chạy thật và chấm điểm

```powershell
python -m evaluation.run_eval run                 # chạy cả bộ
python -m evaluation.run_eval run --limit 10      # thử 10 ca đầu cho rẻ
python -m evaluation.run_eval run --file khac.csv # dùng file nhãn khác
python -m evaluation.run_eval run --traceback     # in traceback ca lỗi đầu tiên rồi dừng
```

Mỗi lần `run` là một lần gọi **LLM thật** cho toàn bộ ca trong file nhãn — cần
mạng công ty và tốn phí. Số ca in ra ở đầu để biết trước quy mô.

Kết quả thô lưu vào `evaluation/last_run.csv`. Có file này vì một lượt chạy tốn
tiền thật: chỉ in ra màn hình thì cuộn mất là mất luôn, muốn xem lại phải trả
tiền chạy lại từ đầu.

**Hai loại điểm, đừng nhầm:**

| Module | Đo cái gì |
|---|---|
| `extraction_score.py` | Model **đọc** đúng chữ trên ảnh chưa (tên, khóa học, ngày) |
| `decision_score.py` | Hệ thống **kết luận** APPROVED/REJECTED đúng chưa |

Đọc đúng mà kết luận sai là **lỗi luật so khớp**. Đọc sai mà kết luận đúng là
**may**. Tách hai con số ra mới biết đi sửa chỗ nào.

### 7.5. Bước 5 — Xuất báo cáo

```powershell
python -m evaluation.run_eval export-errors --out evaluation\error_review.xlsx
python -m evaluation.run_eval export-compare --excel data\information.xlsx
```

- **`export-errors`** — Excel các ca **AI lệch với người**, có sẵn cột trống để
  HR ghi nhận xét. Ghi chú đã có được giữ lại theo cặp *(tên nhân viên, tên khóa
  học)*, **không** theo số thứ tự ca — số thứ tự thay đổi khi bộ dữ liệu được
  dựng lại, và khi đó ghi chú của người này sẽ dán nhầm sang người khác.

- **`export-compare`** — bảng **HUMAN vs AI** dạng CSV 6 cột, đúng format
  `logandcompare.csv` mentor gửi. Truyền `--excel` để in **đủ mọi dòng** của
  file gốc; dòng nào không có ảnh được ghi NOTE `"không có ảnh"` thay vì biến mất.

---

## 8. Luồng 6 — Kiểm tra khả năng trích xuất của LLM2

```powershell
python -m evaluation.run_llm2                     # quét toàn bộ data/image
python -m evaluation.run_llm2 --limit 10          # thử vài ảnh trước
python -m evaluation.run_llm2 --images duong\dan --out ket_qua.xlsx
```

Chạy **OCR + LLM2** trên mọi ảnh và ghi ra Excel **đúng bốn cột**:

```
tên file ảnh | tên người nhận | tên chứng chỉ | thời gian
```

**Không** có cột kết luận, **không** có cột lý do, **không** so với Excel
metadata. Mục đích duy nhất là xem LLM2 **đọc ra gì**, để đối chiếu bằng mắt với
chính tấm ảnh. Thêm cột kết luận vào đây là trộn hai câu hỏi khác nhau: *"model
đọc đúng chữ trên ảnh chưa"* và *"chứng chỉ có hợp lệ không"*. Câu đầu chỉ tấm
ảnh trả lời được; câu sau cần dữ liệu eLIS.

Ảnh nào OCR/LLM hỏng thì ba cột nội dung để **trống**, và tên file được liệt kê
lại ở cuối màn hình. Không nhét thông báo lỗi vào ô dữ liệu: ô trống nghĩa là
"không đọc được", trộn chữ lỗi vào đó sẽ làm hỏng khi lọc bằng Excel. Một ảnh
hỏng **không** giết cả lượt chạy — những ảnh trước đã tốn tiền Azure rồi.

Lệnh này quét **toàn bộ thư mục ảnh**, độc lập hoàn toàn với file Excel.

---

## 9. Luồng 7 — Công cụ chạy tay

### 9.1. Chạy thử một ảnh

```powershell
python run_local.py anh.jpg --name "Nguyễn Văn A" --course "Python cơ bản"
python run_local.py chung_chi.pdf --name "Trần Thị B" --course "An toàn thông tin" --code NV001
```

Chạy đúng pipeline như bản thật nhưng với dữ liệu gõ tay. Không đụng eLIS.

### 9.2. Web demo

```powershell
python web_demo.py
```

Giao diện Gradio ở `http://127.0.0.1:7860`: upload ảnh/PDF, nhập tên/mã/khóa
học, xem ảnh và kết quả APPROVED/REJECTED kèm lý do. Bên trong gọi đúng pipeline
như `run_local`. Không liên quan eLIS.

> **Lưu ý khi thử demo:** trường "khóa học" lấy **nguyên văn** những gì gõ vào.
> Dán cả tên file (`AI入門講座 - AI for Everyone (Japanese Version).pdf`) thì
> đuôi `.pdf` một mình đã đủ làm trượt so khớp — hãy gõ đúng tên khóa như eLIS
> lưu. Ngoài ra, Gradio **giữ prompt trong bộ nhớ**: sửa prompt xong phải khởi
> động lại tiến trình mới thấy thay đổi.

### 9.3. Kiểm tra kết nối eLIS

```powershell
python run.py status
```

Lệnh này gọi **API ① getCert** và in hàng đợi kèm trạng thái thử lại của từng
ca. Chỉ đọc, không tải file, không gọi LLM, không đổi trạng thái bản ghi nào —
chạy bao nhiêu lần cũng được. Xem [3.2](#32-status--lệnh-chẩn-đoán-miễn-phí).

**Không có script nào gọi API ③** vì nó thay đổi trạng thái thật trên eLIS.

#### Những gì đã đo được về API ①

Trong quá trình dựng luật chống nộp trùng, ba script chẩn đoán dùng-một-lần đã
được viết rồi xóa sau khi lấy xong số liệu. Kết quả chúng đo được ghi lại ở đây
vì đó là **căn cứ thiết kế** của mục [5.7](#57-nộp-trùng-khóa-học), và vì đo
lại tốn công hơn nhiều so với đọc một bảng:

| Câu hỏi | Kết quả |
|---|---|
| `status` nhận giá trị nào | `WAITING` / `APPROVED` / `REJECTED` — ba con số khác nhau (4 / 3.134 / 13 trên UAT), tức API **lọc thật** chứ không phớt lờ tham số |
| `size` trần bao nhiêu | **1000** — gửi `size=5000` vẫn chỉ nhận về 1000 |
| Lọc được theo nhân viên? | **Được, bằng `employeeEmail`.** Ba tên khác — `employeeId`, `employee_id`, `employeeCode` — đều bị bỏ qua, cả ba trả về nguyên 3.134 bản ghi. Chọn sai tên tham số là ca hỏng im lặng, API không báo gì |
| Bản ghi APPROVED có gì | `courseId`, `ActionDateTime` (lúc ghi comment), `ActionBy`, `comment`, `submitStatus` |

> **Bẫy khi đo API kiểu này:** bỏ qua tham số lạ là hành vi rất thường gặp. Nếu
> `status=APPROVED` trả về đúng số bản ghi như `status=WAITING` thì đó **không**
> phải "lấy được lịch sử" — đó là API phớt lờ giá trị mình gửi và trả về mặc
> định. Phải **so số liệu giữa các giá trị**, không chỉ xem có báo lỗi hay không.

> **`ActionDateTime` ≠ `submitDatetime`.** Cái đầu là lúc comment được ghi, cái
> sau là lúc học viên nộp. Giao diện eLIS có thể đang hiện cái sau. Lẫn hai mốc
> này đủ để kết luận nhầm rằng một câu chữ do bản code cũ ghi từ tháng trước
> vừa mới được sinh ra hôm qua.

---

## 10. Luồng 8 — Chạy bằng Docker

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

## 11. Cấu hình đầy đủ (`.env`)

Cấu hình đọc bằng `pydantic-settings` từ file `.env` **và** biến môi trường.
Toàn bộ định nghĩa nằm ở `src/config.py`. Thứ tự ưu tiên đầy đủ:

```
tham số  >  biến môi trường  >  kho khóa Windows  >  .env  >  file secrets
```

Kho khóa chỉ giữ bốn khóa bí mật và chỉ có tác dụng trên Windows — xem mục 16.3.
Mọi tham số dưới đây (trừ bốn khóa đó) còn **sửa được ngay trong app desktop**,
có hiệu lực từ vòng sau mà không cần khởi động lại — xem mục 16.2.

### 11.1. FPT AI Marketplace (Gemma — LLM1 & LLM2)

| Biến | Mặc định | Giải thích |
|---|---|---|
| `FPT_API_KEY` | **bắt buộc** | Key FPT AI Marketplace. Thiếu → chương trình dừng ngay lúc khởi động |
| `FPT_BASE_URL` | `https://mkp-api.fptcloud.com` | Endpoint kiểu OpenAI-compatible |
| `FPT_MODEL` | `gemma-4-31B-it` | Model dùng cho **cả** LLM1 (đọc ảnh) và LLM2 (đọc text OCR) |
| `LLM_TEMPERATURE` | `0.2` | Thấp vì đây là việc **trích xuất**, không phải sáng tác. Tăng lên chỉ làm kết quả dao động giữa các lần chạy |
| `LLM_MAX_TOKENS` | `2048` | Đủ cho JSON trích xuất; chứng chỉ nhiều chữ vẫn nằm trong hạn |

### 11.2. Azure Document Intelligence (OCR — tầng 2)

| Biến | Mặc định | Giải thích |
|---|---|---|
| `AZURE_ENDPOINT` | **bắt buộc** | Endpoint tài nguyên Document Intelligence |
| `AZURE_KEY` | **bắt buộc** | Key tài nguyên |

> **Giới hạn gói Free (F0): 500 trang/tháng, 1 request/giây.** Khi chạm trần,
> tầng 2 hỏng và ca đó thành hỏng kỹ thuật. Cách xử lý đúng là **nâng gói**,
> không phải làm yếu tầng 2 đi — tầng 2 chính là lớp bảo vệ chống ca "LLM1 đọc
> sai nhưng nghe hợp lý".

### 11.3. eLIS

| Biến | Mặc định | Giải thích |
|---|---|---|
| `ELIS_BASE_URL` | `https://apitest.fpt.com/uat-elis-gw` | Gốc cho API ① `getCert` và ③ `ProcessUserCourseStatus` |
| `ELIS_FILE_BASE_URL` | `https://apitest.fpt.com/uat-elis-gw` | Gốc cho API ② `download-certificates`. Tách riêng vì eLIS có thể đặt dịch vụ file ở host khác |
| `ELIS_API_KEY` | `""` | Gửi trong header `apikey` |
| `ENV` | `UAT` | Nhãn ghi chú môi trường đang trỏ tới (`UAT` / `Production`) |

### 11.4. Luật nghiệp vụ

| Biến | Mặc định | Giải thích |
|---|---|---|
| `COURSE_MATCH_MODE` | `loose` | `loose` = tên eLIS chỉ cần là **tập con** của tên trên ảnh. `strict` = phải trùng khớp hoàn toàn. Xem [4.2](#42-so-tên-khóa-học--song-ngữ) |
| `VALID_FROM` | `2026-01-01` | Ngày sớm nhất chấp nhận. **Phải đổi khi sang năm mới** |
| `VALID_TO` | `2026-09-30` | Ngày muộn nhất chấp nhận |

### 11.5. Tham số vận hành

| Biến | Mặc định | Giải thích |
|---|---|---|
| `POLL_INTERVAL_SECONDS` | `5` | Chỉ áp dụng khi **hết việc**. Còn việc thì job làm liên tục, không nghỉ |
| `RETRY_COUNT` | `3` | Số lần gọi lại **một request eLIS** khi gặp lỗi tạm thời (502, timeout). Khác hoàn toàn với `TECHNICAL_ALERT_AFTER` |
| `RETRY_DELAY_SECONDS` | `5` | Nghỉ giữa các lần gọi lại đó |
| `TIMEOUT_SECONDS` | `60` | **Hiện không có tác dụng** — `src/client.py` viết cứng `timeout=30` cho API ①, `60` cho ② và ③. Sửa giá trị trong `.env` sẽ không đổi gì. Hoặc nối dây vào `client.py`, hoặc bỏ biến này đi |

> **`BATCH_SIZE` đã bị xóa.** Hệ thống xử lý từng chứng chỉ một, không chia lô.
> Để lại dòng đó trong `.env` cũng vô hại — không mã nào đọc nó nữa.

### 11.6. Kho lưu chứng chỉ

| Biến | Mặc định | Giải thích |
|---|---|---|
| `SAVE_CERTIFICATES` | `0` (**tắt**) | `1` = giữ lại ảnh + thông tin `getCert` mỗi lần tải về, để chạy lại bộ đánh giá mà không cần eLIS (**dữ liệu thật chỉ đi qua eLIS đúng một lần**). Mặc định **tắt** vì chứng chỉ thật chứa tên/mã/email nhân viên |
| `ARCHIVE_DIR` | `cert_archive` | Thư mục kho. Đã nằm trong `.gitignore` |

### 11.7. Thử lại ca hỏng kỹ thuật

| Biến | Mặc định | Giải thích |
|---|---|---|
| `TECHNICAL_RETRY_COOLDOWN_MINUTES` | `2` | Nghỉ bao lâu trước khi thử lại cùng một chứng chỉ. Đủ để eLIS/Azure chập vài giây tự qua, mà sự cố khỏi lúc nào thì chậm nhất 2 phút sau chứng chỉ được xử lý |
| `TECHNICAL_ALERT_AFTER` | `5` | Hỏng tới lần thứ mấy thì **gửi email cảnh báo**. Mỗi vòng thử đúng một lần, nên 5 lần = 5 vòng ≈ 10 phút. **Không phải mốc bỏ cuộc** — máy vẫn thử tiếp ca đó, và hàng đợi vẫn chặn tại nó |

> **`TECHNICAL_RETRY_MAX` vẫn được nhận** (cho `.env` đang chạy khỏi hỏng),
> nhưng **nghĩa đã đổi hẳn**: trước là "thử ngần này lần rồi bỏ cuộc, nộp
> REJECTED", giờ là "hỏng ngần này lần thì báo người, và vẫn thử tiếp". Nên
> đổi sang tên mới `TECHNICAL_ALERT_AFTER` để không ai tưởng nhánh bỏ cuộc
> vẫn còn.

### 11.7b. Email cảnh báo lỗi hệ thống

| Biến | Mặc định | Giải thích |
|---|---|---|
| `ALERT_MAIL_TO` | `hoabd5@fpt.com` | Người nhận cảnh báo, nhiều người cách nhau dấu phẩy. **Khác `MAIL_TO`** — xem [5.3](#53-email-cảnh-báo-srcalertpy) |
| `ALERT_COOLDOWN_HOURS` | `1` | Nhịp **nhắc lại** khi sự cố vẫn còn: mỗi tiếng một thư. **Không làm chậm thư đầu tiên.** Bắt buộc phải có, nếu không một sự cố kéo dài sẽ sinh hàng trăm thư giống hệt nhau |

Cảnh báo dùng chung cấu hình SMTP với báo cáo (`SMTP_HOST`, `SMTP_USER`,
`SMTP_PASSWORD`) — chỉ khác người nhận.

### 11.7c. Chống nộp trùng khóa học

| Biến | Mặc định | Giải thích |
|---|---|---|
| `DUPLICATE_CHECK` | `1` | `1` = bật. Chứng chỉ của khóa nhân viên **đã được duyệt** bị từ chối ngay, không tốn lượt LLM nào. Mỗi chứng chỉ tốn thêm một request hỏi eLIS (~0,24 giây) |

Chi tiết cơ chế ở [5.7](#57-nộp-trùng-khóa-học).

**Luật bỏ qua (mục 5.6) không có tham số nào trong `.env`** — nó luôn bật. Đuôi
email công ty được viết cứng ở `compare.COMPANY_EMAIL_DOMAIN = "fpt.com"`; sửa
một dòng đó là đổi được, nhưng đây là quyết định nghiệp vụ nên cố ý không để
người vận hành đổi qua `.env`.

### 11.8. Email

| Biến | Mặc định | Giải thích |
|---|---|---|
| `SMTP_HOST` | `smtp.office365.com` | Gmail dùng `smtp.gmail.com` |
| `SMTP_PORT` | `587` | STARTTLS |
| `SMTP_USER` | `""` | Cũng nhận tên `SMTP_USERNAME` — hai tên là **một** |
| `SMTP_PASSWORD` | `""` | **Luôn là App Password**, không phải mật khẩu đăng nhập |
| `MAIL_FROM` | `""` | Địa chỉ From. Rỗng = dùng `SMTP_USER`. Gmail **luôn ghi đè** trường này |
| `MAIL_TO` | `""` | Người nhận. Cũng nhận tên `MANAGER_EMAIL` hoặc `MAIL_DEN` |

### 11.9. Lịch báo cáo tự động

| Biến | Mặc định | Giải thích |
|---|---|---|
| `REPORT_SCHEDULE` | `off` | `off` \| `daily` \| `weekly` \| `monthly`. Xem [6.2](#62-gửi-tự-động) |
| `REPORT_TIME` | `18:00` | Giờ gửi (HH:MM, giờ VN) |
| `REPORT_WEEKDAY` | `4` | Chỉ dùng cho `weekly`. `0`=Thứ Hai … `4`=Thứ Sáu … `6`=Chủ nhật |
| `REPORT_MONTHDAY` | `1` | Chỉ dùng cho `monthly` (báo cáo cho **tháng trước**) |
| `REPORT_BUCKET` | `""` (tự chọn) | Mốc gom số liệu trong biểu đồ: `day` \| `week` \| `month`. Đặt thô hơn kỳ báo cáo sẽ **tự bị hạ xuống** — gom 7 ngày theo tuần chỉ ra đúng một cột |

---

## 12. Cơ sở dữ liệu log

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

> **`skipped_external_email` và `duplicate` cố ý KHÔNG nằm trong
> `TECHNICAL_STAGES`.** Thêm vào đó thì `technical_retry_state()` đếm chúng như
> sự cố hệ thống, và với chu kỳ poll 5 giây thì 25 giây sau người vận hành nhận
> mail báo động về một chứng chỉ mà hệ thống chẳng làm gì sai.

> **Tên `skipped_external_email` hiện hơi hẹp nghĩa** — nó được đặt lúc luật bỏ
> qua mới chỉ có một điều kiện (email ngoài công ty), giờ nó chứa cả ca thiếu
> họ/tên đệm. Đổi thành `skipped_unverified_identity` thì đúng hơn, nhưng những
> dòng cũ trong DB sẽ mang giá trị cũ và không được `skipped_ids()` nhận ra
> nữa. Để nguyên cho tới lần dọn DB gần nhất.

**Cột `course_name` có mặt vì:** thiếu nó thì từ DB không thể biết dòng log nào
ứng với khóa học nào. Ca hỏng kỹ thuật còn tệ hơn — không đọc được ảnh nên
`certificate_name` cũng `NULL`, dòng log chỉ còn một chuỗi id vô nghĩa.

`init_db()` vừa `CREATE TABLE IF NOT EXISTS` vừa `ALTER TABLE` thêm cột thiếu.
Phải có phần thứ hai vì `CREATE TABLE IF NOT EXISTS` chỉ chạy khi bảng **chưa**
tồn tại — với máy đã chạy job trước đó, thêm cột vào phần `CREATE` **không có
tác dụng** và chương trình lỗi `no such column` dù code trông đúng.

---

## 13. Cấu trúc thư mục

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

## 14. Test

```powershell
pytest              # toàn bộ — 443 test
ruff check .        # lint
```

Test **không** gọi API thật — mọi hàm gọi API được truyền vào pipeline dưới dạng
tham số (dependency injection), nên test thay bằng hàm giả.

`tests/conftest.py` đặt `DUPLICATE_CHECK=0` cho toàn bộ phiên chạy. Không có
dòng đó thì mọi test đi qua `process_one_round` đều gọi eLIS thật để hỏi lịch
sử — chậm, phụ thuộc mạng, và bẩn. `tests/test_duplicate.py` tự bật luật cho
riêng nó, và fixture của nó xóa `run._approved_this_round` cả trước lẫn sau
mỗi test: đó là biến mức module nên nó sống xuyên suốt cả phiên, không xóa thì
một test để lại dữ liệu cho mọi test chạy sau — kiểu rò rỉ chỉ lộ ra khi đổi
thứ tự test.

Vài test đáng chú ý:

- `test_diem_vao.py` — canh **mọi điểm vào** đặt `KMP_DUPLICATE_LIB_OK` **trước**
  import nặng. Chính test này đã phát hiện `match_images.py` còn thiếu.
- `test_prompt.py` — canh `llm_vision` và `llm_text` khai đúng ánh xạ
  trường ↔ ngôn ngữ, không chỉ kiểm tra "có chữ TIẾNG VIỆT trong prompt".
- `test_match_images.py` — canh việc **không** tách tên file theo `_` đầu tiên.
- `test_course_name.py` — canh cả ba đường khớp song ngữ.
- `test_llm_error.py` — canh cái bẫy mã `429` (rate-limit vs hết tiền), và
  canh việc lỗi vĩnh viễn **không** bị thử lại.
- `test_retry.py::test_qua_nguong_KHONG_BAO_GIO_nop_rejected` — canh **luật
  HR**. Ai khôi phục nhánh bỏ cuộc cũ thì test này đỏ.
- `test_alert.py` — phần lớn canh việc **không gửi trùng**, vì gửi thiếu thì
  thấy ngay còn gửi trùng chỉ phát hiện khi đã spam mất người nhận.
- `test_skip.py` — 7 ca email và 9 ca thiếu tên đệm đều lấy **nguyên từ bộ
  đánh giá thật**, nên test hỏng nghĩa là hành vi lệch khỏi dữ liệu thật chứ
  không phải lệch khỏi ý tôi. `test_ca_bo_qua_KHONG_BAO_GIO_duoc_nop_ve_elis`
  canh chiều ngược: ai nối ca bỏ qua vào API ③ thì test này đỏ.
- `test_app_runner.py` — canh chỗ app desktop **tự ghi sổ** cho `alert` sau khi
  tự gọi API ①. Quên `api_succeeded` thì bộ đếm lỗi không bao giờ về 0: eLIS
  đã sống lại từ lâu mà hệ thống vẫn gửi thư báo động.
- `test_settings_file.py` — canh việc ghi ngược `.env` **không làm mất chú
  thích**, và canh thứ tự ghi-đĩa-trước-gán-bộ-nhớ-sau. Có một test quét AST
  toàn dự án để chắc không chỗ nào đọc `settings.X` ở mức module — nền tảng
  của việc sửa cấu hình mà không cần khởi động lại.
- `test_vault.py` — canh **thứ tự ưu tiên** của nguồn cấu hình: kho khóa phải
  thắng `.env` (không thì nút "Lưu khóa" là nút giả) nhưng phải thua biến môi
  trường (không thì Docker và lệnh ghi đè một lần mất tác dụng).
- `test_duplicate.py::test_nop_cung_khoa_HAI_LAN_trong_MOT_vong` — canh **cửa
  chặn thứ hai**. Chính test này phát hiện thiết kế ban đầu chỉ lọc một lần ở
  đầu vòng nên hai bản ghi trùng nhau trong cùng một vòng đều lọt.

**Mỗi luật mới đều được kiểm bằng đột biến**: cố ý làm hỏng từng chốt rồi xem
test có bắt không. Lần chạy đầu của luật bỏ qua có một chốt lọt lưới (thứ tự
kiểm tra trong `_unverifiable_identity`), phải viết thêm test mới bắt được.

Luật kho khóa cũng vậy: 8 đột biến, lần đầu lọt một chốt. Bỏ điều kiện
`sys.platform != "win32"` trong `vault.py` mà test vẫn xanh — vì trên Linux
`keyring` trả về backend `fail` nên kết quả cuối vẫn là `None`. Xanh ở CI, hỏng
trên máy thật. Phải sửa test thành **cấm luôn câu import** mới bắt được.

---

## 15. Lưu ý bảo mật

> ### ⚠️ Vẫn phải làm bằng tay: THU HỒI KEY
> `.env.example` **được git theo dõi** (`.gitignore` có dòng `!.env.example`)
> và trước đây chứa một `FPT_API_KEY` thật. Giá trị trong file **đã được thay
> bằng placeholder**, nhưng **key vẫn còn trong lịch sử commit** — xóa khỏi
> file không xóa được khỏi lịch sử git.
>
> **Phải thu hồi và cấp lại key đó trên FPT AI Marketplace.** Đây là việc duy
> nhất thật sự đóng lỗ hổng; mọi thao tác trên file chỉ là dọn dẹp.

**Những file chứa PII nhân viên — đã gitignore, không được commit:**

```
.env  ·  mooc_log.db  ·  cert_archive/  ·  data/
evaluation/*.csv  ·  evaluation/*.xlsx  ·  eval_images/
match_report.csv  ·  error_review.xlsx
.report_state.json  ·  .alert_state.json
```

**Nguyên tắc khác:**

- Báo cáo gửi **cho mentor**, không gửi cho khách hàng eLIS.
- Không in credential thật ra màn hình hay log.
- `SAVE_CERTIFICATES` mặc định **tắt**. Chỉ bật khi cần dựng bộ đánh giá, và
  nhớ dọn `cert_archive/` sau đó.
- Gửi PII nhân viên qua Gmail cá nhân cần được mentor duyệt trước khi dùng thật.
- Container chạy bằng **user thường**, không phải root; `.env` được **gắn lúc
  chạy**, không nhúng vào image.
- Trên Windows, bốn khóa bí mật nên để trong Credential Manager thay vì `.env`
  (xem mục 16.3). Nó bảo vệ **file**, không bảo vệ **máy**: ai đăng nhập được
  đúng tài khoản Windows đó vẫn đọc ra được.

---

## 16. App desktop trên Windows (`main_app.py`)

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

Chỉ hai trong năm ô số lấy thẳng từ `RoundResult` (eLIS đã nhận, Đang hoãn).
Ba ô còn lại app tự tính từ ba nguồn ở bảng trên.

### 16.1 Ba tab

| Tab | Có gì |
|---|---|
| **Bảng điều khiển** | 5 ô số (hàng đợi · eLIS đã nhận · từ chối · bỏ qua · đang hoãn), nhật ký cuộn theo dòng mới, nút Tạm dừng / Chạy vòng ngay / Thử lại ca đang hoãn / Mở thư mục log |
| **Ca bỏ qua** | Danh sách chứng chỉ để nguyên WAITING vì không xác minh được danh tính, đọc từ `mooc_log.db` |
| **Cấu hình** | Bốn ô nhập khóa bí mật (ghi vào kho khóa Windows) + **31 ô sửa được** cho mọi cấu hình còn lại |

### 16.2 Sửa cấu hình ngay trong app

Mọi tham số trong `.env` — trừ bốn khóa bí mật, xem 16.3 — sửa được ở tab Cấu
hình. Bấm **Lưu thay đổi** là ba việc xảy ra, theo đúng thứ tự này:

1. **Kiểm trước, trên một BẢN SAO.** `POLL_INTERVAL_SECONDS = "abc"` bị chặn
   ngay ở hộp thoại, không phải đợi vòng sau nổ trong luồng nền. Kiểm trên bản
   sao chứ không gán thẳng: sửa ba trường mà trường thứ ba sai thì hai trường
   đầu đã kịp đổi, và hệ thống chạy tiếp bằng một nửa cấu hình mới.
2. **Ghi xuống `.env`.**
3. **Gán vào object `settings` đang chạy** — có hiệu lực từ vòng kế tiếp,
   không phải khởi động lại.

**Thứ tự 2 trước 3 là bắt buộc.** Làm ngược lại thì khi đĩa đầy hoặc file bị
khóa, hệ thống chạy bằng cấu hình mới trong khi file vẫn giữ cấu hình cũ —
khởi động lại là im lặng quay về giá trị cũ và không ai hiểu vì sao.

Việc (3) chạy được là nhờ **không chỗ nào trong dự án đọc `settings.X` ở mức
module** — mọi nơi đều đọc lại trong thân hàm. Điều đó không tự nhiên đúng mãi:
ai đó viết `NGUONG = settings.technical_alert_after` ở đầu file là từ đó nút Lưu
im lặng mất tác dụng cho riêng chỗ ấy. `test_settings_file.py` quét bằng AST để
canh.

**Ghi `.env` không làm mất chú thích.** File `.env` có chú thích giải thích từng
tham số; dựng lại file từ một dict là xóa sạch. Code sửa đúng chỗ giá trị trên
từng dòng, giữ nguyên mọi thứ còn lại — kể cả chú thích cuối dòng
(`RETRY_COUNT=3   # ba lần là đủ`).

**Tên biến phụ được tôn trọng.** `TECHNICAL_ALERT_AFTER` còn ăn tên cũ
`TECHNICAL_RETRY_MAX`; nếu `.env` đang dùng tên cũ thì code sửa đúng dòng đó chứ
không thêm dòng thứ hai — hai dòng cho một tham số thì người đọc sau không biết
dòng nào có tác dụng.

**Đổi lại là một cuốn sổ.** Cho sửa luật nghiệp vụ bằng vài cú bấm chuột thì
phải có dấu vết: mỗi thay đổi ghi một dòng `thời điểm · tài khoản Windows · tên
biến · cũ → mới` vào `config_changes.log` (gitignored) và vào nhật ký của app.
Không có nó, ba tháng sau không ai trả lời được câu "vì sao `COURSE_MATCH_MODE`
thành `strict`".

Hai trường chỉ nhận vài giá trị (`course_match_mode`, `report_schedule`) hiện
dạng danh sách chọn chứ không phải ô gõ tự do: gõ `"Strict"` hoa chữ S thì
pydantic nhận, nhưng `pipeline.py` so bằng `==` nên luật siết im lặng không bật.

### 16.3 Khóa bí mật để trong kho khóa Windows

Bốn giá trị `FPT_API_KEY`, `AZURE_KEY`, `ELIS_API_KEY`, `SMTP_PASSWORD` cất
được vào Credential Manager thay vì để chữ thường trong `.env`. Windows mã hóa
bằng DPAPI, khóa gắn với tài khoản đang đăng nhập. Code ở `src/vault.py`.

**Thứ tự ưu tiên khi đọc cấu hình** (`Settings.settings_customise_sources`):

```
tham số  >  biến môi trường  >  KHO KHÓA  >  .env  >  file secrets
```

Hai vị trí đều có lý do, đặt sai chỗ nào cũng hỏng im lặng:

- **Trên `.env`** — nếu `.env` thắng thì nút "Lưu" trong app thành nút không làm
  gì cả: người dùng nhập key mới, app báo đã lưu, chương trình vẫn chạy key cũ.
- **Dưới biến môi trường** — `set AZURE_KEY=... && python run.py` là cách thử
  một key khác cho đúng một lần chạy; để kho khóa thắng thì lệnh đó mất tác
  dụng. Docker cũng truyền cấu hình bằng biến môi trường.

**Nó bảo vệ FILE, không bảo vệ MÁY.** Ai đăng nhập được đúng tài khoản Windows
đó vẫn đọc ra được bằng chính thư viện `keyring`. Câu mô tả đúng là "khóa không
còn nằm dạng chữ thường trên đĩa", không phải "đã bảo mật". Hệ quả kèm theo:
chép thư mục dự án sang máy khác thì khóa **không** đi theo — phải nhập lại.

**Bản Docker không đổi gì.** `src/vault.py` trả về rỗng ở mọi máy không phải
Windows, và `keyring` cố ý **không** nằm trong `requirements-job.txt` — trên
Linux nó kéo theo SecretStorage + jeepney rồi đi hỏi D-Bus, mà container không
có D-Bus. `tests/test_vault.py` cấm luôn câu `import keyring` khi không phải
Windows, chứ không chỉ kiểm giá trị trả về.

### 16.4 Tự chạy khi mở máy

```powershell
powershell -ExecutionPolicy Bypass -File tools\autostart.ps1 install
powershell -ExecutionPolicy Bypass -File tools\autostart.ps1 status
powershell -ExecutionPolicy Bypass -File tools\autostart.ps1 uninstall
```

Không cần quyền admin. Dùng Task Scheduler chứ không dùng khóa Registry `Run`:
khóa `Run` chỉ chạy chương trình đúng một lần lúc đăng nhập, app chết giữa
chừng là thôi, sáng hôm sau mới biết. Task Scheduler chạy lại được và nói cho
biết lần chạy cuối kết thúc ra sao.

Hai mặc định của Windows **sai** với app này, script phải đặt lại:

| Tham số | Mặc định | Vì sao phải đổi |
|---|---|---|
| `ExecutionTimeLimit` | 3 ngày | Windows tự giết app vào ngày thứ tư |
| `*OnBatteries` | dừng khi rút sạc | Máy vận hành là laptop → job chết im lặng |

Script còn đặt `-MultipleInstances IgnoreNew`. Cái này **trùng** với mặc định
của Windows, viết ra chỉ để nói rõ ý — nó là lớp phòng thủ thứ hai bên cạnh
khóa socket trong `main_app.py`, vì chạy hai bản là mỗi chứng chỉ tốn hai lượt
LLM.

Script chạy `pythonw.exe` chứ không phải `python.exe`: `python.exe` để lại một
cửa sổ console đen trên màn hình mỗi lần đăng nhập, mà cửa sổ đó đóng là job
chết.

### 16.5 Ba điều phải nói trước với mentor

- **Máy tắt là job dừng.** Khác hẳn Docker trên server. Tắt máy buổi tối thì đêm
  đó không chứng chỉ nào được xử lý, sáng hôm sau hàng đợi dồn lại.
- **Chỉ chạy được trên máy đã allowlist.** eLIS chặn theo IP, nên không phát
  file cài này cho người khác trong công ty được. App dành cho đúng một người.
- **Chạy hai bản là hỏng.** `main_app.py` giữ một socket ở `127.0.0.1:47615`
  làm khóa; bản thứ hai mở lên sẽ tự thoát. Dùng socket chứ không dùng file
  khóa vì file khóa còn nguyên sau khi app bị kill, lần mở sau tưởng đang có
  bản chạy dù không có.

---

## 17. Chạy nền trên server Linux

Mục 16 là app chạy trên máy người vận hành. Mục này là cách chạy thật: job
nằm trên server công ty, chạy 24/7, tự bật lại khi chết và khi server khởi
động lại. HR không vào server — HR vào **eLIS** xem kết quả; việc của server
chỉ là đẩy phán quyết lên đó đều đặn.

### 17.0 Quyết định trước: CHỈ MỘT chỗ được chạy

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

### 17.1 Cách nhanh nhất: Docker trên server

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

### 17.2 Bốn thứ hay làm hỏng bước trên

**`touch mooc_log.db` không phải thừa.** File này bị gitignore nên bản clone
trên server không có nó. `docker-compose.yml` gắn nó theo kiểu bind-mount
**file**; Docker gặp bind-mount file chưa tồn tại sẽ tạo một **thư mục** trùng
tên, và SQLite báo một lỗi chẳng liên quan gì tới nguyên nhân. File rỗng 0
byte là một cơ sở dữ liệu SQLite hợp lệ, `init_db()` tự tạo bảng.

Tương tự với `.alert_state.json`: thiếu nó thì mỗi lần container dựng lại là
gửi thêm một thư cảnh báo — đúng lúc hệ thống đang hỏng nhất thì hộp thư
người vận hành ngập thư trùng.

**Quyền ghi file (`uid`) — chỗ tốn thời gian nhất.** User trong image mang uid
1000, còn file gắn từ ngoài vào giữ nguyên chủ sở hữu của host. Tài khoản của
bạn không phải uid 1000 (thường gặp trên server nhiều tài khoản) thì hỏng, bằng
hai lỗi chẳng gợi gì tới quyền file:

```
PermissionError: [Errno 13] Permission denied: '.env'
sqlite3.OperationalError: attempt to write a readonly database
```

Câu thứ hai đặc biệt dễ lạc hướng: `mooc_log.db` ghi được, nhưng SQLite còn
phải tạo file `-journal` **cùng thư mục**, mà `/app` thuộc về user trong image.

Kiểm bằng `id -u`. Khác 1000 thì:

```bash
printf 'services:\n  job:\n    build:\n      args:\n' > docker-compose.override.yml
printf '        APP_UID: "%s"\n        APP_GID: "%s"\n' "$(id -u)" "$(id -g)" \
    >> docker-compose.override.yml
docker compose build
```

Compose tự nạp `docker-compose.override.yml`, và file đó đã gitignore nên mỗi
máy giữ một bản riêng — `git pull` không xung đột, Docker Desktop trên Windows
không dính.

**Đừng dùng `user:` trong compose.** Nó đổi uid của *tiến trình* nhưng không đổi
chủ sở hữu `/app` trong image: `.env` đọc được, rồi SQLite vẫn báo đúng câu
`readonly database`. Phải sửa từ lúc **build**, bằng `APP_UID`/`APP_GID`.

**Chạy `docker` không cần `sudo`.** Gặp `permission denied ... docker.sock`
thì `sudo usermod -aG docker $USER` rồi **đăng xuất SSH và vào lại** — nhóm
mới chỉ có tác dụng từ phiên đăng nhập sau.

**eLIS chặn theo IP.** IP server gần như chắc chắn khác IP máy bạn. Đó là lý
do bước `docker compose run --rm job python run.py status` đứng TRƯỚC bước
`up`: nhận `403` ở đó nghĩa là phải xin bên eLIS thêm IP server vào allowlist,
không có cách nào code vòng qua. Bật `up` khi chưa xong thì container chỉ
restart vô tận trong nền.

Múi giờ thì không phải lo: `Dockerfile` đã đặt `TZ=Asia/Ho_Chi_Minh`, kể cả
khi server để UTC.

### 17.3 Vận hành hằng ngày

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

### 17.4 Nếu server KHÔNG có Docker

Thì dùng systemd theo người dùng, **không cần quyền root**:

```bash
sudo apt install -y libmagic1            # python-magic cần thư viện HỆ THỐNG này
python3 -m venv .venv
.venv/bin/pip install -r requirements-job.txt

bash tools/preflight.sh                  # kiểm mọi thứ, kể cả IP allowlist

mkdir -p ~/.config/systemd/user
cp tools/mooc.service ~/.config/systemd/user/
# sửa WorkingDirectory và ExecStart trong file cho đúng đường dẫn thật
systemctl --user daemon-reload
systemctl --user enable --now mooc
loginctl enable-linger $USER             # BẮT BUỘC
```

`loginctl enable-linger` là dòng dễ quên nhất và hậu quả khó đoán nhất:
thiếu nó thì systemd giết mọi dịch vụ của bạn **ngay khi đăng xuất SSH**. Job
chạy ngon suốt lúc bạn còn ngồi đó rồi chết lúc đóng terminal — kiểu hỏng làm
người ta đi tìm lỗi trong code.

`nohup python run.py loop &` cũng chạy nền được, nhưng nó không dựng lại khi
job chết và không tự chạy lại sau khi server reboot — hai thứ chính là lý do
tồn tại của cả mục này.

Xem log: `journalctl --user -u mooc -f`. Vẫn phải làm 17.1 (chép `.env`, tạo
ba file) và vẫn dính chuyện IP allowlist ở 17.2.

### 17.5 HR nhìn thấy gì

HR **không** cần vào server. Có hai đường:

| Đường | Cách bật |
|---|---|
| **eLIS** — mỗi chứng chỉ đổi từ WAITING sang APPROVED/REJECTED kèm lý do | Tự động, không phải làm gì |
| **Email báo cáo định kỳ** — số liệu tổng hợp gửi thẳng hộp thư | Đặt `REPORT_SCHEDULE=daily` và `MAIL_TO=<email HR>` trong `.env`, rồi `docker compose restart` |

Ca **bỏ qua** (không xác minh được danh tính) ở lại WAITING và cần người
duyệt xử lý tay trên eLIS — hiện chưa nằm trong báo cáo định kỳ, xem phụ lục
việc còn treo. Nếu HR chỉ nhìn báo cáo, họ sẽ không biết những ca đó đang chờ
mình.

---

## Phụ lục — Việc còn treo

| Việc | Tình trạng |
|---|---|
| **Đo tỷ lệ bắt trúng của luật nộp trùng** | Chưa chạy. Hàng đợi production không có ca trùng nào nên kết quả 0/59 chỉ chứng minh luật không bắt oan. Cách đo: chạy luật ngược lên các bản ghi `REJECTED` mà người duyệt ghi lý do có chữ "trùng" |
| **Bốn ca tên không khớp chưa quyết** | `tienpham89`, `kieuhuuthanh23698` (ảnh in username, không có `@` nên `external_email` không bắt) và hai ca AI không đọc ra tên nào. Hiện vẫn `REJECTED` |
| Danh sách ca **bỏ qua** chưa vào báo cáo định kỳ | Hiện chỉ có dòng log lúc chạy; tắt job là mất dấu. Trên eLIS chúng trông y hệt chứng chỉ chưa tới lượt xử lý |
| Đo lại độ chính xác sau khi sửa prompt + khớp song ngữ + hai luật mới | Chưa chạy — cần gọi LLM thật trên cả bộ |
| **Thu hồi key FPT đã lộ trong lịch sử git** | `.env.example` đã sạch, nhưng **key vẫn phải cấp lại** — xem mục 15 |
| Thêm `DUPLICATE_CHECK` vào `.env` thật | `.env.example` đã có. Thiếu trong `.env` thì vẫn chạy đúng vì mã có giá trị mặc định |
| Điền `SMTP_USER` / `SMTP_PASSWORD` để cảnh báo gửi được | Không có SMTP thì `alert.py` chỉ ghi lỗi vào log, không ai nhận được thư |
| Tạo `.alert_state.json` trước khi `docker compose up` | Giống `mooc_log.db`: bind-mount file chưa tồn tại thì Docker tạo một **thư mục** trùng tên |
| Đổi tên `stage` `skipped_external_email` → `skipped_unverified_identity` | Tên hiện tại hẹp nghĩa hơn thứ nó chứa. Để lại tới lần dọn DB gần nhất — xem mục 12 |

---

## Phụ lục — Số liệu đã đo

Mọi con số trong tài liệu này đều đo được lại, không phải ước lượng.

**Từ `data/information.xlsx`** — 208.426 bản ghi eLIS thật (ảnh chụp 26/08/2026):

| Đo cái gì | Kết quả |
|---|---|
| Tên miền email | `fpt.com` 152.990 · `fe.edu.vn` 54.845 · ngoài FPT 583 · **không có gmail/yahoo nào** |
| Email thiếu, hoặc 1 email ứng 2 mã NV | **0** — email là khóa định danh tin cậy |
| Số từ trong tên nhân viên | 2 từ: 943 · 3 từ: 138.571 · 4 từ: 68.267 · 5-6 từ: 645 · **không có tên 1 từ** |
| Cách viết tên khóa học | 6.268 thô → **6.132** sau `normalize()` (gộp 129 nhóm) |
| Cặp (NV, khóa) nộp từ 2 lần | 4.270, trong đó **3.944** đã có ít nhất một lần duyệt |
| Cặp được duyệt từ 2 lần | 1.163 — **613 trong cùng một ngày**, 73 cách nhau trên 6 tháng |
| APPROVED không có tên người duyệt, khóa nội bộ FPT | **80.217** — dấu vết luồng đồng bộ tự động |

**Từ `evaluation/last_run.csv`** — 162 ca đã chạy pipeline thật:

| Đo cái gì | Kết quả |
|---|---|
| Ca có email trong tên đọc được | 7 — 6 ca email công ty **đang chạy đúng**, 1 ca gmail cá nhân |
| Ca từ chối vì `"Tên không khớp"` | 14 — 9 ca thiếu họ/tên đệm, 1 gmail, 2 username, 2 AI không đọc ra tên |
| Trong 10 ca hai luật bỏ qua bắt được | **6 ca còn sai cả khóa học hoặc ngày** → vẫn `REJECTED`, không bỏ qua |

**Từ API eLIS UAT** (đo bằng script chẩn đoán dùng-một-lần, đã xóa — xem [9.3](#93-kiểm-tra-kết-nối-elis)):

| Đo cái gì | Kết quả |
|---|---|
| `status` nhận giá trị nào | WAITING 4 · APPROVED 3.134 · REJECTED 13 · không truyền: 3.151 |
| `size` trần | **1000** |
| Lọc theo `employeeId` | **Không** — API bỏ qua tham số |
| Lọc theo `employeeEmail` | **Được** — đo trên production: 38 nhân viên, 25 người có dữ liệu, 13 người rỗng. Nếu tham số bị bỏ qua thì cả 38 phải cùng rỗng |
| Hàng đợi production | 59 chứng chỉ WAITING, 38 nhân viên, **0 ca nộp trùng** |
| Cặp (NV, khóa) trùng trong lịch sử UAT | 1/3.130 — UAT gần như không có hiện tượng này, **không kiểm chứng được luật ở đây** |

**Chi phí chỉ mục lịch sử** (đo với 194.000 mục, đúng khối lượng production):

| Cách lưu | Bộ nhớ |
|---|---|
| `dict[(employeeId, courseId)] -> datetime` | 17,4 MB |
| `set["email\|khóa"]` | 25,4 MB |

100 lượt tra mỗi vòng poll: **0,004 mili giây**. Tra chỉ mục là một phép băm
nên chi phí không phụ thuộc chỉ mục có 3.000 hay 200.000 mục — thứ thật sự tốn
là **lần nạp lại** (~194 request HTTP), không phải bộ nhớ.
