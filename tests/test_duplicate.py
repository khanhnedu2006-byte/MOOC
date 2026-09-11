"""Test luật chống NỘP TRÙNG khóa học (test_duplicate).

HAI luồng cùng đẩy chứng chỉ vào eLIS: hệ thống này và luồng đồng bộ của FPT
Elearning. Cùng một khóa của cùng một người có thể vào eLIS hai lần, thành
hai bản ghi với hai user_course_id khác nhau — nên đối chiếu bằng EMAIL +
TÊN KHÓA HỌC. Hỏi eLIS bằng `employeeEmail`, KHÔNG kèm `status`, rồi tự lọc
`submitStatus == "APPROVED"`. Hai phép lọc đó là phần nguy hiểm của luật:

  1. Lọc `submitStatus`. Kết quả chứa cả chứng chỉ WAITING đang xử lý, bỏ lọc
     thì mọi chứng chỉ "trùng" với CHÍNH NÓ. Không phải `status` (trạng thái
     đăng ký học, luôn "REGISTED").

  2. Tự kiểm email. API ① bỏ qua tham số lạ trong IM LẶNG, vẫn trả 200 kèm
     nguyên bộ dữ liệu — lúc đó cái trả về là lịch sử của MỌI người.

Cả hai kiểu hỏng đều không báo lỗi, chỉ lộ ra khi hàng đợi bị từ chối sạch.
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
import run                             # noqa: E402
from database import database          # noqa: E402
from schemas import ProcessResult, Verdict   # noqa: E402

logging.disable(logging.CRITICAL)


def _row(email: str, course: str, submit_status: str = "APPROVED") -> dict:
    """Một bản ghi như API ① trả về (rút gọn, giữ đúng tên trường)."""
    return {"employeeEmail": email, "courseName": course,
            "submitStatus": submit_status, "status": "REGISTED"}


@pytest.fixture
def bat_luat(monkeypatch):
    """Bật luật (conftest tắt mặc định để test khác khỏi gọi mạng)."""
    monkeypatch.setattr(run.settings, "duplicate_check", True)
    run._approved_this_round.clear()
    yield
    run._approved_this_round.clear()


# ===== Hỏi eLIS những khóa một người đã hoàn thành =====

def test_lay_dung_khoa_cua_dung_nguoi(bat_luat):
    with patch.object(client, "get_by_email", return_value=[
            _row("hoabd5@fpt.com", "Python cơ bản"),
            _row("hoabd5@fpt.com", "Java cơ bản")]) as goi:
        assert run.completed_courses("hoabd5@fpt.com") == {"python co ban",
                                                           "java co ban"}
    assert goi.call_args.args[0] == "hoabd5@fpt.com"


@pytest.mark.parametrize("submit_status", ["WAITING", "REJECTED", "", None])
def test_CHI_lay_ban_ghi_DA_DUYET(bat_luat, submit_status):
    """Ca hỏng tệ nhất trong cả luật, và nó không báo lỗi gì.

    eLIS trả về cả chứng chỉ WAITING đang xử lý; bỏ lọc submitStatus thì cả
    hàng đợi bị từ chối tự động.
    """
    with patch.object(client, "get_by_email", return_value=[
            _row("a@fpt.com", "Python cơ bản", submit_status)]):
        assert run.completed_courses("a@fpt.com") == set()


def test_doc_submitStatus_chu_KHONG_phai_status(bat_luat):
    """Hai trường nằm cạnh nhau trong cùng bản ghi và rất dễ nhầm.

    `status` luôn là "REGISTED", đọc nhầm nó thì phép lọc mất tác dụng.
    """
    row = _row("a@fpt.com", "Python cơ bản", "WAITING")
    assert row["status"] == "REGISTED"
    with patch.object(client, "get_by_email", return_value=[row]):
        assert run.completed_courses("a@fpt.com") == set()

    row["submitStatus"] = "APPROVED"
    with patch.object(client, "get_by_email", return_value=[row]):
        assert run.completed_courses("a@fpt.com") == {"python co ban"}


def test_email_khong_phan_biet_hoa_thuong(bat_luat):
    with patch.object(client, "get_by_email",
                      return_value=[_row("HoaBD5@fpt.com", "Python cơ bản")]):
        assert run.completed_courses("hoabd5@FPT.com") == {"python co ban"}


@pytest.mark.parametrize("da_duyet, vua_nop", [
    (" What Can AI Do for Marketing?", "What Can AI Do for Marketing?"),
    ("Business Analysis:  Working with Use Cases",
     "Business Analysis: Working with Use Cases"),
    ("MỞ KHÓA AI: CẨM NANG VIẾT PROMPT", "Mở Khoá AI: cẩm nang viết Prompt"),
])
def test_ten_khoa_lech_cach_viet_van_tinh_la_TRUNG(bat_luat, da_duyet, vua_nop):
    """Ba cặp lấy từ dữ liệu thật — cùng một khóa, hai cách viết.

    So thô là bỏ sót các nhóm chỉ lệch nhau khoảng trắng hoặc hoa/thường.
    """
    with patch.object(client, "get_by_email",
                      return_value=[_row("a@fpt.com", da_duyet)]):
        assert run.is_duplicate({"employeeEmail": "a@fpt.com",
                                 "courseName": vua_nop}) is True


def test_API_KHONG_LOC_thi_BO_QUA_luat_chu_khong_tu_choi_bua(bat_luat):
    """Ca hỏng nguy hiểm nhất, và nó KHÔNG báo lỗi gì cả.

    API trả 200 nhưng phớt lờ tham số lọc; tin vào đó thì mọi chứng chỉ
    "trùng" với khóa của một người lạ.
    """
    with patch.object(client, "get_by_email", return_value=[
            _row("nguoikhac@fpt.com", "Python cơ bản"),
            _row("hoabd5@fpt.com", "Python cơ bản")]):
        assert run.completed_courses("hoabd5@fpt.com") == set()


def test_thieu_email_hoac_ten_khoa_thi_KHONG_doan(bat_luat):
    """Từ chối dựa trên dữ liệu khuyết là kiểu sai đắt nhất; bỏ sót thì chứng
    chỉ chỉ đi tiếp theo luồng thường."""
    assert run.completed_courses(None) == set()
    assert run.completed_courses("") == set()
    assert run.is_duplicate({"employeeEmail": None, "courseName": "Python"}) is False
    assert run.is_duplicate({"employeeEmail": "a@fpt.com", "courseName": ""}) is False


def test_eLIS_LOI_thi_KHONG_BAO_GIO_tu_choi_bua(bat_luat):
    """Coi lỗi mạng là "đã từng duyệt" thì thành hàng trăm từ chối oan.

    Cách xử lý tùy loại lỗi (xem test_tra_lich_su_loi.py), nhưng KHÔNG đường
    nào được trả True.
    """
    with patch.object(client, "get_by_email",
                      side_effect=client.ElisError("eLIS sập")):
        with pytest.raises(client.ElisError):
            run.is_duplicate({"employeeEmail": "a@fpt.com",
                              "courseName": "Python"})


def test_khac_khoa_thi_KHONG_tinh_la_trung(bat_luat):
    with patch.object(client, "get_by_email",
                      return_value=[_row("a@fpt.com", "Python nâng cao")]):
        assert run.is_duplicate({"employeeEmail": "a@fpt.com",
                                 "courseName": "Python cơ bản"}) is False


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

    def lay(employee_email, page=1, size=1000):
        return [r for r in lich_su
                if r["employeeEmail"].lower() == employee_email.lower()]

    with patch.object(client, "get_pending_list", return_value=items), \
         patch.object(client, "get_by_email", side_effect=lay), \
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
        [_item("A")], [], [_row("hoabd5@fpt.com", "Python cơ bản")])
    assert so_lan_quet == 0, "đã quét LLM cho một ca vốn kết luận được từ eLIS"
    assert len(da_nop) == 1
    assert da_nop[0]["status"] == "REJECTED"
    assert da_nop[0]["comment"] == "Cán bộ nộp trùng khóa học"


def test_ca_trung_KHONG_chan_cac_ca_sau(moi_truong):
    """Nộp trùng là chuyện của riêng một chứng chỉ, không phải sự cố cả lô."""
    da_nop, so_lan_quet = _chay(
        [_item("A", "Python cơ bản"), _item("B", "Java cơ bản")],
        [_duyet()],
        [_row("hoabd5@fpt.com", "Python cơ bản")])
    assert so_lan_quet == 1, "chứng chỉ B phải được quét bình thường"
    assert {d["id"]: d["status"] for d in da_nop} == {"A": "REJECTED", "B": "APPROVED"}


def test_ghi_log_voi_stage_duplicate(moi_truong):
    _chay([_item("A")], [], [_row("hoabd5@fpt.com", "Python cơ bản")])
    conn = sqlite3.connect(moi_truong)
    rows = conn.execute("SELECT verdict, stage, reason, course_name "
                        "FROM process_log WHERE user_course_id='A'").fetchall()
    conn.close()
    assert rows == [("REJECTED", "duplicate", "Cán bộ nộp trùng khóa học",
                     "Python cơ bản")]


def test_duplicate_KHONG_bi_dem_nhu_hong_ky_thuat(moi_truong):
    """Nộp trùng là kết luận nghiệp vụ, không phải sự cố hệ thống. Xếp nhầm
    vào TECHNICAL_STAGES thì vừa thử lại vô ích vừa kéo theo email báo động."""
    _chay([_item("A")], [], [_row("hoabd5@fpt.com", "Python cơ bản")])
    assert "duplicate" not in database.TECHNICAL_STAGES
    assert database.technical_retry_state(["A"], moi_truong) == {}


def test_TAT_luat_thi_van_quet_nhu_cu(moi_truong, monkeypatch):
    monkeypatch.setattr(run.settings, "duplicate_check", False)
    da_nop, so_lan_quet = _chay(
        [_item("A")], [_duyet()], [_row("hoabd5@fpt.com", "Python cơ bản")])
    assert so_lan_quet == 1
    assert da_nop[0]["status"] == "APPROVED"


def test_nop_cung_khoa_HAI_LAN_trong_MOT_vong(moi_truong):
    """Hai bản ghi khác nhau, cùng người cùng khóa, cùng một vòng xử lý.

    eLIS chưa kịp phản ánh lần duyệt đầu, nên `_approved_this_round` là thứ
    duy nhất chặn được cái sau.
    """
    da_nop, so_lan_quet = _chay(
        [_item("A", "Python cơ bản"), _item("B", "Python cơ bản")],
        [_duyet()], [])
    assert so_lan_quet == 1, "bản ghi thứ hai vẫn tốn một lượt LLM"
    assert {d["id"]: d["status"] for d in da_nop} == {"A": "APPROVED", "B": "REJECTED"}


def test_bo_nho_trong_vong_duoc_XOA_giua_cac_vong(moi_truong):
    """Không xóa thì một khóa vừa duyệt bị coi là trùng mãi mãi, kể cả khi
    eLIS đã có dữ liệu thật."""
    _chay([_item("A")], [_duyet()], [])
    assert run._approved_this_round != set()
    _chay([_item("B", "Java cơ bản")], [_duyet()], [])
    assert ("hoabd5@fpt.com", "python co ban") not in run._approved_this_round


# ===== Tên tham số gửi lên API =====

class _Resp:
    status_code = 200

    @staticmethod
    def json():
        return {"data": []}


def test_gui_dung_ten_tham_so_employeeEmail():
    """Canh CHUỖI tên tham số, ở tầng thật sự dựng request.

    Mọi test khác patch client.get_by_email nên gõ nhầm thành employeeId vẫn
    xanh hết, mà API bỏ qua tham số lạ trong im lặng nên eLIS cũng không báo.
    """
    with patch.object(client.requests, "get", return_value=_Resp()) as goi:
        client.get_by_email("hoabd5@fpt.com")

    params = goi.call_args.kwargs["params"]
    assert params["employeeEmail"] == "hoabd5@fpt.com"
    assert "employeeId" not in params


def test_hoi_theo_email_thi_KHONG_gui_kem_status():
    """Cố ý không lọc phía server: bản ghi đã mang sẵn submitStatus, lọc phía
    mình thì không phụ thuộc việc API có tôn trọng tham số hay không."""
    with patch.object(client.requests, "get", return_value=_Resp()) as goi:
        client.get_by_email("hoabd5@fpt.com")
    assert "status" not in goi.call_args.kwargs["params"]


def test_lay_hang_doi_thi_van_gui_status_WAITING():
    with patch.object(client.requests, "get", return_value=_Resp()) as goi:
        client.get_pending_list()
    params = goi.call_args.kwargs["params"]
    assert params["status"] == "WAITING"
    assert "employeeEmail" not in params


def test_duyet_TU_LAU_van_tinh_la_trung(bat_luat):
    """LUẬT NGHIỆP VỤ: một khóa học chỉ được học MỘT LẦN.

    Không có cửa sổ thời gian; ai thêm "chỉ tính trong N tháng" sẽ làm test
    đỏ và phải quay lại hỏi HR.
    """
    cu = _row("a@fpt.com", "Python cơ bản")
    cu["ActionDateTime"] = "2023-01-15T09:00:00.000"
    cu["submitDatetime"] = "2023-01-10T09:00:00.000"

    with patch.object(client, "get_by_email", return_value=[cu]):
        assert run.is_duplicate({"employeeEmail": "a@fpt.com",
                                 "courseName": "Python cơ bản"}) is True
