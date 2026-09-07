"""Phân loại lỗi gọi LLM và thử lại (llm_error).

VÌ SAO TÁCH RA MODULE RIÊNG. llm_vision (LLM1 đọc ảnh) và llm_text (LLM2 đọc
text OCR) gọi CÙNG một model qua CÙNG một endpoint, nên gặp y hệt các lỗi.
Chép bảng phân loại làm hai bản là cách chắc chắn để hai bên lệch nhau sau lần
sửa đầu tiên — chuyện đã xảy ra thật với prompt của hai file đó.

VÌ SAO CẦN BẢNG NÀY. Trước đây mọi lỗi gọi Gemma đều ra đúng một câu
"Lỗi gọi Gemma: <exception thô>". Người vận hành mở email cảnh báo, đọc
"Error code: 402", rồi vẫn phải tự tra nghĩa. Trong khi Azure đã có bảng
tương đương từ lâu (ocr_azure._explain_error) — đây là chỗ bất đối xứng.
"""

from __future__ import annotations

import logging
import re
import time

logger = logging.getLogger(__name__)

TAG_NEEDS_HUMAN = "CẦN NGƯỜI XỬ LÝ"

# Mã lỗi TẠM THỜI — thử lại thì có cơ may thành công.
TRANSIENT_CODES = frozenset({408, 429, 500, 502, 503, 504})

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (2, 5)

# Dấu hiệu HẾT TIỀN / HẾT HẠN MỨC trong nội dung message.
#
# Phải dò theo chuỗi chứ không chỉ theo mã, vì hai lý do:
#   - 429 vừa là rate-limit vừa là insufficient_quota (xem docstring đầu file).
#   - Mỗi nhà cung cấp OpenAI-compatible dùng một mã khác nhau cho cùng một
#     chuyện: 402, 403, hoặc 429. Dò nội dung bắt được cả ba.
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

    openai/langchain-openai ném APIStatusError có sẵn .status_code. Nhưng
    không phải đường nào cũng đi qua đó — có bản chỉ ném RuntimeError với
    chuỗi "Error code: 402 - {...}". Dò cả hai để không phụ thuộc vào chi
    tiết nội bộ của thư viện, thứ đã đổi vài lần giữa các phiên bản.
    """
    code = getattr(e, "status_code", None) or getattr(e, "http_status", None)
    if isinstance(code, int):
        return code
    match = re.search(r"[Ee]rror code:\s*(\d{3})", str(e))
    return int(match.group(1)) if match else None


def classify(e: Exception) -> tuple[bool, str]:
    """Trả về (co_the_thu_lai, giai_thich_bang_tieng_Viet).

    co_the_thu_lai=False nghĩa là thử lại chắc chắn ra đúng kết quả đó — hoặc
    vì cấu hình sai, hoặc vì hết tiền. Những ca này được gắn TAG_CAN_NGUOI.
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
        # Tới được đây nghĩa là 429 mà KHÔNG có dấu hiệu hết tiền -> đúng nghĩa
        # rate-limit, tạm thời thật.
        return True, "Bị giới hạn tốc độ gọi model. Tạm thời."

    if code == 408:
        return True, "Model xử lý quá lâu rồi bỏ cuộc. Tạm thời."

    if code in TRANSIENT_CODES:
        return True, f"Lỗi phía FPT Cloud ({code}). Tạm thời."

    if code is None:
        # Không rút được mã: rớt mạng, DNS hỏng, timeout ở tầng socket. Đều là
        # lỗi tạm thời theo nghĩa "thử lại có cơ may".
        return True, "Không gọi được tới FPT Cloud (mạng hoặc timeout). Tạm thời."

    return False, f"Lỗi không rõ từ FPT Cloud (mã {code})."


# Dấu ngăn giữa phần GIẢI THÍCH và phần message thô của SDK.
#
# KHÔNG dùng " — ": chính câu giải thích cũng chứa dấu đó ("...KHÔNG tự khỏi —
# phải nạp thêm hạn mức"), nên alert._viec_phai_lam() cắt nhầm ngay chỗ đầu
# tiên và mất đúng vế nói người phải làm gì. Đây là lỗi đã xảy ra thật, thấy
# được khi dựng thử email.
SEPARATOR = " | Lỗi gốc: "


def describe(e: Exception, attempts: int = 1) -> str:
    """Câu mô tả đầy đủ để ghi log / đưa vào email."""
    _, explanation = classify(e)
    code = _status_code(e)
    prefix = f"[FPT {code}] " if code else "[FPT] "
    attempt = f" (đã thử {attempts} lần)" if attempts > 1 else ""
    # Message của SDK hay xuống dòng nhiều lần cho cùng một nội dung; ép về
    # một dòng để log và bảng email đọc được.
    raw = " ".join(str(e).split())[:300]
    return f"{prefix}{explanation}{SEPARATOR}{raw}{attempt}"


def call_with_retry(func, label: str = "LLM"):
    """Gọi `ham()`, tự thử lại khi gặp lỗi TẠM THỜI.

    VÌ SAO CẦN. Trước đây llm_vision và llm_text không thử lại lần nào, trong
    khi ocr_azure thử 3 lần. Nghĩa là một cú 429 rate-limit thoáng qua ở tầng
    LLM bị đối xử y hệt hết tiền: chứng chỉ thành hỏng kỹ thuật, chặn cả hàng
    đợi, và phải đợi hết giãn cách 2 phút mới được thử lại. Ở tầng Azure thì
    đúng cú đó tự khỏi sau 2 giây.

    KHÔNG thử lại lỗi vĩnh viễn (hết tiền, sai key, sai model): thử lại chỉ
    làm chứng chỉ kẹt lâu thêm 7 giây, kết quả không đổi.
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
