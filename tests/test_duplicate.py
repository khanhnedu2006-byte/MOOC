"""Test luật chống NỘP TRÙNG khóa học (test_duplicate).

Có HAI luồng cùng đẩy chứng chỉ vào eLIS: hệ thống này (quét bằng AI), và
luồng đồng bộ tự động của FPT Elearning (đẩy thẳng, không xác minh). Cùng một
khóa học của cùng một người vì thế có thể vào eLIS hai lần, thành hai bản ghi
riêng với hai user_course_id khác nhau.

Đối chiếu bằng EMAIL + TÊN KHÓA HỌC, theo phương án mentor chốt. Không dùng
user_course_id: hai lần nộp là hai bản ghi riêng nên id luôn khác nhau — tra
theo nó thì không bao giờ khớp được cái gì.

Bẫy chính nằm ở normalize(): dữ liệu thật có 6.268 cách viết tên khóa, sau
chuẩn hóa còn 6.132. So thô là bỏ sót 129 nhóm chỉ lệch dấu cách hoặc
hoa/thường.
"""

import logging
import pathlib
import sqlite3
import sys
from unittest.mock import patch

import pytest

GOC = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GOC))
sys.path.insert(0, str(GOC / "src"))

import client                          # noqa: E402
import history                         # noqa: E402
import run                             # noqa: E402
from database import database          # noqa: E402
from schemas import ProcessResult, Verdict   # noqa: E402

logging.disable(logging.CRITICAL)


def _approved(email: str, course: str) -> dict:
    return {"employeeEmail": email, "courseName": course}


@pytest.fixture
def bat_luat(monkeypatch):
    """Bật luật (conftest tắt mặc định để test khác khỏi gọi mạng)."""
    monkeypatch.setattr(run.settings, "duplicate_check", True)
    monkeypatch.setattr(history.settings, "duplicate_check", True)
    monkeypatch.setattr(history.settings, "history_refresh_minutes", 60)


# ===== Chỉ mục =====

def test_nap_lich_su_va_tra_cuu(bat_luat):
    with patch.object(client, "get_all_by_status",
                      return_value=[_approved("hoabd5@fpt.com", "Python cơ bản")]):
        assert history.refresh(force=True) is True
    assert history.already_completed("hoabd5@fpt.com", "Python cơ bản") is True
    assert history.already_completed("hoabd5@fpt.com", "Java cơ bản") is False
    assert history.already_completed("nguoikhac@fpt.com", "Python cơ bản") is False


def test_email_khong_phan_biet_hoa_thuong(bat_luat):
    with patch.object(client, "get_all_by_status",
                      return_value=[_approved("HoaBD5@fpt.com", "Python cơ bản")]):
        history.refresh(force=True)
    assert history.already_completed("hoabd5@FPT.com", "Python cơ bản") is True


@pytest.mark.parametrize("da_duyet, vua_nop", [
    (" What Can AI Do for Marketing?", "What Can AI Do for Marketing?"),
    ("Business Analysis:  Working with Use Cases",
     "Business Analysis: Working with Use Cases"),
    ("MỞ KHÓA AI: CẨM NANG VIẾT PROMPT", "Mở Khoá AI: cẩm nang viết Prompt"),
])
def test_ten_khoa_lech_cach_viet_van_tinh_la_TRUNG(bat_luat, da_duyet, vua_nop):
    """Ba cặp này lấy nguyên từ dữ liệu thật — cùng một khóa, hai cách viết.

    So thô thì cả ba đều lọt lưới; normalize() gộp chúng lại.
    """
    with patch.object(client, "get_all_by_status",
                      return_value=[_approved("a@fpt.com", da_duyet)]):
        history.refresh(force=True)
    assert history.already_completed("a@fpt.com", vua_nop) is True


def test_khac_khoa_thi_KHONG_tinh_la_trung(bat_luat):
    with patch.object(client, "get_all_by_status",
                      return_value=[_approved("a@fpt.com", "Python nâng cao")]):
        history.refresh(force=True)
    assert history.already_completed("a@fpt.com", "Python cơ bản") is False


