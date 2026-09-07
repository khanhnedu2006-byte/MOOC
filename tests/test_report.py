"""Test cho báo cáo (test_report).

Trọng tâm là MỘT rủi ro cụ thể: chuỗi lý do từ chối được sinh ở
pipeline._mismatch_reason() nhưng được ĐỌC LẠI bằng cách so chuỗi ở
report.rejection_causes(). Hai chỗ này không có ràng buộc nào ở mức ngôn ngữ.

Sửa "Tên không khớp" thành "Tên không trùng" ở pipeline mà quên sửa report
thì KHÔNG có lỗi nào được ném ra — báo cáo chỉ lặng lẽ đếm ra 0, và người
đọc kết luận rằng tháng này không ai sai tên. Test này biến lỗi ngầm đó
thành lỗi ồn ào.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GOC))
sys.path.insert(0, str(GOC / "src"))

import pipeline                             # noqa: E402
from database import database, report       # noqa: E402
from schemas import ExtractedInfo, InputInfo  # noqa: E402


# ===== Ràng buộc giữa pipeline và report =====

def test_chuoi_ly_do_khop_giua_pipeline_va_report():
    """Mọi chuỗi report đi tìm phải THẬT SỰ được pipeline sinh ra."""
    # Ép cả ba tiêu chí cùng sai để lấy đủ ba chuỗi trong một lần gọi.
    trich = ExtractedInfo(recipient_name="Nguyen Van B",
                          certificate_name="Khóa học khác",
                          issue_date="01/01/1999")
    nhap = InputInfo(employee_name="Bui Duc Hoa",
                     course_name="ISO 27001", employee_code="hoabd3")
    reason = pipeline._mismatch_reason(trich, nhap, "test")

    for text, _nhan in report.REJECTION_CAUSES:
        assert text in reason, (
            f"report.REJECTION_CAUSES tìm chuỗi {text!r} nhưng "
            f"pipeline._mismatch_reason() sinh ra {reason!r}. "
            f"Đổi chuỗi ở một bên thì phải đổi cả hai."
        )


def test_moi_tieu_che_sai_deu_sinh_dung_mot_chuoi():
    """Sai đúng một tiêu chí thì lý do chỉ chứa đúng chuỗi của tiêu chí đó."""
    nhap = InputInfo(employee_name="Bui Duc Hoa",
                     course_name="ISO 27001", employee_code="hoabd3")
    dung = dict(recipient_name="Bui Duc Hoa", certificate_name="ISO 27001",
                issue_date="15/03/2026")

    ca = {
        "Tên không khớp": {**dung, "recipient_name": "Nguyen Van B"},
        "Tên khóa học không khớp": {**dung, "certificate_name": "Khóa khác"},
        "Ngày không hợp lệ": {**dung, "issue_date": "01/01/1999"},
    }
    for mong_doi, truong in ca.items():
        reason = pipeline._mismatch_reason(
            ExtractedInfo(**truong), nhap, "test")
        assert mong_doi in reason
        khac = [c for c, _ in report.REJECTION_CAUSES if c != mong_doi]
        for c in khac:
            assert c not in reason, f"{truong} lẽ ra chỉ sai {mong_doi}, nhận {reason!r}"


# ===== Đếm nguyên nhân =====

@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "t.db")
    database.init_db(p)
    return p


def _them(db_path, rows):
    conn = sqlite3.connect(db_path)
    for i, (verdict, stage, reason) in enumerate(rows):
        conn.execute(
            "INSERT INTO process_log (created_at,user_course_id,verdict,stage,reason)"
            " VALUES (?,?,?,?,?)",
            (f"2026-08-10T09:{i:02d}:00", f"uc{i}", verdict, stage, reason))
    conn.commit()
    conn.close()


def test_dem_tung_nguyen_nhan(db):
    _them(db, [
        ("REJECTED", "llm1", "Tên không khớp"),
        ("REJECTED", "llm2", "Tên khóa học không khớp"),
        ("REJECTED", "llm1", "Ngày không hợp lệ"),
    ])
    rc = report.rejection_causes("2026-08-10", "2026-08-10", db)
    dem = {m["label"]: m["count"] for m in rc["causes"]}
    assert dem["Sai tên"] == 1
    assert dem["Sai tên khóa học"] == 1
    assert dem["Sai ngày"] == 1
    assert rc["multi_cause"] == 0


def test_mot_ca_sai_nhieu_tieu_chi_duoc_dem_o_moi_nguyen_nhan(db):
    """Đây là điểm dễ hiểu nhầm nhất của bảng này, nên phải có test."""
    _them(db, [
        ("REJECTED", "llm1",
         "Tên không khớp; Tên khóa học không khớp; Ngày không hợp lệ"),
    ])
    rc = report.rejection_causes("2026-08-10", "2026-08-10", db)
    dem = {m["label"]: m["count"] for m in rc["causes"]}
    assert dem["Sai tên"] == dem["Sai tên khóa học"] == dem["Sai ngày"] == 1
    assert rc["total_rejected"] == 1
    assert rc["multi_cause"] == 1
    # Tổng nguyên nhân (3) > số ca (1). Có chủ đích, và multi_cause là thứ
    # để chỗ hiển thị giải thích chênh lệch đó.
    assert sum(dem.values()) > rc["total_rejected"]


def test_hong_ky_thuat_khong_tinh_vao_ly_do_tu_choi(db):
    _them(db, [
        ("REJECTED", "download_error", "Không tải được file từ eLIS: 502"),
        ("REJECTED", "llm1_error", "Lỗi gọi AI"),
        ("REJECTED", "llm1", "Tên không khớp"),
    ])
    rc = report.rejection_causes("2026-08-10", "2026-08-10", db)
    assert rc["total_rejected"] == 1, "ca hỏng kỹ thuật lọt vào thống kê nghiệp vụ"
    assert {m["label"]: m["count"] for m in rc["causes"]}["Sai tên"] == 1


def test_ly_do_la_gom_vao_khong_ro(db):
    _them(db, [("REJECTED", "llm1", "Không khớp"),
               ("REJECTED", "llm1", None)])
    rc = report.rejection_causes("2026-08-10", "2026-08-10", db)
    dem = {m["label"]: m["count"] for m in rc["causes"]}
    assert dem["Không rõ nguyên nhân"] == 2, "lý do lạ bị bỏ im lặng"


# ===== Phạm vi báo cáo =====

def test_bao_cao_loai_ca_hong_ky_thuat_khoi_moi_con_so(db):
    _them(db, [
        ("APPROVED", "llm1", None),
        ("REJECTED", "llm1", "Tên không khớp"),
        ("REJECTED", "download_error", "eLIS 502"),
        ("REJECTED", "llm1_error", "AI lỗi"),
    ])
    r = report.period_report("2026-08-10", "2026-08-10", "day", db)
    assert r["total"] == 2, "ca hỏng kỹ thuật vẫn bị tính vào tổng"
    assert r["approved"] == 1
    assert r["rejected"] == 1
    assert r["excluded_technical"] == 2
    assert r["approval_rate"] == 50.0
    # Biểu đồ và bảng nhà cung cấp phải CÙNG phạm vi, nếu không thì các con
    # số trong cùng một báo cáo không cộng ra bằng nhau.
    assert sum(p["total"] for p in r["trend"]) == 2
    assert sum(p["total"] for p in r["by_provider"]) == 2


# =====================================================================
# Lịch gửi báo cáo
# =====================================================================

from datetime import datetime  # noqa: E402

import scheduler  # noqa: E402
from config import settings  # noqa: E402


@pytest.fixture
def lich(tmp_path, monkeypatch):
    """Cô lập file trạng thái để test không đụng file thật."""
    monkeypatch.setattr(scheduler, "STATE_FILE", tmp_path / "s.json")
    monkeypatch.setattr(settings, "report_time", "18:00")
    monkeypatch.setattr(settings, "report_weekday", 4)   # Thứ Sáu
    monkeypatch.setattr(settings, "report_monthday", 1)
    return lambda khi: scheduler.check_and_send(
        datetime.fromisoformat(khi), really_send=False)


def test_off_khong_bao_gio_gui(lich, monkeypatch):
    monkeypatch.setattr(settings, "report_schedule", "off")
    assert lich("2026-08-21T18:00") is None


def test_weekly_gui_dung_gio_va_khong_gui_lai(lich, monkeypatch):
    monkeypatch.setattr(settings, "report_schedule", "weekly")
    assert lich("2026-08-21T18:00") == "weekly:2026-W34"   # Thứ Sáu
    assert lich("2026-08-21T20:00") is None
    assert lich("2026-08-24T09:00") is None                # Thứ Hai


def test_weekly_gui_bu_khi_job_tat_dung_hom_toi_han(lich, monkeypatch):
    """Đây là ca mà bản đầu tiên làm SAI: mất hẳn báo cáo tuần đó.

    Job không chạy hôm Thứ Sáu (máy tắt, container restart). Bật lại Thứ Bảy
    thì PHẢI gửi bù, và phải mang đúng khóa của tuần đó để tuần sau vẫn gửi
    bình thường.
    """
    monkeypatch.setattr(settings, "report_schedule", "weekly")
    assert lich("2026-08-22T09:00") == "weekly:2026-W34", "không gửi bù"
    assert lich("2026-08-23T09:00") is None, "gửi bù xong còn gửi lại"
    assert lich("2026-08-28T18:00") == "weekly:2026-W35", "tuần sau không gửi"


def test_khoang_bao_cao_cua_ky_gui_bu_van_dung(lich, monkeypatch):
    """Gửi bù không được làm lệch khoảng số liệu."""
    tu, end, key = scheduler._report_period(
        "weekly", datetime.fromisoformat("2026-08-21T18:00"))
    assert (tu, end) == ("2026-08-15", "2026-08-21")
    assert key == "weekly:2026-W34"


def test_monthly_bao_cao_thang_TRUOC(lich, monkeypatch):
    monkeypatch.setattr(settings, "report_schedule", "monthly")
    assert lich("2026-09-05T10:00") == "monthly:2026-08", "không gửi bù tháng 8"
    tu, end, _ = scheduler._report_period(
        "monthly", datetime.fromisoformat("2026-09-05T10:00"))
    assert (tu, end) == ("2026-08-01", "2026-08-31")


def test_monthday_31_khong_lam_ket_lich(lich, monkeypatch):
    """monthday=31 phải bị ép về 28, nếu không tháng Hai không bao giờ tới hạn."""
    monkeypatch.setattr(settings, "report_schedule", "monthly")
    monkeypatch.setattr(settings, "report_monthday", 31)
    assert lich("2026-03-01T18:00") is not None


def test_khong_gui_don_nhieu_ky_mot_luc(lich, monkeypatch):
    """Job tắt lâu rồi bật lại: nhận MỘT báo cáo, không phải một chồng."""
    monkeypatch.setattr(settings, "report_schedule", "weekly")
    da_gui = [k for d in range(1, 29)
              if (k := lich(f"2026-09-{d:02d}T09:00"))]
    # 1 lần gửi bù lúc bật + đúng 4 Thứ Sáu trong khoảng.
    assert len(da_gui) == 5, da_gui
    assert len(set(da_gui)) == len(da_gui), "có kỳ bị gửi trùng"


def test_cau_hinh_sai_khong_lam_chet_job(lich, monkeypatch):
    monkeypatch.setattr(settings, "report_schedule", "hangngay")
    assert lich("2026-08-21T18:00") is None
    monkeypatch.setattr(settings, "report_schedule", "daily")
    monkeypatch.setattr(settings, "report_time", "25:99")
    assert lich("2026-08-21T23:00") is not None   # lùi về 18:00, vẫn gửi


def test_gui_hong_khong_thu_lai_lien_tuc(lich, monkeypatch, tmp_path):
    """Mật khẩu sai KHÔNG được thành 12 lần đăng nhập Gmail mỗi phút.

    Vòng lặp chính chạy mỗi 5 giây. Không có giãn cách thì Gmail khóa tài
    khoản vì nghi brute-force, và log ngập traceback tới mức che hết thông
    tin thật. Đây là lỗi đã xảy ra ở bản đầu, nên phải có test canh.
    """
    import sys
    import types
    from datetime import timedelta

    monkeypatch.setattr(settings, "report_schedule", "daily")
    dem = {"n": 0}

    def gui_hong(*a, **k):
        dem["n"] += 1
        raise RuntimeError("535 Username and Password not accepted")

    fake = types.ModuleType("send_report")
    fake.send_period_report = gui_hong
    monkeypatch.setitem(sys.modules, "send_report", fake)

    t0 = datetime.fromisoformat("2026-08-24T18:00:00")
    for i in range(12 * 60):                       # 1 giờ, mỗi 5 giây
        scheduler.check_and_send(t0 + timedelta(seconds=5 * i))

    assert dem["n"] <= 6, f"thử lại {dem['n']} lần trong 1 giờ — quá dày"
    assert dem["n"] >= 2, "không thử lại lần nào — lỗi tạm thời sẽ không tự khỏi"


def test_gui_lai_duoc_sau_khi_loi_tam_thoi_het(lich, monkeypatch):
    """Mạng chập rồi tốt lại thì báo cáo vẫn phải đi, không bị bỏ luôn."""
    import sys
    import types
    from datetime import timedelta

    monkeypatch.setattr(settings, "report_schedule", "daily")
    t0 = datetime.fromisoformat("2026-08-24T18:00:00")
    bucket = [t0]

    def gui(*a, **k):
        if (bucket[0] - t0) < timedelta(minutes=10):
            raise RuntimeError("mạng chập")

    fake = types.ModuleType("send_report")
    fake.send_period_report = gui
    monkeypatch.setitem(sys.modules, "send_report", fake)

    da_gui = None
    for i in range(12 * 60):
        bucket[0] = t0 + timedelta(seconds=5 * i)
        if (k := scheduler.check_and_send(bucket[0])):
            da_gui = k
            break
    assert da_gui == "daily:2026-08-24", "lỗi tạm thời làm mất luôn báo cáo"


def test_daily_bao_cao_7_ngay_de_bieu_do_co_nghia(lich, monkeypatch):
    """daily = gửi mỗi ngày, nội dung là 7 ngày gần nhất.

    Bản đầu cho daily = đúng một ngày, nên biểu đồ đường chỉ có MỘT điểm —
    không vẽ được xu hướng gì, và con số một ngày cũng không cho biết nó cao
    hay thấp so với bình thường.
    """
    tu, end, key = scheduler._report_period(
        "daily", datetime.fromisoformat("2026-08-20T18:00"))
    assert (tu, end) == ("2026-08-14", "2026-08-20")
    assert key == "daily:2026-08-20", "khóa phải theo NGÀY -> đúng 1 thư/ngày"


def test_bucket_qua_tho_bi_ha_xuong(lich):
    """REPORT_BUCKET=week với kỳ 7 ngày cho ra đúng một cột — phải tự hạ."""
    assert scheduler._sensible_bucket("week", "2026-08-14", "2026-08-20", "daily") == "day"
    assert scheduler._sensible_bucket("month", "2026-08-14", "2026-08-20", "daily") == "day"
    # Kỳ đủ dài thì giữ nguyên.
    assert scheduler._sensible_bucket("week", "2026-06-01", "2026-08-31", "monthly") == "week"
    assert scheduler._sensible_bucket("day", "2026-08-14", "2026-08-20", "daily") == "day"
    # Giá trị lạ không được làm chết job.
    assert scheduler._sensible_bucket("nam", "2026-08-14", "2026-08-20", "daily") == "day"


def test_khong_ve_bieu_do_khi_ky_qua_ngan(db):
    """Một mốc thì không dựng ảnh nào — biểu đồ một điểm chỉ làm dài báo cáo."""
    import report_layout
    _them(db, [("APPROVED", "llm1", None), ("REJECTED", "llm1", "Tên không khớp")])
    r = report.period_report("2026-08-10", "2026-08-10", "day", db)
    html_body, images = report_layout.build_html(r)
    assert images == [], "vẫn dựng ảnh cho kỳ một mốc"
    assert "cid:" not in html_body, "HTML còn trỏ tới ảnh không tồn tại"
    # Nhưng số liệu thì vẫn phải đủ.
    assert "Lý do từ chối" in html_body


# =====================================================================
# Đọc tham số ngày từ dòng lệnh
# =====================================================================

def test_doc_ngay_chuan_hoa_ve_yyyy_mm_dd():
    """Thiếu số 0 PHẢI được chuẩn hóa, không chỉ được chấp nhận.

    Truy vấn so ngày bằng CHUỖI (substr(created_at,1,10) BETWEEN ...), nên
    '2026-8-25' không khớp '2026-08-25' trong DB — báo cáo ra rỗng mà không
    có lỗi nào để lần ra.
    """
    import send_report
    assert send_report._parse_day("2026-8-25", "--day") == "2026-08-25"
    assert send_report._parse_day("2026-08-25", "--day") == "2026-08-25"
    assert send_report._parse_day("2026/8/5", "--day") == "2026-08-05"
    assert send_report._parse_day(" 2026-08-05 ", "--day") == "2026-08-05"


def test_doc_ngay_sai_bao_loi_ro_khong_traceback():
    import send_report
    for xau in ("hôm nay", "25-08-2026", "2026-13-45", "", "2026-08"):
        with pytest.raises(SystemExit) as e:
            send_report._parse_day(xau, "--day")
        assert "YYYY-MM-DD" in str(e.value), f"thông báo lỗi không nói định dạng: {xau!r}"
