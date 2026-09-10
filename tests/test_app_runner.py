"""Test phần chạy job của app desktop (test_app_runner).

VÌ SAO FILE NÀY TỒN TẠI: `app_runner.JobRunner` KHÔNG gọi thẳng
`run.process_one_round` rồi thôi. Nó tự gọi API ① để biết hàng đợi dài bao
nhiêu — mà tự gọi API thì phải TỰ GHI SỔ `alert.api_failed/api_succeeded`,
đúng như `run.py` làm. Quên ghi sổ thì email cảnh báo sai: eLIS chết mà không
ai được báo, hoặc eLIS sống lại mà bộ đếm lỗi không được xóa.

Không có test nào ở đây gọi mạng, gọi LLM, hay động vào giao diện. Đây cũng
là lý do JobRunner nằm ở src/app_runner.py chứ không nằm trong main_app.py:
main_app.py `import tkinter`, máy CI Linux thường không có.
"""

import app_runner
import pytest

import run


class FakeRoundResult:
    """Đủ giống RoundResult cho phần app_runner đọc tới."""

    def __init__(self, scanned=0, accepted=0, deferred=0):
        self.scanned_count = scanned
        self.accepted_count = accepted
        self.deferred_count = deferred


@pytest.fixture
def runner(monkeypatch):
    """JobRunner với mọi thứ bên ngoài thay bằng bản giả, kèm sổ ghi lời gọi."""
    ghi = {"api_failed": [], "api_succeeded": [], "scheduler": [],
           "process": [], "counts": {"REJECTED": 100, "APPROVED": 900}}

    monkeypatch.setattr(app_runner.alert, "api_failed",
                        lambda code, reason: ghi["api_failed"].append((code, reason)))
    monkeypatch.setattr(app_runner.alert, "api_succeeded",
                        lambda code: ghi["api_succeeded"].append(code))
    monkeypatch.setattr(app_runner.scheduler, "check_and_send",
                        lambda: ghi["scheduler"].append(1))
    monkeypatch.setattr(app_runner.database, "skipped_ids", lambda ids: set())
    monkeypatch.setattr(app_runner.database, "count_by_verdict",
                        lambda: dict(ghi["counts"]))

    def gia_process(azure_client, items=None, ignore_cooldown=False):
        ghi["process"].append((items, ignore_cooldown))
        return FakeRoundResult(scanned=len(items or []), accepted=len(items or []))

    monkeypatch.setattr(app_runner.run, "process_one_round", gia_process)

    job = app_runner.JobRunner(app_runner.SharedState())
    job.ghi = ghi
    return job


def _cam_hang_doi(monkeypatch, items):
    monkeypatch.setattr(app_runner.client, "get_pending_list",
                        lambda page=1, size=100: list(items))


def _cam_hang_doi_hong(monkeypatch, message="502 Bad Gateway"):
    def no(page=1, size=100):
        raise app_runner.client.ElisError(message)
    monkeypatch.setattr(app_runner.client, "get_pending_list", no)
    # call_with_retry ngủ giữa các lần thử; test không cần chờ thật.
    monkeypatch.setattr(app_runner.run.time, "sleep", lambda s: None)


# --------------------------------------------------- ghi sổ cho cảnh báo

def test_API1_chay_duoc_thi_XOA_bo_dem_loi(runner, monkeypatch):
    """Thiếu api_succeeded thì bộ đếm lỗi không bao giờ về 0: eLIS đã sống
    lại từ lâu mà hệ thống vẫn gửi thư báo động."""
    _cam_hang_doi(monkeypatch, [{"id": "a", "employeeEmail": "x@fpt.com"}])
    runner._one_round(object(), False)
    assert runner.ghi["api_succeeded"] == [1]
    assert runner.ghi["api_failed"] == []


def test_API1_hong_thi_GHI_SO_dung_ma_API(runner, monkeypatch):
    """Ghi nhầm mã API thì thư cảnh báo chỉ sai chỗ hỏng."""
    _cam_hang_doi_hong(monkeypatch)
    runner._one_round(object(), False)
    assert len(runner.ghi["api_failed"]) == 1
    assert runner.ghi["api_failed"][0][0] == 1
    assert runner.ghi["api_succeeded"] == []


