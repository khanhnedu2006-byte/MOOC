"""Test ca BỎ QUA vì người học nộp file sai định dạng (WebP, HEIC, DOCX...).

Phát hiện ngay ở API ② (client.py gắn cờ unsupported_mime), trước khi tốn
lượt LLM nào. Trước đây ca này rơi vào download_error / file_error — hỏng
kỹ thuật, chặn đầu hàng và thử lại mãi, nên một file WebP đứng im cả hàng đợi.

Chạy: pytest tests/test_unsupported_file.py -v
"""

import base64
import io
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import client
import file_utils
import run
from database import database
from schemas import ProcessResult, Verdict


def _image(fmt: str) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(buf, fmt)
    return buf.getvalue()


def _docx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "x")
        z.writestr("word/document.xml", "x")
    return buf.getvalue()


# ===== file_utils.unsupported_mime =====

@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "BMP", "TIFF"])
def test_anh_ho_tro_thi_None(fmt):
    assert file_utils.unsupported_mime(_image(fmt)) is None


def test_pdf_ho_tro_thi_None():
    assert file_utils.unsupported_mime(b"%PDF-1.4\n%fake\n") is None


@pytest.mark.parametrize("data, mime", [
    (_image("WEBP"), "image/webp"),
    (_image("GIF"), "image/gif"),
])
def test_anh_khong_ho_tro_tra_ve_mime(data, mime):
    assert file_utils.unsupported_mime(data) == mime


def test_docx_khong_ho_tro():
    # libmagic trả application/zip hoặc ...wordprocessingml... tùy thứ tự file
    # bên trong — cả hai đều là "không hỗ trợ".
    assert file_utils.unsupported_mime(_docx()) in (
        "application/zip",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")


def test_bytes_rong_KHONG_ket_luan():
    assert file_utils.unsupported_mime(b"") is None


# ===== client: API ② gắn cờ =====

def _body(data: bytes, **extra) -> dict:
    return {"data": [{"userCourseId": "A", "certificate_id": "c-A",
                      "fileName": "cert.x",
                      "fileBase64": base64.b64encode(data).decode(), **extra}]}


def test_client_gan_co_webp():
    [f] = client._read_files_from_json(_body(_image("WEBP")))
    assert f["unsupported_mime"] == "image/webp"
    assert f["anh_bytes"].startswith(b"RIFF")
    assert f["ten_file"] == "cert.x"


def test_client_gan_co_docx():
    [f] = client._read_files_from_json(_body(_docx()))
    assert f["unsupported_mime"] is not None


def test_client_file_hop_le_KHONG_gan_co():
    [f] = client._read_files_from_json(_body(_image("PNG")))
    assert f["unsupported_mime"] is None


def test_chuoi_van_ban_dai_KHONG_bi_coi_la_file():
    """Không có chữ ký file thì chỉ tin trường có tên gợi ý file và giải CHẶT."""
    body = {"data": [{"userCourseId": "A", "certificate_id": "c-A",
                      "description": "Khóa học này dạy về " * 10}]}
    with pytest.raises(client.ElisError):
        client._read_files_from_json(body)


# ===== run.py: xếp vào ca BỎ QUA =====

def _item(uc_id: str) -> dict:
    return {"id": uc_id, "certificate_id": f"c-{uc_id}", "courseLink": "https://learn.example.com/k",
            "courseId": "K1",
            "employeeId": "003", "employeeName": "Bùi Đức Hòa",
            "employeeEmail": "hoabd3@fpt.com", "courseName": "ISO 27001",
            "providerName": "Coursera"}


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    db = str(tmp_path / "t.db")
    database.init_db(db)
    monkeypatch.setattr(database, "DB_PATH", db)
    monkeypatch.setattr(run.settings, "technical_alert_after", 3)
    monkeypatch.setattr(run.settings, "technical_retry_cooldown_minutes", 30)
    monkeypatch.setattr(run.alert, "send_alert", lambda *a, **k: False)
    monkeypatch.setattr(run.alert, "STATE_FILE", tmp_path / ".alert_state.json")
    monkeypatch.setattr(run, "_last_skipped_ids", frozenset())
    return db


def _chay(items, bad_ids):
    """Một vòng; id trong bad_ids được API ② trả về dạng file WebP."""
    da_nop, da_quet = [], []

    def tai(cc):
        return [{"userCourseId": c["UserCourseId"], "anh_bytes": b"x",
                 "ten_file": "cert.webp",
                 "unsupported_mime": "image/webp" if c["UserCourseId"] in bad_ids else None}
                for c in cc]

    def nop(dtos):
        da_nop.extend(dtos)
        return {"successList": [{"id": d["id"]} for d in dtos], "failList": []}

    def quet(image_bytes, info, ocr_client):
        da_quet.append(info["id"])
        return ProcessResult(verdict=Verdict.APPROVED, reason="Khớp", stage="llm1")

    with patch.object(client, "get_pending_list", return_value=items), \
         patch.object(client, "download_certificates", side_effect=tai), \
         patch.object(client, "update_status", side_effect=nop), \
         patch.object(run, "scan_certificate", side_effect=quet), \
         patch.object(run.archive, "save", return_value=None), \
         patch.object(run.archive, "write_verdict", return_value=None):
        result = run.process_one_round(None)
    return result, [d["id"] for d in da_nop], da_quet


def test_file_sai_dinh_dang_KHONG_goi_llm_KHONG_nop(moi_truong):
    _, nop, quet = _chay([_item("A")], {"A"})
    assert quet == [], "đã gọi LLM cho một file hệ thống không đọc được"
    assert nop == [], "đã nộp API ③ cho ca bỏ qua"


def test_file_sai_dinh_dang_KHONG_chan_hang_doi(moi_truong):
    _, nop, quet = _chay([_item("A"), _item("B")], {"A"})
    assert quet == ["B"]
    assert nop == ["B"]


def test_ghi_log_dung_stage_va_ly_do(moi_truong):
    _chay([_item("A")], {"A"})
    [row] = database.read_recent_logs(5, moi_truong)
    assert row["stage"] == database.SKIP_UNSUPPORTED_FILE_STAGE
    assert row["verdict"] == "WAITING"
    assert "image/webp" in row["reason"] and "cert.webp" in row["reason"]
    assert row["course_name"] == "ISO 27001"


def test_vong_sau_KHONG_tai_lai(moi_truong):
    _chay([_item("A")], {"A"})
    with patch.object(client, "download_certificates") as tai:
        tai.side_effect = lambda cc: [{"userCourseId": c["UserCourseId"],
                                       "anh_bytes": b"x"} for c in cc]
        _, nop, quet = _chay([_item("A"), _item("B")], set())
    assert quet == ["B"], "đã tải + quét lại ca file sai định dạng"


def test_KHONG_tinh_la_hong_ky_thuat(moi_truong):
    _chay([_item("A")], {"A"})
    assert database.SKIP_UNSUPPORTED_FILE_STAGE not in database.TECHNICAL_STAGES
    assert database.technical_retry_state(["A"], moi_truong) == {}


def test_is_skip_nhan_ca_hai_loai():
    for stage in database.SKIP_STAGES:
        assert run.is_skip(ProcessResult(verdict=Verdict.WAITING, stage=stage))
