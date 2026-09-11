"""Phân loại lỗi gọi LLM và thử lại (llm_error).

VÌ SAO TÁCH RIÊNG: llm_vision (LLM1) và llm_text (LLM2) gọi cùng một model qua
cùng một endpoint nên gặp y hệt các lỗi. Chép bảng phân loại thành hai bản thì
hai bên lệch nhau ngay lần sửa đầu.

BA CÂU HỎI BẢNG NÀY TRẢ LỜI, theo thứ tự quan trọng:

  1. CÓ TỰ KHỎI KHÔNG? Hết tiền cần người đi nạp, rate-limit chỉ cần đợi. Nói
     sai chỗ này là tai hại nhất: email hẹn "sự cố khắc phục xong thì tự xử
     lý" trong khi không ai đang khắc phục gì.
  2. CÓ NÊN THỬ LẠI NGAY KHÔNG? Lỗi tạm thời thì có, sai key thì vô ích.
  3. NGƯỜI ĐỌC PHẢI LÀM GÌ? "402" không nói gì, "hết tiền, phải nạp" thì có.

CÁI BẪY CỦA MÃ 429: endpoint kiểu OpenAI dùng 429 cho cả rate-limit (tạm thời)
lẫn insufficient_quota (hết tiền). Phân loại 429 chỉ theo mã số là sai một nửa
số ca — phải đọc cả nội dung message.
"""

from __future__ import annotations

import logging
import re
import time

logger = logging.getLogger(__name__)

# Tiền tố gắn vào thông báo của lỗi KHÔNG TỰ KHỎI. alert.py đọc mốc này để đổi
# giọng email. Dùng chuỗi mốc thay vì truyền thêm cờ: ProcessResult chỉ mang
# `reason` là chuỗi, thêm trường mới phải sửa cả schema, database, báo cáo.
TAG_NEEDS_HUMAN = "CẦN NGƯỜI XỬ LÝ"

# Mã lỗi TẠM THỜI — thử lại thì có cơ may thành công. Cố ý giống hệt
# ocr_azure.MA_LOI_TAM_THOI: cùng loại vấn đề thì hai tầng phải cư xử như nhau.
TRANSIENT_CODES = frozenset({408, 429, 500, 502, 503, 504})

# Số lần gọi tối đa cho MỘT lần trích xuất, và giãn cách giữa các lần. Ngắn
# thôi: nằm trong vòng xử lý chứng chỉ, kéo dài thì cả hàng đợi đứng.
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (2, 5)

# Dấu hiệu HẾT TIỀN / HẾT HẠN MỨC trong nội dung message. Phải dò theo chuỗi
# chứ không chỉ theo mã: 429 vừa là rate-limit vừa là insufficient_quota, và
# mỗi nhà cung cấp dùng mã khác nhau (402, 403 hoặc 429) cho cùng chuyện đó.
OUT_OF_CREDIT_MARKERS = (
    "insufficient_quota", "insufficient quota", "insufficient balance",
    "insufficient_user_quota", "exceeded your current quota",
    "quota exceeded", "out of credit", "no credit", "balance",
    "billing", "payment required", "hết hạn mức", "hết tiền",
)

CONTEXT_LIMIT_MARKERS = (
    "context_length_exceeded", "maximum context length",
    "context length", "too many tokens",
)


def _status_code(e: Exception) -> int | None:
    """Rút mã HTTP ra khỏi exception của SDK.

    openai/langchain-openai ném APIStatusError có sẵn .status_code, nhưng có
    bản chỉ ném RuntimeError với chuỗi "Error code: 402 - {...}". Dò cả hai vì
    chi tiết nội bộ này đã đổi vài lần giữa các phiên bản.
    """
    code = getattr(e, "status_code", None) or getattr(e, "http_status", None)
    if isinstance(code, int):
        return code
    match = re.search(r"[Ee]rror code:\s*(\d{3})", str(e))
    return int(match.group(1)) if match else None