def test_API1_hong_thi_KHONG_goi_process_one_round(runner, monkeypatch):
    """Không lấy được hàng đợi thì vòng này không được xử lý gì cả."""
    _cam_hang_doi_hong(monkeypatch)
    runner._one_round(object(), False)
    assert runner.ghi["process"] == []
    data = runner.state.snapshot()
    assert data["phase"] == "error"
    assert data["queue_size"] == 0
    assert "502" in str(data["api_error"])


def test_chay_lai_duoc_thi_xoa_api_error(runner, monkeypatch):
    """Lỗi cũ phải biến mất khỏi giao diện khi eLIS sống lại."""
    _cam_hang_doi_hong(monkeypatch)
    runner._one_round(object(), False)
    assert runner.state.snapshot()["api_error"]

    _cam_hang_doi(monkeypatch, [{"id": "a", "employeeEmail": "x@fpt.com"}])
    runner._one_round(object(), False)
    assert runner.state.snapshot()["api_error"] is None


# --------------------------------------------------- các con số

def test_hang_doi_va_so_nhan_vien_dem_dung(runner, monkeypatch):
    """Hai người, ba chứng chỉ — không được đếm thành ba người."""
    _cam_hang_doi(monkeypatch, [
        {"id": "1", "employeeEmail": "a@fpt.com"},
        {"id": "2", "employeeEmail": "A@FPT.COM"},   # cùng người, khác kiểu chữ
        {"id": "3", "employeeEmail": "b@fpt.com"},
    ])
    runner._one_round(object(), False)
    data = runner.state.snapshot()
    assert data["queue_size"] == 3
    assert data["employee_count"] == 2


def test_o_TU_CHOI_dem_tu_luc_mo_app_chu_khong_phai_ca_lich_su(runner, monkeypatch):
    """DB đã có 100 bản REJECTED từ trước. Mở app lên phải hiện 0, không phải
    100 — vì ô ngay bên cạnh ("eLIS đã nhận") đếm từ lúc mở app. Hai số cạnh
    nhau mà khác mốc thời gian thì người đọc trừ nhẩm ra kết luận sai."""
    runner.rejected_at_start = runner.ghi["counts"]["REJECTED"]   # 100
    _cam_hang_doi(monkeypatch, [{"id": "1", "employeeEmail": "a@fpt.com"}])

    runner._one_round(object(), False)
    assert runner.state.snapshot()["rejected_total"] == 0

    runner.ghi["counts"]["REJECTED"] = 103       # thêm 3 ca bị từ chối
    runner._one_round(object(), False)
    assert runner.state.snapshot()["rejected_total"] == 3


def test_so_cong_don_khong_bi_dat_lai_sau_moi_vong(runner, monkeypatch):
    _cam_hang_doi(monkeypatch, [{"id": "1", "employeeEmail": "a@fpt.com"},
                                {"id": "2", "employeeEmail": "b@fpt.com"}])
    runner._one_round(object(), False)
    runner._one_round(object(), False)
    assert runner.state.snapshot()["accepted_total"] == 4


def test_hang_doi_rong_van_dem_ve_0_chu_khong_giu_so_cu(runner, monkeypatch):
    _cam_hang_doi(monkeypatch, [{"id": "1", "employeeEmail": "a@fpt.com"}])
    runner._one_round(object(), False)
    assert runner.state.snapshot()["queue_size"] == 1

    _cam_hang_doi(monkeypatch, [])
    runner._one_round(object(), False)
    assert runner.state.snapshot()["queue_size"] == 0


# --------------------------------------------------- các việc kèm theo

def test_moi_vong_deu_kiem_lich_bao_cao(runner, monkeypatch):
    """Thiếu chỗ này thì báo cáo định kỳ im lặng không bao giờ gửi — đúng như
    run_forever gọi scheduler sau mỗi vòng."""
    _cam_hang_doi(monkeypatch, [{"id": "1", "employeeEmail": "a@fpt.com"}])
    runner._one_round(object(), False)
    runner._one_round(object(), False)
    assert len(runner.ghi["scheduler"]) == 2