def test_thieu_email_hoac_ten_khoa_thi_KHONG_doan(bat_luat):
    """Từ chối dựa trên dữ liệu khuyết là kiểu sai đắt nhất. Bỏ sót thì chứng
    chỉ chỉ đi tiếp theo luồng thường."""
    with patch.object(client, "get_all_by_status",
                      return_value=[_approved("a@fpt.com", "Python")]):
        history.refresh(force=True)
    assert history.already_completed(None, "Python") is False
    assert history.already_completed("a@fpt.com", None) is False
    assert history.already_completed("", "") is False


def test_ban_ghi_thieu_truong_KHONG_lam_hong_ca_chi_muc(bat_luat):
    with patch.object(client, "get_all_by_status", return_value=[
            _approved("a@fpt.com", "Python"),
            _approved("", "Java"),
            _approved("b@fpt.com", None)]):
        history.refresh(force=True)
    assert history.already_completed("a@fpt.com", "Python") is True
    assert history.stats()[0] == 1


def test_KEO_LICH_SU_HONG_thi_MO_chu_khong_dong(bat_luat):
    """eLIS lỗi thì chỉ mục rỗng, và chỉ mục rỗng nghĩa là không phát hiện ca
    trùng nào — chứng chỉ đi tiếp theo luồng bình thường.

    Chiều ngược lại mới nguy: coi lỗi mạng là 'chưa từng duyệt' rồi từ chối
    hàng loạt thì một sự cố hạ tầng biến thành hàng trăm từ chối oan.
    """
    with patch.object(client, "get_all_by_status",
                      side_effect=client.ElisError("eLIS sập")):
        assert history.refresh(force=True) is False
    assert history.already_completed("a@fpt.com", "Python") is False


def test_ghi_nho_ngay_sau_khi_duyet(bat_luat):
    """Nộp cùng khóa hai lần trong cùng một giờ thì cái thứ hai vẫn phải bị
    bắt, không đợi tới lần nạp lịch sử kế tiếp."""
    with patch.object(client, "get_all_by_status", return_value=[]):
        history.refresh(force=True)
    assert history.already_completed("a@fpt.com", "Python") is False
    history.remember("a@fpt.com", "Python")
    assert history.already_completed("a@fpt.com", "Python") is True


# ===== Hành vi ở run.py =====

def _item(uc_id: str, course: str = "Python cơ bản",
          email: str = "hoabd5@fpt.com") -> dict:
    return {"id": uc_id, "certificate_id": f"c-{uc_id}", "courseId": f"k-{course}",
            "employeeId": "00332383", "employeeName": "Bùi Đức Hòa",
            "employeeEmail": email, "courseName": course,
            "providerName": "Coursera"}


def _duyet():
    return ProcessResult(verdict=Verdict.APPROVED, reason="Khớp", stage="llm1")


@pytest.fixture
def moi_truong(tmp_path, monkeypatch, bat_luat):
    db = str(tmp_path / "t.db")
    database.init_db(db)
    monkeypatch.setattr(database, "DB_PATH", db)
    monkeypatch.setattr(run.settings, "technical_alert_after", 3)
    monkeypatch.setattr(run.settings, "technical_retry_cooldown_minutes", 30)
    monkeypatch.setattr(run.alert, "send_alert", lambda *a, **k: False)
    monkeypatch.setattr(run.alert, "STATE_FILE", tmp_path / ".alert_state.json")
    return db


def _chay(items, ket_qua_scan, lich_su):
    """Chạy một vòng. Trả về (DTO đã nộp, số lần thật sự quét bằng LLM)."""
    da_nop, da_quet = [], []
    it_kq = iter(ket_qua_scan)

    def nop(dtos):
        da_nop.extend(dtos)
        return {"successList": [{"id": d["id"]} for d in dtos], "failList": []}

    def quet(*a):
        da_quet.append(a)
        return next(it_kq)

    with patch.object(client, "get_pending_list", return_value=items), \
         patch.object(client, "get_all_by_status", return_value=lich_su), \
         patch.object(client, "download_certificates",
                      side_effect=lambda cc: [
                          {"userCourseId": c["UserCourseId"], "anh_bytes": b"x"}
                          for c in cc]), \
         patch.object(client, "update_status", side_effect=nop), \
         patch.object(run, "scan_certificate", side_effect=quet), \
         patch.object(run.archive, "save", return_value=None), \
         patch.object(run.archive, "write_verdict", return_value=None):
        run.process_one_round(None)
    return da_nop, len(da_quet)


