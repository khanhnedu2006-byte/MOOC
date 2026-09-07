"""Test bảng phân loại lỗi LLM (test_llm_error).

Bảng này tồn tại để trả lời MỘT câu hỏi mà mã HTTP một mình không trả lời
được: sự cố này có TỰ KHỎI không? Hết tiền và rate-limit làm chứng chỉ kẹt y
hệt nhau, nhưng một cái cần người đi nạp tiền còn cái kia chỉ cần đợi hai giây.

Phần lớn test ở đây canh mã 429, vì đó là chỗ duy nhất mã số nói dối: các
endpoint kiểu OpenAI dùng 429 cho CẢ rate-limit (tạm thời) lẫn
insufficient_quota (hết tiền). Phân loại 429 chỉ theo mã là sai một nửa số ca.
"""

import logging
import pathlib
import sys

import pytest

GOC = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GOC))
sys.path.insert(0, str(GOC / "src"))

import llm_error                          # noqa: E402

logging.disable(logging.CRITICAL)


class LoiGia(Exception):
    """Giả exception của SDK: có .status_code như openai.APIStatusError."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


# ===== Hết tiền: KHÔNG tự khỏi =====

@pytest.mark.parametrize("message, code", [
    ("Error code: 402 - {'error': {'message': 'Insufficient balance'}}", None),
    ("You exceeded your current quota, please check your billing", 429),
    ("insufficient_quota", 429),
    ("Payment Required", 402),
    ("Tài khoản đã hết hạn mức", None),
])
def test_het_tien_thi_KHONG_duoc_coi_la_tam_thoi(message, code):
    """Thử lại khi hết tiền là vô ích — phải nói cho người biết đi nạp."""
    thu_lai, giai_thich = llm_error.classify(LoiGia(message, code))
    assert thu_lai is False, f"{message!r} bị coi là lỗi tạm thời"
    assert llm_error.TAG_NEEDS_HUMAN in giai_thich
    assert "HẾT TIỀN" in giai_thich


def test_429_rate_limit_VAN_la_tam_thoi():
    """Cái bẫy chính: 429 có HAI nghĩa trái ngược.

    429 không kèm dấu hiệu quota là rate-limit thật — đợi vài giây là khỏi.
    Gộp chung với hết tiền thì hệ thống bỏ thử lại một lỗi vốn tự khỏi, và
    email báo động nhầm là "cần người nạp tiền".
    """
    thu_lai, giai_thich = llm_error.classify(
        LoiGia("Rate limit reached for requests", 429))
    assert thu_lai is True
    assert llm_error.TAG_NEEDS_HUMAN not in giai_thich


# ===== Sai cấu hình: KHÔNG tự khỏi =====

@pytest.mark.parametrize("code, tu_khoa", [
    (401, "FPT_API_KEY"),
    (403, "quyền"),
    (404, "FPT_MODEL"),
])
def test_sai_cau_hinh_thi_bao_ro_phai_sua_gi(code, tu_khoa):
    thu_lai, giai_thich = llm_error.classify(LoiGia("Lỗi", code))
    assert thu_lai is False
    assert llm_error.TAG_NEEDS_HUMAN in giai_thich
    assert tu_khoa in giai_thich


def test_qua_dai_ngu_canh_thi_KHONG_tu_khoi():
    thu_lai, giai_thich = llm_error.classify(
        LoiGia("This model's maximum context length is 8192 tokens", 400))
    assert thu_lai is False
    assert llm_error.TAG_NEEDS_HUMAN in giai_thich


# ===== Lỗi tạm thời =====

@pytest.mark.parametrize("code", [408, 500, 502, 503, 504])
def test_loi_dich_vu_la_tam_thoi(code):
    thu_lai, _ = llm_error.classify(LoiGia("Server error", code))
    assert thu_lai is True


def test_khong_ro_ma_thi_van_cho_thu_lai():
    """Rớt mạng / DNS hỏng / timeout socket không có mã HTTP nào.

    Mặc định phải là 'cho thử lại': đoán sai theo hướng thử thêm vài giây thì
    tốn chút thời gian, còn đoán sai theo hướng bỏ cuộc thì chứng chỉ kẹt
    thêm hai phút vì một cú chập mạng.
    """
    thu_lai, giai_thich = llm_error.classify(LoiGia("Connection reset by peer"))
    assert thu_lai is True
    assert llm_error.TAG_NEEDS_HUMAN not in giai_thich


def test_rut_ma_tu_CHUOI_khi_khong_co_status_code():
    """Không phải đường nào cũng ném exception có .status_code.

    Có bản chỉ ném RuntimeError với chuỗi "Error code: 402 - {...}". Phụ thuộc
    vào .status_code một mình là phụ thuộc vào chi tiết nội bộ của thư viện,
    thứ đã đổi vài lần giữa các phiên bản.
    """
    assert llm_error._status_code(LoiGia("Error code: 503 - Service Unavailable")) == 503


# ===== Thử lại =====

def test_loi_tam_thoi_duoc_thu_lai(monkeypatch):
    """Trước đây llm_vision/llm_text không thử lại lần nào, trong khi
    ocr_azure thử 3 lần — một cú 429 thoáng qua ở tầng LLM làm chứng chỉ kẹt
    hai phút, còn đúng cú đó ở tầng Azure tự khỏi sau 2 giây."""
    monkeypatch.setattr(llm_error.time, "sleep", lambda s: None)
    dem = {"n": 0}

    def ham():
        dem["n"] += 1
        if dem["n"] < 3:
            raise LoiGia("Rate limit", 429)
        return "xong"

    assert llm_error.call_with_retry(ham) == "xong"
    assert dem["n"] == 3


def test_loi_vinh_vien_KHONG_duoc_thu_lai(monkeypatch):
    """Hết tiền thì thử lại chỉ làm chứng chỉ kẹt thêm 7 giây, kết quả không
    đổi — mà nó đang CHẶN cả hàng đợi phía sau."""
    monkeypatch.setattr(llm_error.time, "sleep", lambda s: None)
    dem = {"n": 0}

    def ham():
        dem["n"] += 1
        raise LoiGia("Insufficient balance", 402)

    with pytest.raises(LoiGia):
        llm_error.call_with_retry(ham)
    assert dem["n"] == 1, f"gọi {dem['n']} lần wait lỗi vĩnh viễn, mong đúng 1"


def test_het_luot_thu_thi_nem_loi_that(monkeypatch):
    """Ném exception GỐC, không bọc lại: mã lỗi và message gốc là thứ dán vào
    ticket cho nhà cung cấp."""
    monkeypatch.setattr(llm_error.time, "sleep", lambda s: None)

    def ham():
        raise LoiGia("Bad gateway", 502)

    with pytest.raises(LoiGia) as ex:
        llm_error.call_with_retry(ham)
    assert ex.value.status_code == 502


def test_mo_ta_ngan_cach_KHONG_trung_voi_dau_trong_giai_thich():
    """Dấu ngăn phải là chuỗi không xuất hiện trong câu giải thích.

    Bản trước ngăn bằng " — ", mà chính câu giải thích cũng chứa dấu đó
    ("...KHÔNG tự khỏi — phải nạp thêm hạn mức"). alert._viec_phai_lam() cắt
    ở chỗ đầu tiên nên mất đúng vế nói người phải đi làm gì — vế quan trọng
    nhất của cả bức thư.
    """
    text = llm_error.describe(LoiGia("Insufficient balance", 402))
    phan_giai_thich = text.split(llm_error.SEPARATOR)[0]
    assert "phải nạp thêm hạn mức" in phan_giai_thich


def test_mo_ta_mang_theo_ma_va_message_goc():
    text = llm_error.describe(LoiGia("Insufficient balance", 402))
    assert "[FPT 402]" in text
    assert "HẾT TIỀN" in text
    assert "Insufficient balance" in text, "mất message gốc, không tra được"
