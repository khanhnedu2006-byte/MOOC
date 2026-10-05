"""Test bước lọc ảnh DANH SÁCH nhiều khóa học (test_multi_course).

Ca thật: người học chụp trang hồ sơ "Registrations" (Anthropic/Skilljar) hoặc
"Enrollments" (Udacity) liệt kê 4-6 khóa, rồi nộp CÙNG một ảnh cho từng khóa.
Ảnh đó không phải chứng chỉ của riêng khóa nào -> BỎ QUA trước LLM1, giữ
WAITING cho người duyệt.

Bộ lọc là phụ: chỉ bỏ qua khi CẢ HAI tín hiệu cùng có (page_type
"course_list" VÀ từ 2 khóa), lỗi thì đi tiếp như chưa có bộ lọc.
"""

import json
import logging
import pathlib
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

GOC = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GOC))
sys.path.insert(0, str(GOC / "src"))

import llm_vision                      # noqa: E402
import pipeline                        # noqa: E402
import run                             # noqa: E402
from database import database          # noqa: E402
from schemas import (                  # noqa: E402
    ExtractedInfo, ImageKind, InputInfo, Verdict,
)

logging.disable(logging.CRITICAL)

DANH_SACH_ANTHROPIC = ImageKind(page_type="course_list", course_titles=[
    "Building with the Claude API", "Claude 101", "Claude Code 101",
    "Claude Platform 101", "Claude with Amazon Bedrock"])


def _given():
    return InputInfo(employee_name="Nguyễn Bá Anh", course_name="Claude 101",
                     employee_code="anhnb2")


def _khop():
    return ExtractedInfo(recipient_name="Nguyễn Bá Anh", certificate_name="Claude 101",
                         issue_date="01/10/2026")


def _process(detect, llm1=None):
    goi = {"llm1": 0, "ocr": 0}

    def llm1_fn(img):
        goi["llm1"] += 1
        return llm1 or _khop()

    def ocr_fn(cli, imgs):
        goi["ocr"] += 1
        return "text"

    result = pipeline.process(
        images=[b"x"], given=_given(),
        extract_from_image=llm1_fn, ocr_images=ocr_fn,
        extract_from_text=lambda t: _khop(), azure_client=None,
        detect_course_list=detect)
    return result, goi


# ===== pipeline =====

def test_anh_danh_sach_nhieu_khoa_thi_BO_QUA_va_KHONG_goi_llm1_azure():
    result, goi = _process(lambda img: DANH_SACH_ANTHROPIC)
    assert result.verdict == Verdict.WAITING
    assert result.stage == pipeline.SKIP_MULTI_COURSE_STAGE
    assert "5 khóa học" in result.reason
    assert goi == {"llm1": 0, "ocr": 0}


def test_ly_do_cat_bot_khi_qua_nhieu_khoa():
    many = ImageKind(page_type="course_list",
                     course_titles=[f"Khóa {i}" for i in range(8)])
    result, _ = _process(lambda img: many)
    assert "8 khóa học" in result.reason
    assert "Khóa 5" not in result.reason and "..." in result.reason


def test_ten_khoa_TRUNG_LAP_khong_tinh_hai_lan():
    """Model chép trùng một tên khóa -> vẫn chỉ là MỘT khóa, không bỏ qua."""
    kind = ImageKind(page_type="course_list",
                     course_titles=["Claude 101", "Claude 101 ", ""])
    result, goi = _process(lambda img: kind)
    assert result.stage != pipeline.SKIP_MULTI_COURSE_STAGE
    assert goi["llm1"] == 1


@pytest.mark.parametrize("kind", [
    # Chứng chỉ thường.
    ImageKind(page_type="certificate", course_titles=["Claude 101"]),
    # Chứng chỉ chương trình in khóa con: model gán certificate -> KHÔNG bỏ qua
    # dù có nhiều tên.
    ImageKind(page_type="certificate", course_titles=["A", "B", "C"]),
    # Trang danh sách nhưng chỉ 1 khóa (vd hồ sơ mới học một khóa).
    ImageKind(page_type="course_list", course_titles=["Claude 101"]),
    ImageKind(page_type="course_list", course_titles=[]),
    ImageKind(page_type="other", course_titles=["A", "B"]),
    ImageKind(page_type=None, course_titles=["A", "B"]),
])
def test_thieu_mot_trong_hai_tin_hieu_thi_DI_TIEP_nhu_cu(kind):
    result, goi = _process(lambda img: kind)
    assert result.verdict == Verdict.APPROVED
    assert result.stage == "llm1"
    assert goi["llm1"] == 1


def test_page_type_khong_phan_biet_hoa_thuong():
    kind = ImageKind(page_type=" Course_List ", course_titles=["A", "B"])
    result, _ = _process(lambda img: kind)
    assert result.stage == pipeline.SKIP_MULTI_COURSE_STAGE


def test_bo_loc_LOI_thi_di_tiep_KHONG_tinh_hong_ky_thuat():
    """Gemma hỏng ở bước lọc: ca hỏng kỹ thuật chặn cả hàng đợi, không đáng
    vì một bộ lọc phụ. Đi tiếp = hành vi trước khi có bộ lọc."""
    def hong(img):
        raise llm_vision.LlmVisionError("503")
    result, goi = _process(hong)
    assert result.verdict == Verdict.APPROVED
    assert result.stage not in database.TECHNICAL_STAGES
    assert goi["llm1"] == 1


def test_khong_truyen_bo_loc_thi_pipeline_y_nhu_cu():
    result, goi = _process(None)
    assert result.stage == "llm1" and goi["llm1"] == 1


