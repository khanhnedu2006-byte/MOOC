"""Test ca BỎ QUA theo nhà cung cấp (SKIP_PROVIDERS, mặc định Udacity).

Chạy: pytest tests/test_skip_provider.py -v
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


@pytest.mark.parametrize("providers, provider, expected", [
    ("Udacity", "Udacity", True),
    ("Udacity", "  UDACITY ", True),          # hoa/thường, khoảng trắng
    ("Udacity", "LinkedIn", False),
    ("Udacity", None, False),
    ("Udacity, Coursera", "coursera", True),
    ("", "Udacity", False),                   # rỗng = tắt
    ("Udacity", "Udacity Business", False),   # so trọn tên, không so một phần
])
def test_is_skipped_provider(monkeypatch, providers, provider, expected):
    monkeypatch.setattr(run.settings, "skip_providers", providers)
    assert run.is_skipped_provider(provider) is expected


def _item(uc_id: str, provider: str) -> dict:
    return {"id": uc_id, "certificate_id": f"c-{uc_id}",
            "courseLink": "https://learn.example.com/k", "courseId": "K1",
            "employeeId": "003", "employeeName": "Bùi Đức Hòa",
            "employeeEmail": "hoabd3@fpt.com", "courseName": "Data Analyst",
            "providerName": provider}


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    db = str(tmp_path / "t.db")
    database.init_db(db)
    monkeypatch.setattr(database, "DB_PATH", db)
    monkeypatch.setattr(run.settings, "skip_providers", "Udacity")
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

    def quet(image_bytes, info, azure_client):
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


def test_udacity_KHONG_goi_gi_ca(moi_truong):
    assert _chay([_item("A", "Udacity")]) == {"email": [], "tai": [], "quet": [], "nop": []}


def test_udacity_KHONG_chan_hang_doi(moi_truong):
    goi = _chay([_item("A", "Udacity"), _item("B", "LinkedIn")])
    assert goi["quet"] == ["B"] and goi["nop"] == ["B"]


def test_ghi_log_va_vong_sau_loc_tu_dau(moi_truong):
    _chay([_item("A", "Udacity")])
    [row] = database.read_recent_logs(5, moi_truong)
    assert row["stage"] == database.SKIP_PROVIDER_STAGE
    assert row["verdict"] == "WAITING"
    assert row["provider"] == "Udacity"
    assert database.skipped_ids(["A"], moi_truong) == {"A"}


def test_KHONG_tinh_la_hong_ky_thuat():
    assert database.SKIP_PROVIDER_STAGE not in database.TECHNICAL_STAGES
    assert database.SKIP_PROVIDER_STAGE in database.SKIP_STAGES
