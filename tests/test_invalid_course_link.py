"""Test ca BỎ QUA vì courseLink trên eLIS không phải URL hợp lệ.

Ca thật: khóa "Tiếng Anh Vstep" (Chương trình chuyển đổi) có courseLink =
"Tiếng Anh Vstep" — không có nguồn khóa học để đối chiếu.

Chạy: pytest tests/test_invalid_course_link.py -v
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import client
import run
from database import database
from schemas import ProcessResult, Verdict


@pytest.mark.parametrize("link", [
    "https://anthropic.skilljar.com/claude-101",
    "http://anthropic.skilljar.com/ai-fluency-framework-foundations",
    "https://www.linkedin.com/learning/x?dApp=261700545&leis=LAA",
    "  https://learning.sap.com/certifications/x  ",
])
def test_link_hop_le(link):
    assert run.is_valid_course_link(link) is True


@pytest.mark.parametrize("link", [
    "Tiếng Anh Vstep",          # ca thật
    None, "", "   ",
    "anthropic.skilljar.com/claude-101",   # thiếu scheme
    "ftp://files.example.com/x",
    "https://localhost/x",       # host không có tên miền
    "https://",
    "https://exa mple.com/x",    # có khoảng trắng
    "javascript:alert(1)",
])
def test_link_khong_hop_le(link):
    assert run.is_valid_course_link(link) is False


def _item(uc_id: str, link: str) -> dict:
    return {"id": uc_id, "certificate_id": f"c-{uc_id}", "courseLink": link,
            "courseId": "K1", "employeeId": "003", "employeeName": "Bùi Đức Hòa",
            "employeeEmail": "hoabd3@fpt.com", "courseName": "Tiếng Anh Vstep",
            "providerName": "Chương trình chuyển đổi"}


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    db = str(tmp_path / "t.db")
    database.init_db(db)
    monkeypatch.setattr(database, "DB_PATH", db)
    monkeypatch.setattr(run.settings, "duplicate_check", True)
    monkeypatch.setattr(run.alert, "send_alert", lambda *a, **k: False)
    monkeypatch.setattr(run.alert, "STATE_FILE", tmp_path / ".alert_state.json")
    monkeypatch.setattr(run, "_last_skipped_ids", frozenset())
    return db


def _chay(items):
    goi = {"email": [], "tai": [], "quet": [], "nop": []}

    def tai(cc):
        goi["tai"] += [c["UserCourseId"] for c in cc]
        return [{"userCourseId": c["UserCourseId"], "anh_bytes": b"x"} for c in cc]

    def nop(dtos):
        goi["nop"] += [d["id"] for d in dtos]
        return {"successList": [{"id": d["id"]} for d in dtos], "failList": []}

    def quet(image_bytes, info, ocr_client):
        goi["quet"].append(info["id"])
        return ProcessResult(verdict=Verdict.APPROVED, reason="Khớp", stage="llm1")

    with patch.object(client, "get_pending_list", return_value=items), \
         patch.object(client, "get_by_email",
                      side_effect=lambda e: goi["email"].append(e) or []), \
         patch.object(client, "download_certificates", side_effect=tai), \
         patch.object(client, "update_status", side_effect=nop), \
         patch.object(run, "scan_certificate", side_effect=quet), \
         patch.object(run.archive, "save", return_value=None), \
         patch.object(run.archive, "write_verdict", return_value=None):
        run.process_one_round(None)
    return goi


def test_link_sai_KHONG_goi_gi_ca(moi_truong):
    goi = _chay([_item("A", "Tiếng Anh Vstep")])
    assert goi == {"email": [], "tai": [], "quet": [], "nop": []}


def test_link_sai_KHONG_chan_hang_doi(moi_truong):
    goi = _chay([_item("A", "Tiếng Anh Vstep"), _item("B", "https://learn.example.com/k")])
    assert goi["quet"] == ["B"] and goi["nop"] == ["B"]


def test_ghi_log_dung_stage(moi_truong):
    _chay([_item("A", "Tiếng Anh Vstep")])
    [row] = database.read_recent_logs(5, moi_truong)
    assert row["stage"] == database.SKIP_INVALID_LINK_STAGE
    assert row["verdict"] == "WAITING"
    assert "Tiếng Anh Vstep" in row["reason"]


def test_vong_sau_loc_ngay_tu_dau(moi_truong):
    _chay([_item("A", "Tiếng Anh Vstep")])
    assert database.skipped_ids(["A"], moi_truong) == {"A"}


def test_KHONG_tinh_la_hong_ky_thuat():
    assert database.SKIP_INVALID_LINK_STAGE not in database.TECHNICAL_STAGES
    assert database.SKIP_INVALID_LINK_STAGE in database.SKIP_STAGES
