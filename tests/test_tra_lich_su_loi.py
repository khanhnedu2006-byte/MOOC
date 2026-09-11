"""Test khi KHÔNG tra được lịch sử để kiểm nộp trùng (test_tra_lich_su_loi).

Luật chống nộp trùng phải hỏi eLIS lịch sử của nhân viên. Hỏi không được thì
chưa biết chứng chỉ có trùng hay không, và có ĐÚNG HAI cách xử sai:

  - đi tiếp như không có gì: khóa đã duyệt được duyệt lần hai, tốn lượt LLM,
    và không ai biết vì luật tắt trong im lặng;
  - hoãn tất: hỏng kỹ thuật CHẶN ĐẦU HÀNG, nên một lỗi vĩnh viễn (vd HTTP 400
    vì email dị dạng) làm cả hàng đợi đứng im mãi mãi vì một bản ghi.

Nên phải phân loại: hạ tầng (timeout, mất mạng, 5xx, 408, 429) thì hoãn và
thử lại; lỗi dữ liệu của riêng bản ghi thì bỏ qua luật cho ca đó.
"""

import logging
import pathlib
import sqlite3
import sys
from unittest.mock import patch

import pytest
import requests

GOC = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GOC))
sys.path.insert(0, str(GOC / "src"))

import alert                            # noqa: E402
import client                           # noqa: E402
import run                              # noqa: E402
from database import database, report   # noqa: E402
from schemas import ProcessResult, Verdict   # noqa: E402

logging.disable(logging.CRITICAL)

STAGE = "duplicate_check_error"


@pytest.fixture(autouse=True)
def khong_nghi_giua_cac_lan_thu(monkeypatch):
    """call_with_retry nghỉ 5 giây giữa mỗi lần thử; giữ nguyên thì riêng file
    này chạy mất gần hai phút."""
    monkeypatch.setattr(run.settings, "retry_delay_seconds", 0)


@pytest.fixture
def bat_luat(monkeypatch):
    monkeypatch.setattr(run.settings, "duplicate_check", True)
    run._approved_this_round.clear()
    yield
    run._approved_this_round.clear()


def _loi(status_code=None):
    return client.ElisError(f"eLIS lỗi {status_code}", status_code)


# ===== Phân loại lỗi =====

@pytest.mark.parametrize("code", [None, 500, 502, 503, 504, 408, 429])
def test_HA_TANG_thi_thu_lai_co_ich(code):
    assert client.is_infrastructure(_loi(code)) is True


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422, 200])
def test_LOI_DU_LIEU_thi_thu_lai_vo_ich(code):
    """Gán nhầm nhóm này thành hỏng kỹ thuật là kẹt cả hàng đợi vĩnh viễn."""
    assert client.is_infrastructure(_loi(code)) is False


def test_loi_khong_co_status_code_van_phan_loai_duoc():
    """Lỗi cũ dựng bằng ElisError("...") một tham số vẫn phải chạy được."""
    assert client.is_infrastructure(client.ElisError("cũ")) is True


# ===== client gắn đúng mã =====

class _Resp:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self.text = "lỗi"
        self._body = body or {}

    def json(self):
        return self._body


def test_mat_mang_thi_status_code_la_None():
    """Không nối được thì không có mã HTTP nào cả — đó là dấu hiệu hạ tầng."""
    with patch.object(client.requests, "get",
                      side_effect=requests.ConnectionError("đứt cáp")):
        with pytest.raises(client.ElisError) as e:
            client.get_by_email("a@fpt.com")
    assert e.value.status_code is None


@pytest.mark.parametrize("code", [400, 403, 500, 503])
def test_HTTP_loi_thi_mang_dung_ma(code):
    with patch.object(client.requests, "get", return_value=_Resp(code)):
        with pytest.raises(client.ElisError) as e:
            client.get_by_email("a@fpt.com")
    assert e.value.status_code == code


def test_200_kem_isError_tinh_la_loi_du_lieu():
    """eLIS trả lời được, nó chỉ không đồng ý — thử lại cũng vậy."""
    with patch.object(client.requests, "get",
                      return_value=_Resp(200, {"isError": True,
                                               "message": "sai tham số"})):
        with pytest.raises(client.ElisError) as e:
            client.get_by_email("a@fpt.com")
    assert client.is_infrastructure(e.value) is False


def test_timeout_lay_tu_cau_hinh_chu_khong_hardcode(monkeypatch):
    """TIMEOUT_SECONDS hiện ra ở tab cấu hình của app; không nối vào đây thì
    người vận hành chỉnh xong tưởng đã nới timeout."""
    monkeypatch.setattr(client.settings, "timeout_seconds", 17)
    with patch.object(client.requests, "get",
                      return_value=_Resp(200, {"data": []})) as goi:
        client.get_by_email("a@fpt.com")
    assert goi.call_args.kwargs["timeout"] == 17


# ===== is_duplicate =====

def test_ha_tang_hong_thi_NEM_RA_chu_khong_doan(bat_luat):
    with patch.object(client, "get_by_email", side_effect=_loi(503)):
        with pytest.raises(client.ElisError):
            run.is_duplicate({"employeeEmail": "a@fpt.com",
                              "courseName": "Python"})


