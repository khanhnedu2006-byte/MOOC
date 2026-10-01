"""Lý do từ chối về NGÀY phải tách: không có ngày / không đọc được / ngoài khoảng.

Ca thật: AI trả issue_date=null mà lý do ghi "Ngày không hợp lệ" — người học
tưởng mình nộp sai ngày, trong khi chứng chỉ không in ngày nào.

Chạy: pytest tests/test_date_reason.py -v
"""

import pipeline
import process_data
import pytest
from schemas import ExtractedInfo, InputInfo, Verdict

GIVEN = InputInfo(employee_name="Bui Duc Hoa", course_name="ISO 27001",
                  employee_code="hoabd3")
OK = dict(recipient_name="Bui Duc Hoa", certificate_name="ISO 27001")


@pytest.fixture(autouse=True)
def khoang_ngay(monkeypatch):
    monkeypatch.setattr(pipeline.settings, "valid_from", "2026-01-01")
    monkeypatch.setattr(pipeline.settings, "valid_to", "2026-10-15")
    monkeypatch.setattr(pipeline.settings, "course_match_mode", "loose")


# ===== process_data.date_status =====

@pytest.mark.parametrize("value, status", [
    (None, process_data.DATE_MISSING),
    ("", process_data.DATE_MISSING),
    ("   ", process_data.DATE_MISSING),
    ("không rõ", process_data.DATE_UNREADABLE),
    ("2026", process_data.DATE_UNREADABLE),          # chỉ có năm
    ("15/03/2026", process_data.DATE_OK),
    ("Sept. 29, 2026", process_data.DATE_OK),
    ("01/01/1999", process_data.DATE_OUT_OF_RANGE),
    ("Sep. 29, 2020", process_data.DATE_OUT_OF_RANGE),
])
def test_date_status(value, status):
    assert process_data.date_status(value, "2026-01-01", "2026-10-15") == status


def test_date_in_range_van_nhu_cu():
    assert process_data.date_in_range("15/03/2026", "2026-01-01", "2026-10-15")
    assert not process_data.date_in_range(None, "2026-01-01", "2026-10-15")
    assert not process_data.date_in_range("01/01/1999", "2026-01-01", "2026-10-15")


# ===== Câu lý do =====

def _reason(issue_date, other_date="__same__"):
    doc = ExtractedInfo(**OK, issue_date=issue_date)
    other = doc if other_date == "__same__" else ExtractedInfo(**OK, issue_date=other_date)
    return pipeline._mismatch_reason(doc, GIVEN, other)


def test_null_la_KHONG_CO_NGAY():
    r = _reason(None)
    assert r == "Không tìm thấy ngày hoàn thành chứng chỉ"
    assert "Ngày không hợp lệ" not in r


def test_chu_la_la_KHONG_DOC_DUOC():
    assert _reason("không rõ") == "Ngày không hợp lệ"


def test_ngoai_khoang_la_NGAY_KHONG_HOP_LE():
    assert _reason("01/01/1999") == "Ngày không hợp lệ"


def test_ban_chinh_null_ban_kia_co_ngay_thi_noi_theo_ban_kia():
    """Một máy vẫn đọc ra ngày thì 'không có ngày' là sai sự thật."""
    r = _reason(None, other_date="01/01/1999")
    assert r == "Ngày không hợp lệ"


def test_van_ghep_voi_loi_khac():
    doc = ExtractedInfo(recipient_name="Bui Duc Hoa", certificate_name="Khóa khác",
                        issue_date=None)
    assert pipeline._mismatch_reason(doc, GIVEN, doc) == (
        "Tên khóa học không khớp; Không tìm thấy ngày hoàn thành chứng chỉ")


# ===== Qua pipeline =====

def _run(llm1, llm2):
    return pipeline.process(images=[b"x"], given=GIVEN,
                            extract_from_image=lambda i: llm1,
                            ocr_images=lambda c, i: "ocr",
                            extract_from_text=lambda t: llm2, azure_client=None)


def test_pipeline_ca_hai_may_deu_null():
    doc = ExtractedInfo(**OK, issue_date=None)
    r = _run(doc, doc)
    assert r.verdict == Verdict.REJECTED
    assert r.reason == "Không tìm thấy ngày hoàn thành chứng chỉ"


def test_pipeline_doc_lai_ngay_llm2_null_thi_noi_theo_llm1():
    """_recheck_date: LLM1 ra ngày ngoài khoảng, LLM2 không đọc được ngày."""
    r = _run(ExtractedInfo(**OK, issue_date="Sep. 29, 2020"),
             ExtractedInfo(**OK, issue_date=None))
    assert r.verdict == Verdict.REJECTED
    assert r.reason == "Ngày không hợp lệ"


# ===== Ngày tiếng Việt và ngày chỉ có tháng + năm =====

@pytest.mark.parametrize("value, status", [
    # Ca thật: Coursera tiếng Việt "Đã hoàn thành tháng 9 2026"
    ("tháng 9 2026", process_data.DATE_OK),
    ("Tháng 9/2026", process_data.DATE_OK),
    ("thang 9 2026", process_data.DATE_OK),
    ("Sep 2026", process_data.DATE_OK),
    ("09/2026", process_data.DATE_OK),
    # Bản dịch máy của "Oct 8, 2026"
    ("tháng 10 8, 2026", process_data.DATE_OK),
    ("ngày 8 tháng 10 năm 2026", process_data.DATE_OK),
    ("ngày 20 tháng 10 năm 2026", process_data.DATE_OUT_OF_RANGE),
    # Tháng chạm biên (hạn 15/10): không biết ngày thật -> không nhận
    ("tháng 10 2026", process_data.DATE_OUT_OF_RANGE),
    ("tháng 12 2025", process_data.DATE_OUT_OF_RANGE),
    ("tháng 13 2026", process_data.DATE_UNREADABLE),
])
def test_ngay_tieng_viet_va_thang_nam(value, status):
    assert process_data.date_status(value, "2026-01-01", "2026-10-15") == status