def classify(e: Exception) -> tuple[bool, str]:
    """Trả về (co_the_thu_lai, giai_thich_bang_tieng_Viet).

    co_the_thu_lai=False: thử lại chắc chắn ra đúng kết quả đó (sai cấu hình
    hoặc hết tiền). Những ca này được gắn TAG_NEEDS_HUMAN.
    """
    code = _status_code(e)
    text = str(e).lower()

    out_of_credit = any(d in text for d in OUT_OF_CREDIT_MARKERS) or code == 402
    too_long = any(d in text for d in CONTEXT_LIMIT_MARKERS)

    if out_of_credit:
        return False, (
            f"{TAG_NEEDS_HUMAN}: HẾT TIỀN hoặc hết hạn mức FPT AI Marketplace. "
            f"Thử lại sẽ KHÔNG tự khỏi — phải nạp thêm hạn mức delay tài khoản "
            f"thì hệ thống mới chạy lại được.")

    if code == 401:
        return False, (
            f"{TAG_NEEDS_HUMAN}: Sai FPT_API_KEY (hoặc key đã bị thu hồi). "
            f"Sửa .env rồi khởi động lại job.")

    if code == 403:
        return False, (
            f"{TAG_NEEDS_HUMAN}: Key không có quyền gọi model này. Kiểm tra "
            f"FPT_MODEL và quyền của key trên FPT AI Marketplace.")

    if code == 404:
        return False, (
            f"{TAG_NEEDS_HUMAN}: Không tìm thấy model. Sai FPT_MODEL "
            f"(chú ý chữ B hoa trong 'gemma-4-31B-it') hoặc sai FPT_BASE_URL.")

    if too_long:
        return False, (
            f"{TAG_NEEDS_HUMAN}: Nội dung gửi lên vượt giới hạn ngữ cảnh của "
            f"model. Chứng chỉ này quá nhiều chữ — cần giảm số trang gửi lên "
            f"hoặc đổi model, thử lại không giải quyết được.")

    if code == 429:
        # 429 mà KHÔNG có dấu hiệu hết tiền -> rate-limit thật, tạm thời.
        return True, "Bị giới hạn tốc độ gọi model. Tạm thời."

    if code == 408:
        return True, "Model xử lý quá lâu rồi bỏ cuộc. Tạm thời."

    if code in TRANSIENT_CODES:
        return True, f"Lỗi phía FPT Cloud ({code}). Tạm thời."

    if code is None:
        # Không rút được mã: rớt mạng, DNS hỏng, timeout socket — thử lại có cơ may.
        return True, "Không gọi được tới FPT Cloud (mạng hoặc timeout). Tạm thời."

    return False, f"Lỗi không rõ từ FPT Cloud (mã {code})."


# Dấu ngăn giữa phần GIẢI THÍCH và message thô của SDK. KHÔNG dùng " — ":
# chính câu giải thích cũng chứa dấu đó, nên alert._viec_phai_lam() cắt nhầm
# ngay chỗ đầu tiên và mất đúng vế nói người phải làm gì.
SEPARATOR = " | Lỗi gốc: "


def describe(e: Exception, attempts: int = 1) -> str:
    """Câu mô tả đầy đủ để ghi log / đưa vào email."""
    _, explanation = classify(e)
    code = _status_code(e)
    prefix = f"[FPT {code}] " if code else "[FPT] "
    attempt = f" (đã thử {attempts} lần)" if attempts > 1 else ""
    # Message của SDK hay xuống dòng nhiều lần; ép về một dòng cho log dễ đọc.
    raw = " ".join(str(e).split())[:300]
    return f"{prefix}{explanation}{SEPARATOR}{raw}{attempt}"


def call_with_retry(func, label: str = "LLM"):
    """Gọi `func()`, tự thử lại khi gặp lỗi TẠM THỜI.

    Một cú 429 rate-limit thoáng qua mà không thử lại thì chứng chỉ thành hỏng
    kỹ thuật và chặn cả hàng đợi.

    KHÔNG thử lại lỗi vĩnh viễn (hết tiền, sai key, sai model): kết quả không
    đổi, chỉ làm chứng chỉ kẹt lâu thêm 7 giây.
    """
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return func()
        except Exception as e:
            last_error = e
            retryable, _ = classify(e)
            if not retryable or attempt == MAX_ATTEMPTS:
                raise
            delay = BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)]
            logger.warning("%s lỗi tạm thời (%s), thử lại lần %d/%d sau %ds.",
                           label, describe(e), attempt + 1, MAX_ATTEMPTS, delay)
            time.sleep(delay)
    raise last_error