def test_loi_du_lieu_thi_BO_QUA_luat_cho_rieng_ca_nay(bat_luat):
    """Ném ra thì bản ghi này chặn đầu hàng mãi mãi mà thử lại không bao giờ
    khỏi. Thà lọt một ca trùng còn hơn kẹt cả hàng đợi."""
    with patch.object(client, "get_by_email", side_effect=_loi(400)):
        assert run.is_duplicate({"employeeEmail": "a@fpt.com",
                                 "courseName": "Python"}) is False


# ===== Chạy cả vòng =====

def _item(uc_id: str, course: str = "Python cơ bản",
          email: str = "hoabd5@fpt.com") -> dict:
    return {"id": uc_id, "certificate_id": f"c-{uc_id}", "courseId": f"k-{course}",
            "employeeId": "00332383", "employeeName": "Bùi Đức Hòa",
            "employeeEmail": email, "courseName": course,
            "providerName": "Coursera"}


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


def _chay(items, tra_lich_su):
    """Chạy một vòng. Trả về (DTO đã nộp, số lần quét bằng LLM)."""
    da_nop, da_quet = [], []

    def nop(dtos):
        da_nop.extend(dtos)
        return {"successList": [{"id": d["id"]} for d in dtos], "failList": []}

    def quet(*a):
        da_quet.append(a)
        return ProcessResult(verdict=Verdict.APPROVED, reason="Khớp",
                             stage="llm1")

    with patch.object(client, "get_pending_list", return_value=items), \
         patch.object(client, "get_by_email", side_effect=tra_lich_su), \
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


def _log(db, uc_id):
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT verdict, stage, reason FROM process_log "
                        "WHERE user_course_id=?", (uc_id,)).fetchall()
    conn.close()
    return rows


def test_ha_tang_hong_thi_HOAN_chu_khong_nop_gi(moi_truong):
    da_nop, so_lan_quet = _chay([_item("A")], _loi(503))

    assert da_nop == [], "đã nộp kết quả cho ca chưa kiểm được nộp trùng"
    assert so_lan_quet == 0, "đốt lượt LLM cho ca sẽ phải làm lại"

    rows = _log(moi_truong, "A")
    assert len(rows) == 1
    verdict, stage, reason = rows[0]
    assert verdict == Verdict.WAITING.value
    assert stage == STAGE
    assert "lịch sử" in reason


def test_ha_tang_hong_thi_CHAN_ca_phia_sau(moi_truong):
    """eLIS hỏng thì hỏng cho mọi người; chạy tiếp chỉ đốt LLM cho những ca
    rồi cũng phải làm lại."""
    _, so_lan_quet = _chay([_item("A"), _item("B", "Java cơ bản")], _loi(503))
    assert so_lan_quet == 0


def test_loi_du_lieu_thi_KHONG_chan_hang_doi(moi_truong):
    """Bản ghi lỗi 4xx vẫn đi tiếp và ca sau vẫn được xử lý."""
    da_nop, so_lan_quet = _chay([_item("A"), _item("B", "Java cơ bản")],
                                _loi(400))
    assert so_lan_quet == 2
    assert {d["id"] for d in da_nop} == {"A", "B"}
    assert _log(moi_truong, "A")[0][1] != STAGE


def test_hoan_roi_thi_DEM_va_canh_bao_nhu_moi_hong_ky_thuat(moi_truong,
                                                            monkeypatch):
    """Đây là điểm khác biệt so với chỉ ghi log: bộ đếm theo TỪNG chứng chỉ
    nằm trong DB nên leo qua các vòng và chắc chắn chạm ngưỡng."""
    monkeypatch.setattr(run.settings, "technical_retry_cooldown_minutes", 0)
    for _ in range(3):
        _chay([_item("A")], _loi(503))

    so_lan, _ = database.technical_retry_state(["A"], moi_truong)["A"]
    assert so_lan == 3

    _, _, needs_alert = run.filter_queue([_item("A")], ignore_cooldown=True)
    assert [i["id"] for i, _ in needs_alert] == ["A"]


def test_eLIS_song_lai_thi_tu_xu_ly_tiep(moi_truong, monkeypatch):
    """Không có nhánh bỏ cuộc: sự cố khỏi thì chứng chỉ chạy bình thường."""
    monkeypatch.setattr(run.settings, "technical_retry_cooldown_minutes", 0)
    _chay([_item("A")], _loi(503))
    da_nop, so_lan_quet = _chay([_item("A")], lambda *a, **k: [])
    assert so_lan_quet == 1
    assert da_nop[0]["status"] == "APPROVED"


# ===== Stage phải được khai ở CẢ BA nơi =====

def test_stage_nam_trong_TECHNICAL_STAGES():
    """Thiếu thì không được đếm, không cooldown, và bị quét lại mỗi 5 giây."""
    assert STAGE in database.TECHNICAL_STAGES


def test_stage_co_nhan_trong_bao_cao():
    """Thiếu thì báo cáo in "Khác (duplicate_check_error)"."""
    assert STAGE in report.FAILURE_GROUPS


def test_stage_co_giai_thich_trong_thu_canh_bao():
    """Người nhận thư là người vận hành, không phải người viết code."""
    assert STAGE in alert.STAGE_DESCRIPTION