def test_ca_trung_bi_TU_CHOI_ma_KHONG_ton_luot_LLM(moi_truong):
    da_nop, so_lan_quet = _chay(
        [_item("A")], [], [_approved("hoabd5@fpt.com", "Python cơ bản")])
    assert so_lan_quet == 0, "đã quét LLM cho một ca vốn kết luận được từ DB"
    assert len(da_nop) == 1
    assert da_nop[0]["status"] == "REJECTED"
    assert da_nop[0]["comment"] == "Cán bộ nộp trùng khóa học"


def test_ca_trung_KHONG_chan_cac_ca_sau(moi_truong):
    """Nộp trùng là chuyện của riêng một chứng chỉ, không phải sự cố cả lô."""
    da_nop, so_lan_quet = _chay(
        [_item("A", "Python cơ bản"), _item("B", "Java cơ bản")],
        [_duyet()],
        [_approved("hoabd5@fpt.com", "Python cơ bản")])
    assert so_lan_quet == 1, "chứng chỉ B phải được quét bình thường"
    assert {d["id"]: d["status"] for d in da_nop} == {"A": "REJECTED", "B": "APPROVED"}


def test_ghi_log_voi_stage_duplicate(moi_truong):
    _chay([_item("A")], [], [_approved("hoabd5@fpt.com", "Python cơ bản")])
    conn = sqlite3.connect(moi_truong)
    rows = conn.execute("SELECT verdict, stage, reason, course_name "
                        "FROM process_log WHERE user_course_id='A'").fetchall()
    conn.close()
    assert rows == [("REJECTED", "duplicate", "Cán bộ nộp trùng khóa học",
                     "Python cơ bản")]


def test_duplicate_KHONG_bi_dem_nhu_hong_ky_thuat(moi_truong):
    """Nộp trùng là kết luận nghiệp vụ, không phải sự cố hệ thống. Xếp nhầm
    vào TECHNICAL_STAGES thì nó vừa được thử lại vô ích vừa kéo theo email
    báo động cho người vận hành."""
    _chay([_item("A")], [], [_approved("hoabd5@fpt.com", "Python cơ bản")])
    assert "duplicate" not in database.TECHNICAL_STAGES
    assert database.technical_retry_state(["A"], moi_truong) == {}


def test_TAT_luat_thi_van_quet_nhu_cu(moi_truong, monkeypatch):
    monkeypatch.setattr(run.settings, "duplicate_check", False)
    da_nop, so_lan_quet = _chay(
        [_item("A")], [_duyet()], [_approved("hoabd5@fpt.com", "Python cơ bản")])
    assert so_lan_quet == 1
    assert da_nop[0]["status"] == "APPROVED"


def test_nop_cung_khoa_HAI_LAN_trong_MOT_vong(moi_truong):
    """Hai bản ghi khác nhau, cùng người cùng khóa, cùng một vòng xử lý.

    Cái đầu chưa có trong lịch sử nên được quét và duyệt; cái sau phải bị bắt
    ngay nhờ history.remember(), chứ không đợi lần nạp lịch sử kế tiếp.
    """
    da_nop, so_lan_quet = _chay(
        [_item("A", "Python cơ bản"), _item("B", "Python cơ bản")],
        [_duyet()], [])
    assert so_lan_quet == 1, "bản ghi thứ hai vẫn tốn một lượt LLM"
    assert {d["id"]: d["status"] for d in da_nop} == {"A": "APPROVED", "B": "REJECTED"}
    assert da_nop[1]["comment"] == "Cán bộ nộp trùng khóa học"
