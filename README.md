# MOOC — Hệ thống xác minh chứng chỉ khóa học bằng AI

Hệ thống đọc ảnh chứng chỉ mà nhân viên nộp lên eLIS, đối chiếu với dữ liệu
eLIS đã đăng ký (tên nhân viên, tên khóa học, thời gian), rồi tự nộp kết luận
**APPROVED / REJECTED** ngược về eLIS.

Mục tiêu: bỏ bước duyệt tay từng chứng chỉ, nhưng **không** duyệt bừa — mọi ca
hệ thống không tự tin đều để lại dấu vết đủ để người tra lại.

---

## Mục lục

1. [Kiến trúc và nguyên lý ba tầng](#1-kiến-trúc-và-nguyên-lý-ba-tầng)
2. [Cài đặt](#2-cài-đặt)
3. [Luồng 1 — Job sản xuất `run.py`](#3-luồng-1--job-sản-xuất-runpy)
4. [Luồng 2 — Luật so khớp](#4-luồng-2--luật-so-khớp)
5. [Luồng 3 — Ca hỏng kỹ thuật và cơ chế thử lại](#5-luồng-3--ca-hỏng-kỹ-thuật-và-cơ-chế-thử-lại)
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

### 1.3. Ba nhãn kết luận

| Nhãn | Nghĩa | Có nộp về eLIS không |
|---|---|---|
| `APPROVED` | Chứng chỉ hợp lệ | Có |
| `REJECTED` | Nội dung chứng chỉ không khớp dữ liệu eLIS | Có |
| `WAITING` | **Hệ thống chưa xử lý được** (lỗi kỹ thuật) | **Không** — để nguyên trên eLIS cho vòng sau |

`WAITING` chỉ tồn tại trong log và trên màn hình. Nó có mặt vì nếu hiển thị
những ca này là `REJECTED`, người vận hành sẽ đọc log rồi đi báo học viên
"chứng chỉ bị từ chối", trong khi hệ thống chỉ đang hẹn thử lại sau vài phút.

> **Luật HR: lỗi hệ thống KHÔNG BAO GIỜ thành `REJECTED`.** Ca hỏng kỹ thuật
> ở lại `WAITING` và được thử lại mãi, không giới hạn số lần; hỏng tới ngưỡng
> thì gửi email cảnh báo cho người vận hành. Chi tiết ở [mục 5](#5-luồng-3--ca-hỏng-kỹ-thuật-và-cơ-chế-thử-lại).

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

# 3. Kiểm tra kết nối eLIS trước khi chạy job
python test_api.py 1
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
① getCert (tối đa 100 item)
      │
      ▼
Cắt hàng đợi (_sap_xep_va_loc) — GIỮ NGUYÊN thứ tự eLIS trả về
      ├─ gặp ca còn giãn cách → DỪNG tại đó, mọi ca sau CHỜ THEO
      └─ đã hỏng ≥ TECHNICAL_ALERT_AFTER → GỬI EMAIL cảnh báo,
                                            và VẪN ở nguyên vị trí cũ
      │
      ▼
Chia lô theo BATCH_SIZE (ép về khoảng 1–20)
      │
      ▼   với mỗi lô:
② download-certificates
      ├─ cả lô lỗi     → ghi log WAITING từng cái, sang lô sau
      └─ thiếu file    → ghi log WAITING (stage = no_file)
      │
      ▼   với mỗi file:
(tùy chọn) lưu vào cert_archive/  ← đặt TRƯỚC khi scan
      │
      ▼
file_utils.read_as_images()   ← kiểm MIME bằng nội dung thật, PDF render 200 DPI
      │
      ▼
pipeline.process()  → APPROVED / REJECTED / hỏng kỹ thuật
      │
      ▼
In kết luận ra màn hình → ghi DB → nộp NGAY lô này về ③
      │
      ▼
Hỏng kỹ thuật → KHÔNG nộp, KHÔNG chuyển chỗ, và DỪNG CẢ VÒNG.
                Các ca phía sau chưa tới lượt. Vòng sau (hết giãn cách)
                thử lại CHÍNH NÓ, ở CHÍNH CHỖ đó.
```

**Hàng đợi chạy đúng thứ tự và chặn đầu hàng.** Xem
[5.2b](#52b-chặn-đầu-hàng-chưa-xong-1-thì-chưa-tới-lượt-2).

**Vì sao nộp ngay sau mỗi lô, không gom hết rồi nộp một lần:** kết quả chưa nộp
thì bản ghi vẫn `WAITING`, vòng poll sau sẽ tải lại và gọi LLM lại — tốn thêm
một lượt LLM cho mỗi cái. Gom cả mẻ rồi mới nộp còn khiến toàn bộ công đã làm
phụ thuộc vào một request duy nhất ở cuối; chỉ cần nó hỏng (rớt mạng, container
restart) là mất sạch.

**Vì sao lưu archive TRƯỚC khi scan:** nếu pipeline chết giữa chừng thì ảnh vẫn
còn — mà ca làm pipeline chết mới là ca đáng nghiên cứu nhất.

### 3.4. Hai trường thông tin nộp về eLIS

| Trường | Nội dung | Người đọc |
|---|---|---|
| `comment` | Kết luận cho học viên. APPROVED → "Hợp lệ". Sai nghiệp vụ → nêu đúng trường sai. Hỏng kỹ thuật → câu trung tính, **không đổ lỗi học viên**, không lộ chi tiết nội bộ. | Học viên |
| `comment_cer` | Thông tin AI **đọc được** từ ảnh (`AI đọc được — Tên: … \| Khóa học: … \| Ngày: …`), để người duyệt đối chiếu bằng mắt. Không phải kết luận. | Người duyệt |

Tên tầng xử lý (`llm1`, `llm2`…) **không** xuất hiện ở hai trường này — chúng
chỉ có nghĩa với người bảo trì và đã nằm trong bảng log.

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
  tiếp** trong tên trên ảnh không. Mã là chuỗi máy nên phải khớp chính xác.

> ⚠️ **Luật tên đệm đang chờ HR.** Hiện tại tên rút gọn (`"Anh Le"` cho
> `"Lê Hoàng Anh"`) bị coi là **không khớp**. Đo trên bộ dữ liệu thật: nới luật
> này giảm số ca từ chối oan từ 19 xuống 9, nhưng `"Nguyễn Tuấn"` sẽ khớp với
> nhiều nhân viên khác nhau. **Không sửa cho tới khi có kết luận từ HR.**

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

Không kèm giá trị đọc được và không kèm tên tầng — hai thứ đó nằm ở
`comment_cer` và ở cột `stage` trong DB.

---

## 5. Luồng 3 — Ca hỏng kỹ thuật và cơ chế thử lại

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
> ### ⚠️ Cái giá của chặn đầu hàng
> Luật này an toàn khi ca đứng đầu hỏng vì **hạ tầng** — lúc đó cả lô hỏng nên
> chặn không mất gì. Nhưng nếu nó hỏng vì **lý do của riêng nó**, cụ thể là
> `stage = file_error` (file chứng chỉ thật sự lỗi), thì thử lại bao nhiêu lần
> cũng vẫn lỗi và **nó chặn cả hàng đợi vô thời hạn**. Một nhân viên nộp nhầm
> file hỏng có thể làm cả phòng không được duyệt chứng chỉ.
>
>
> Nếu muốn `file_error` **không** chặn hàng (vì nó là lỗi của riêng một chứng
> chỉ, không phải lỗi hệ thống), đó là một thay đổi nhỏ — hỏi khi cần.

**Lưu ý về `BATCH_SIZE`.** Luật chặn đầu hàng chạy đúng nhất với `BATCH_SIZE=1`
(mặc định). Giá trị lớn hơn vẫn tải cả lô về trước rồi mới quét lần lượt và
dừng đúng chỗ, nhưng lô đã tải là chi phí đã bỏ ra cho những ca chưa tới lượt.

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

**Nhịp gửi: mail đầu ngay khi chạm ngưỡng, sau đó mỗi tiếng một mail nhắc lại**
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

"Loại sự cố" nhận diện bằng **tập `stage` đang hỏng**, không phải bằng danh
sách chứng chỉ. Danh sách đổi mỗi vòng (ca cũ xong, ca mới vào), nên lấy nó
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
python test_api.py 1      # API ① getCert — chỉ đọc, an toàn
python test_api.py 2      # API ② download — cần id lấy từ ①
```

**Không có test cho API ③** vì nó thay đổi trạng thái thật trên eLIS.

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
docker compose run --rm job python test_api.py 1
```

---

## 11. Cấu hình đầy đủ (`.env`)

Cấu hình đọc bằng `pydantic-settings` từ file `.env` **và** biến môi trường.
Biến môi trường thắng file. Toàn bộ định nghĩa nằm ở `src/config.py`.

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
| `BATCH_SIZE` | `1` | Số chứng chỉ xử lý trong **một** lô: tải file → scan → nộp kết quả. Lô nhỏ = mất ít công hơn khi eLIS lỗi giữa chừng; lô lớn = ít request hơn. **eLIS giới hạn 20 cặp mỗi request tải file**, giá trị lớn hơn bị ép về 20 |
| `RETRY_COUNT` | `3` | Số lần gọi lại **một request eLIS** khi gặp lỗi tạm thời (502, timeout). Khác hoàn toàn với `TECHNICAL_ALERT_AFTER` |
| `RETRY_DELAY_SECONDS` | `5` | Nghỉ giữa các lần gọi lại đó |
| `TIMEOUT_SECONDS` | `60` | Timeout mỗi request HTTP |

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
| `stage` | Tầng ra kết luận: `llm1`, `llm2`, `llm1_vs_llm2`, hoặc một trong các `TECHNICAL_STAGES` |
| `provider` | Nhà cung cấp chứng chỉ (Udemy, Coursera…) |
| `elis_sent_ok` | `1`=eLIS nhận, `0`=eLIS từ chối, `NULL`=chưa gửi |
| `elis_message` | Thông điệp eLIS trả về khi từ chối |

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
├── test_api.py               # Kiểm tra kết nối eLIS ① ②
├── send_report.py            # Dựng + gửi báo cáo email
├── scheduler.py              # Kiểm tra tới giờ gửi báo cáo chưa
├── report_layout.py          # Bộ dựng HTML báo cáo (DUY NHẤT)
│
├── src/
│   ├── config.py             # ★ Toàn bộ cấu hình (.env) + get_llm()
│   ├── client.py             # Gọi 3 API eLIS
│   ├── pipeline.py           # ★ Ba lần so cho MỘT chứng chỉ
│   ├── compare.py            # ★ Luật so khớp tên / mã / khóa học
│   ├── process_data.py       # normalize(), code_from_email(), date_in_range()
│   ├── llm_vision.py         # Prompt + gọi LLM1 (đọc ảnh)
│   ├── llm_text.py           # Prompt + gọi LLM2 (đọc text OCR) — giữ ĐỒNG BỘ với llm_vision
│   ├── ocr_azure.py          # Azure Document Intelligence
│   ├── file_utils.py         # Kiểm MIME thật + render PDF → ảnh
│   ├── schemas.py            # Verdict, InputInfo, ExtractedInfo, ProcessResult
│   ├── archive.py            # Lưu chứng chỉ vào kho
│   ├── alert.py              # ★ Email cảnh báo lỗi hệ thống (gộp + chặn trùng)
│   └── charts.py             # Biểu đồ cho báo cáo
│
├── database/
│   ├── database.py           # SQLite: ghi log, đếm số lần hỏng kỹ thuật
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
├── tests/                    # pytest
├── data/                     # Dữ liệu thật (gitignored)
├── cert_archive/             # Kho chứng chỉ (gitignored)
├── mooc_log.db               # Log SQLite (gitignored)
├── Dockerfile · docker-compose.yml · DOCKER.md
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
pytest              # toàn bộ
ruff check .        # lint
```

Test **không** gọi API thật — mọi hàm gọi API được truyền vào pipeline dưới dạng
tham số (dependency injection), nên test thay bằng hàm giả.

Vài test đáng chú ý:

- `test_diem_vao.py` — canh **mọi điểm vào** đặt `KMP_DUPLICATE_LIB_OK` **trước**
  import nặng. Chính test này đã phát hiện `match_images.py` còn thiếu.
- `test_prompt.py` — canh `llm_vision` và `llm_text` khai đúng ánh xạ
  trường ↔ ngôn ngữ, không chỉ kiểm tra "có chữ TIẾNG VIỆT trong prompt".
- `test_match_images.py` — canh việc **không** tách tên file theo `_` đầu tiên.
- `test_course_name.py` — canh cả ba đường khớp song ngữ.
- `test_retry.py::test_qua_nguong_KHONG_BAO_GIO_nop_rejected` — canh **luật
  HR**. Ai khôi phục nhánh bỏ cuộc cũ thì test này đỏ.
- `test_alert.py` — phần lớn canh việc **không gửi trùng**, vì gửi thiếu thì
  thấy ngay còn gửi trùng chỉ phát hiện khi đã spam mất người nhận.

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

---

## Phụ lục — Việc còn treo

| Việc | Tình trạng |
|---|---|
| Luật **tên đệm** (`"Anh Le"` vs `"Lê Hoàng Anh"`) | **Chờ HR.** Đo được: nới luật giảm từ chối oan 19 → 9, nhưng `"Nguyễn Tuấn"` khớp nhiều người. **Không sửa cho tới khi HR kết luận** |
| Đo lại độ chính xác sau khi sửa prompt + khớp song ngữ | Chưa chạy — cần gọi LLM thật trên cả bộ |
| **Thu hồi key FPT đã lộ trong lịch sử git** | `.env.example` đã sạch, nhưng **key vẫn phải cấp lại** — xem mục 15 |
| Điền `SMTP_USER` / `SMTP_PASSWORD` để cảnh báo gửi được | Không có SMTP thì `alert.py` chỉ ghi lỗi vào log, không ai nhận được thư |
| Tạo `.alert_state.json` trước khi `docker compose up` | Giống `mooc_log.db`: bind-mount file chưa tồn tại thì Docker tạo một **thư mục** trùng tên |
| Xóa 3 file thừa còn sót trên máy | `evaluation\compare_tiers.py`, `tests\test_compare_tiers.py`, `tests\test_tier2_only.py` — chưa xóa thì `pytest` lỗi thiếu import |
