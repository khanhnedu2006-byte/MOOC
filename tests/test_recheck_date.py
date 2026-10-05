"""Test đọc lại ngày bằng Azure + LLM2 khi LLM1 khớp tên + khóa nhưng trượt ngày.

Ca thật: Gemma đọc "Sept. 29, 2026" thành "Sep. 29, 2020" -> chứng chỉ Claude
101 hợp lệ bị từ chối oan ở LLM1 mà không có bản đọc thứ hai.

Chạy: pytest tests/test_recheck_date.py -v
"""

import pipeline
import pytest
from schemas import ExtractedInfo, InputInfo, Verdict

GIVEN = InputInfo(employee_name="Âu Quốc Hòa", course_name="Claude 101",
                  employee_code="hoaaq")

LLM1_SAI_NAM = ExtractedInfo(recipient_name="Quoc Hoa Au",
                             certificate_name="Claude 101",
                             issue_date="Sep. 29, 2020")


@pytest.fixture(autouse=True)
def khoang_ngay(monkeypatch):
    monkeypatch.setattr(pipeline.settings, "valid_from", "2026-01-01")
    monkeypatch.setattr(pipeline.settings, "valid_to", "2026-10-15")
    monkeypatch.setattr(pipeline.settings, "course_match_mode", "loose")


def _run(llm1, llm2=None, ocr_error=None):
    calls = {"ocr": 0}

    def ocr(client, images):
        calls["ocr"] += 1
        if ocr_error:
            raise ocr_error
        return "ocr text"

    result = pipeline.process(
        images=[b"img"], given=GIVEN,
        extract_from_image=lambda img: llm1,
        ocr_images=ocr,
        extract_from_text=lambda text: llm2,
        ocr_client=None)
    return result, calls


def test_llm1_doc_sai_nam_llm2_doc_dung_thi_APPROVED():
    llm2 = ExtractedInfo(recipient_name="Quoc Hoa Au", certificate_name="Claude 101",
                         issue_date="Sept. 29, 2026")
    result, calls = _run(LLM1_SAI_NAM, llm2)
    assert calls["ocr"] == 1
    assert result.verdict == Verdict.APPROVED
    assert result.stage == "llm2"
    # Log giữ tên/khóa của LLM1, ngày của LLM2
    assert result.extracted.recipient_name == "Quoc Hoa Au"
    assert result.extracted.issue_date == "Sept. 29, 2026"


def test_llm2_doc_ten_kem_van_APPROVED_vi_chi_hoi_ngay():
    llm2 = ExtractedInfo(recipient_name="Q. H. Au", certificate_name="Claude",
                         issue_date="2026-09-29")
    result, _ = _run(LLM1_SAI_NAM, llm2)
    assert result.verdict == Verdict.APPROVED
    assert result.extracted.recipient_name == "Quoc Hoa Au"


def test_ca_hai_ban_doc_deu_ngoai_khoang_thi_REJECTED():
    llm2 = LLM1_SAI_NAM.model_copy(update={"issue_date": "29/09/2020"})
    result, calls = _run(LLM1_SAI_NAM, llm2)
    assert calls["ocr"] == 1
    assert result.verdict == Verdict.REJECTED
    assert result.reason.startswith("Ngày không hợp lệ")
    assert result.stage == "llm2"


def test_llm2_khong_doc_duoc_ngay_thi_REJECTED():
    llm2 = LLM1_SAI_NAM.model_copy(update={"issue_date": None})
    result, _ = _run(LLM1_SAI_NAM, llm2)
    assert result.verdict == Verdict.REJECTED


def test_azure_loi_thi_la_hong_ky_thuat_khong_tu_choi():
    result, _ = _run(LLM1_SAI_NAM, ocr_error=RuntimeError("Azure 503"))
    assert result.stage == "stage2_error"
    assert result.stage in pipeline_technical_stages()


def test_llm1_khop_du_ca_ba_thi_KHONG_goi_azure():
    ok = LLM1_SAI_NAM.model_copy(update={"issue_date": "Sept. 29, 2026"})
    result, calls = _run(ok)
    assert calls["ocr"] == 0
    assert result.verdict == Verdict.APPROVED
    assert result.stage == "llm1"


def pipeline_technical_stages():
    from database import database
    return database.TECHNICAL_STAGES