def test_lich_bao_cao_hong_KHONG_lam_hong_vong_xu_ly(runner, monkeypatch):
    """Việc phụ không được phép giết việc chính."""
    _cam_hang_doi(monkeypatch, [{"id": "1", "employeeEmail": "a@fpt.com"}])

    def no():
        raise RuntimeError("SMTP chết")
    monkeypatch.setattr(app_runner.scheduler, "check_and_send", no)

    runner._one_round(object(), False)      # không được ném ra ngoài
    assert runner.state.snapshot()["accepted_total"] == 1


def test_db_hong_KHONG_lam_hong_vong_xu_ly(runner, monkeypatch):
    """mooc_log.db bị khóa thì mất mấy con số trên màn hình, nhưng chứng chỉ
    vẫn phải được xử lý."""
    _cam_hang_doi(monkeypatch, [{"id": "1", "employeeEmail": "a@fpt.com"}])

    def no(*a, **k):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(app_runner.database, "skipped_ids", no)
    monkeypatch.setattr(app_runner.database, "count_by_verdict", no)

    runner._one_round(object(), False)
    assert runner.ghi["process"], "chứng chỉ phải vẫn được xử lý"


def test_truyen_thang_items_sang_process_one_round(runner, monkeypatch):
    """App gọi API ① rồi ĐƯA LẠI danh sách đó. Nếu không truyền `items`,
    process_one_round sẽ tự gọi API ① lần nữa — mỗi vòng hai request."""
    items = [{"id": "1", "employeeEmail": "a@fpt.com"}]
    _cam_hang_doi(monkeypatch, items)
    runner._one_round(object(), False)
    truyen, _ = runner.ghi["process"][0]
    assert truyen == items


def test_bo_qua_gian_cach_duoc_truyen_dung(runner, monkeypatch):
    """Nút "Thử lại ca đang hoãn" phải thật sự tới được process_one_round."""
    _cam_hang_doi(monkeypatch, [{"id": "1", "employeeEmail": "a@fpt.com"}])
    runner._one_round(object(), True)
    assert runner.ghi["process"][0][1] is True


# --------------------------------------------------- điều khiển luồng

def test_tam_dung_va_chay_tiep(runner):
    runner.pause()
    assert runner.state.snapshot()["phase"] == "paused"
    assert not runner.resume_event.is_set()

    runner.resume()
    assert runner.resume_event.is_set()


def test_dung_han_thi_go_ca_hai_chot_cho(runner):
    """stop() phải mở cả resume_event lẫn wake_event, không thì luồng nằm chờ
    mãi ở wait() và app không bao giờ đóng được."""
    runner.pause()
    runner.stop()
    assert runner.stop_event.is_set()
    assert runner.resume_event.is_set()
    assert runner.wake_event.is_set()


def test_run_thoat_ngay_khi_khong_tao_duoc_client_azure(runner, monkeypatch):
    """Sai key Azure là hỏng ngay từ đầu, lặp lại mỗi 5 giây cũng vô ích."""
    def no():
        raise RuntimeError("401 Unauthorized")
    monkeypatch.setattr(app_runner.ocr_azure, "create_client", no)

    runner.run()        # chạy thẳng trong luồng test, phải trả về ngay
    data = runner.state.snapshot()
    assert data["phase"] == "error"
    assert "401" in str(data["fatal"])


def test_khong_dung_toi_tkinter():
    """app_runner phải sạch Tk. Lẫn vào là bộ test trên CI Linux gãy ngay ở
    bước thu thập, trước khi chạy test nào.

    Đọc bằng ast chứ không phải tìm chuỗi: chính docstring của app_runner.py
    có chữ "tkinter" để giải thích vì sao file này tồn tại, nên tìm chuỗi thì
    test đỏ vì một dòng chú thích."""
    import ast
    import pathlib

    cay = ast.parse(pathlib.Path(app_runner.__file__).read_text(encoding="utf-8"))
    nap = set()
    for node in ast.walk(cay):
        if isinstance(node, ast.Import):
            nap.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            nap.add(node.module.split(".")[0])
    assert "tkinter" not in nap, f"app_runner đang import tkinter: {sorted(nap)}"


def test_run_module_van_dung_ham_that():
    """Chốt cho fixture: nếu run.process_one_round bị đổi tên, fixture ở trên
    lặng lẽ dựng một hàm giả cho một tên không còn ai gọi."""
    assert callable(run.process_one_round)
    assert callable(run.call_with_retry)
