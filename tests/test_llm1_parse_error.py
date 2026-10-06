"""LLM1 trả về JSON hỏng / sai schema -> chuyển sang OCR + LLM2, KHÔNG coi là
hỏng kỹ thuật.

Trước đây mọi lỗi của LLM1 đều thành llm1_error: WAITING, chặn đầu hàng, thử
lại sau giãn cách — dù chỉ là một lần Gemma trả lời lạc định dạng.

Chạy: pytest tests/test_llm1_parse_error.py -v
"""

from types import SimpleNamespace

import llm_vision
import pipeline
import pytest
from database import database
from schemas import ExtractedInfo, ExtractionParseError, InputInfo, Verdict

GIVEN = InputInfo(employee_name="Bùi Đức Hòa", course_name="ISO 27001",
                  employee_code="hoabd3")
KHOP = ExtractedInfo(recipient_name="Bui Duc Hoa", certificate_name="ISO 27001",
                     issue_date="15/03/2026")


@pytest.fixture(autouse=True)
def khoang_ngay(monkeypatch):
    monkeypatch.setattr(pipeline.settings, "valid_from", "2026-01-01")
    monkeypatch.setattr(pipeline.settings, "valid_to", "2026-12-31")
    monkeypatch.setattr(pipeline.settings, "course_match_mode", "loose")


def _raise(exc):
    def f(*a):
        raise exc
    return f


def _run(llm1_error, llm2=None, ocr_error=None):
    calls = {"ocr": 0}

    def ocr(client, images):
        calls["ocr"] += 1
        if ocr_error:
            raise ocr_error
        return "ocr text"

    r = pipeline.process(images=[b"x"], given=GIVEN,
                         extract_from_image=_raise(llm1_error),
                         ocr_images=ocr, extract_from_text=lambda t: llm2,
                         ocr_client=None)
    return r, calls


# ===== llm_vision gắn đúng loại lỗi =====

class _FakeLLM:
    def __init__(self, content):
        self.content = content

    def invoke(self, messages):
        return SimpleNamespace(content=self.content)


def test_json_hong_la_parse_error():
    with pytest.raises(ExtractionParseError):
        llm_vision.extract_from_image(b"\x89PNG\r\n\x1a\nxx", llm=_FakeLLM("không phải json"))


def test_sai_schema_la_parse_error():
    with pytest.raises(ExtractionParseError):
        llm_vision.extract_from_image(b"\x89PNG\r\n\x1a\nxx",
                                      llm=_FakeLLM('{"recipient_name": 123}'))


def test_loi_goi_api_KHONG_phai_parse_error():
    class Boom:
        def invoke(self, m):
            raise RuntimeError("401 sai key")
    with pytest.raises(llm_vision.LlmVisionError) as e:
        llm_vision.extract_from_image(b"\x89PNG\r\n\x1a\nxx", llm=Boom())
    assert not isinstance(e.value, ExtractionParseError)


# ===== pipeline =====

def test_json_hong_chuyen_sang_ocr_llm2_va_APPROVED():
    r, calls = _run(llm_vision.LlmVisionParseError("JSON hỏng"), KHOP)
    assert calls["ocr"] == 1
    assert r.verdict == Verdict.APPROVED
    assert r.stage == "llm2"
    assert "LLM1 trả về không đọc được" in r.reason


def test_json_hong_llm2_sai_ten_thi_REJECTED_nghiep_vu():
    sai = KHOP.model_copy(update={"recipient_name": "Nguyen Van B"})
    r, _ = _run(llm_vision.LlmVisionParseError("JSON hỏng"), sai)
    assert r.verdict == Verdict.REJECTED
    assert r.stage == "llm2"
    assert r.stage not in database.TECHNICAL_STAGES
    assert r.reason == "Tên không khớp"


def test_json_hong_llm2_thieu_ten_dem_thi_BO_QUA():
    thieu = KHOP.model_copy(update={"recipient_name": "Hoa Bui"})
    r, _ = _run(llm_vision.LlmVisionParseError("JSON hỏng"), thieu)
    assert r.verdict == Verdict.WAITING
    assert r.stage == database.SKIP_STAGE


def test_json_hong_ma_ocr_cung_hong_thi_hong_ky_thuat():
    r, _ = _run(llm_vision.LlmVisionParseError("JSON hỏng"),
                ocr_error=RuntimeError("OCR 503"))
    assert r.stage == "stage2_error"
    assert r.stage in database.TECHNICAL_STAGES


def test_loi_goi_api_LLM1_VAN_la_llm1_error():
    """Sai key / hết tiền / mạng: OCR + LLM2 không cứu được, giữ như cũ."""
    r, calls = _run(llm_vision.LlmVisionError("401 sai key"), KHOP)
    assert calls["ocr"] == 0
    assert r.stage == "llm1_error"