def test_bo_loc_chi_doc_trang_DAU_nhu_llm1():
    seen = []
    pipeline.process(
        images=[b"trang1", b"trang2"], given=_given(),
        extract_from_image=lambda img: _khop(), ocr_images=lambda c, i: "",
        extract_from_text=lambda t: _khop(), azure_client=None,
        detect_course_list=lambda img: seen.append(img) or ImageKind())
    assert seen == [b"trang1"]


# ===== llm_vision.detect_course_list =====

class _FakeLlm:
    def __init__(self, content):
        self.content = content

    def invoke(self, messages):
        return SimpleNamespace(content=self.content)


def test_detect_parse_json_co_markdown_fence():
    raw = "```json\n" + json.dumps({"page_type": "course_list",
                                     "course_titles": ["A", "B"]}) + "\n```"
    kind = llm_vision.detect_course_list(b"\x89PNG\r\n\x1a\n", llm=_FakeLlm(raw))
    assert kind.page_type == "course_list" and kind.course_titles == ["A", "B"]


def test_detect_thieu_truong_thi_mac_dinh_rong():
    kind = llm_vision.detect_course_list(b"x", llm=_FakeLlm('{"page_type": "other"}'))
    assert kind.course_titles == []


def test_detect_khong_phai_json_thi_nem_LlmVisionError():
    with pytest.raises(llm_vision.LlmVisionError):
        llm_vision.detect_course_list(b"x", llm=_FakeLlm("không biết"))


def test_prompt_llm1_KHONG_bi_doi():
    """Bộ lọc dùng prompt RIÊNG; PROMPT của LLM1 phải giữ nguyên để kết quả
    trích xuất đã đánh giá không bị lệch."""
    assert "course_list" not in llm_vision.PROMPT
    assert llm_vision.COURSE_LIST_PROMPT != llm_vision.PROMPT


# ===== database / run.py =====

def test_stage_khop_giua_pipeline_va_database():
    assert pipeline.SKIP_MULTI_COURSE_STAGE == database.SKIP_MULTI_COURSE_STAGE


def test_la_ca_BO_QUA_khong_phai_hong_ky_thuat():
    assert database.SKIP_MULTI_COURSE_STAGE in database.SKIP_STAGES
    assert database.SKIP_MULTI_COURSE_STAGE not in database.TECHNICAL_STAGES


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    db = str(tmp_path / "t.db")
    database.init_db(db)
    monkeypatch.setattr(database, "DB_PATH", db)
    monkeypatch.setattr(run.alert, "send_alert", lambda *a, **k: False)
    monkeypatch.setattr(run.alert, "STATE_FILE", tmp_path / ".alert_state.json")
    monkeypatch.setattr(run, "_last_skipped_ids", frozenset())
    monkeypatch.setattr(run.settings, "duplicate_check", False)
    return db


def _item(uc_id):
    return {"id": uc_id, "certificate_id": f"c-{uc_id}",
            "courseLink": "https://anthropic.skilljar.com/claude-101", "courseId": "K1",
            "employeeId": "00324344", "employeeName": "Nguyễn Bá Anh",
            "employeeEmail": "AnhNB2@fpt.com", "courseName": "Claude 101",
            "providerName": "Anthropic"}


def _chay(items, detect, monkeypatch):
    """Chạy MỘT vòng run.py thật tới pipeline, chỉ giả các lời gọi ra ngoài."""
    da_nop, goi_llm1 = [], []
    monkeypatch.setattr(run.llm_vision, "detect_course_list", detect)
    monkeypatch.setattr(run.llm_vision, "extract_from_image",
                        lambda img: goi_llm1.append(img) or _khop())
    monkeypatch.setattr(run.file_utils, "read_as_images", lambda p: [b"x"])

    def nop(dtos):
        da_nop.extend(dtos)
        return {"successList": [{"id": d["id"]} for d in dtos], "failList": []}

    with patch.object(run.client, "get_pending_list", return_value=items), \
         patch.object(run.client, "download_certificates",
                      side_effect=lambda cc: [
                          {"userCourseId": c["UserCourseId"], "anh_bytes": b"x"}
                          for c in cc]), \
         patch.object(run.client, "update_status", side_effect=nop), \
         patch.object(run.archive, "save", return_value=None), \
         patch.object(run.archive, "write_verdict", return_value=None):
        run.process_one_round(None)
    return da_nop, goi_llm1


def test_run_ca_danh_sach_KHONG_nop_va_vong_sau_KHONG_quet_lai(moi_truong, monkeypatch):
    da_nop, goi_llm1 = _chay([_item("A")], lambda img: DANH_SACH_ANTHROPIC, monkeypatch)
    assert da_nop == [] and goi_llm1 == []
    assert database.skipped_ids(["A"], moi_truong) == {"A"}

    goi_loc = []
    da_nop, _ = _chay([_item("A")], lambda img: goi_loc.append(img), monkeypatch)
    assert goi_loc == [] and da_nop == []


def test_run_tat_co_thi_KHONG_goi_bo_loc(moi_truong, monkeypatch):
    monkeypatch.setattr(run.settings, "multi_course_check", False)

    def khong_duoc_goi(img):
        raise AssertionError("bộ lọc bị gọi khi đã tắt")

    da_nop, goi_llm1 = _chay([_item("A")], khong_duoc_goi, monkeypatch)
    assert len(goi_llm1) == 1
    assert [d["id"] for d in da_nop] == ["A"]
