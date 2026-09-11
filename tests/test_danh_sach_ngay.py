"""Test danh sách chứng chỉ trong thư báo cáo (test_danh_sach_ngay).

Thư gửi lúc 18h ngày N phải liệt kê mọi chứng chỉ đã xử lý từ 00:00 tới 18h
của chính ngày N, và ghi rõ đó là ngày N.

Hai chỗ dễ sai:
  - lấy nhầm sang ngày khác (hôm qua, hoặc cả kỳ 7 ngày);
  - lọc mất ca hỏng kỹ thuật và ca bỏ qua. Các con số tổng hợp cố ý loại
    chúng, nhưng DANH SÁCH thì phải có: người đọc cần thấy ca máy chưa kết
    luận được.
"""

import sqlite3

import pytest

import report_layout
from database import database, report

HOM_NAY = "2026-09-11"
HOM_QUA = "2026-09-10"


def _ghi(db, created_at, code, course, verdict, stage, reason="", sent=1):
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO process_log (created_at, employee_code, name_on_image, "
        "course_name, verdict, reason, stage, elis_sent_ok, provider) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (created_at, code, code.upper(), course, verdict, reason, stage,
         sent, "Coursera"))
    conn.commit()
    conn.close()


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "mooc_log.db")
    database.init_db(path)
    return path


def test_lay_dung_ngay_duoc_hoi(db):
    _ghi(db, f"{HOM_QUA}T17:00:00", "hom_qua", "A", "APPROVED", "llm1")
    _ghi(db, f"{HOM_NAY}T08:00:00", "hom_nay", "B", "APPROVED", "llm1")

    kq = report.certificates_on_day(HOM_NAY, db_path=db)
    assert kq["day"] == HOM_NAY
    assert [r["employee"] for r in kq["rows"]] == ["hom_nay"]


def test_lay_het_tu_dau_ngay_toi_gio_gui(db):
    """Gửi lúc 18h thì ca 00:05 và ca 17:59 đều phải có."""
    _ghi(db, f"{HOM_NAY}T00:05:00", "sang_som", "A", "APPROVED", "llm1")
    _ghi(db, f"{HOM_NAY}T17:59:00", "sat_gio", "B", "REJECTED", "llm2")

    kq = report.certificates_on_day(HOM_NAY, db_path=db)
    assert kq["total"] == 2
    assert {r["employee"] for r in kq["rows"]} == {"sang_som", "sat_gio"}


def test_moi_nhat_dung_truoc(db):
    _ghi(db, f"{HOM_NAY}T08:00:00", "som", "A", "APPROVED", "llm1")
    _ghi(db, f"{HOM_NAY}T16:00:00", "muon", "B", "APPROVED", "llm1")

    kq = report.certificates_on_day(HOM_NAY, db_path=db)
    assert [r["employee"] for r in kq["rows"]] == ["muon", "som"]


def test_danh_sach_GIU_ca_bo_qua_va_ca_hong_ky_thuat(db):
    """Con số tổng hợp loại hai loại này, danh sách thì không.

    Bỏ chúng khỏi danh sách là người đọc tưởng ngày đó không có gì cần xử lý
    tay, trong khi ca bỏ qua đang nằm chờ người duyệt trên eLIS."""
    _ghi(db, f"{HOM_NAY}T09:00:00", "thuong", "A", "APPROVED", "llm1")
    _ghi(db, f"{HOM_NAY}T10:00:00", "bo_qua", "B", "WAITING",
         "skipped_external_email", "Không xác minh được danh tính", sent=None)
    _ghi(db, f"{HOM_NAY}T11:00:00", "hong", "C", "REJECTED",
         "download_error", "eLIS không trả file", sent=None)

    kq = report.certificates_on_day(HOM_NAY, db_path=db)
    assert {r["employee"] for r in kq["rows"]} == {"thuong", "bo_qua", "hong"}


def test_cat_bot_khi_qua_dai_nhung_van_bao_tong_that(db):
    """Thư quá dài bị Outlook cắt, nên phải giới hạn — và phải nói còn bao
    nhiêu dòng nữa, không im lặng bỏ bớt."""
    for i in range(7):
        _ghi(db, f"{HOM_NAY}T09:{i:02d}:00", f"nv{i}", "A", "APPROVED", "llm1")

    kq = report.certificates_on_day(HOM_NAY, limit=3, db_path=db)
    assert len(kq["rows"]) == 3
    assert kq["total"] == 7
    assert kq["truncated"] == 4


def test_ngay_khong_co_gi_thi_danh_sach_rong(db):
    kq = report.certificates_on_day(HOM_NAY, db_path=db)
    assert kq["rows"] == []
    assert kq["total"] == 0


# --------------------------------------------------- nối vào báo cáo

def test_period_report_lay_danh_sach_theo_NGAY_GUI(db):
    """Kỳ báo cáo daily dài 7 ngày, nhưng danh sách chỉ của ngày cuối."""
    _ghi(db, f"{HOM_QUA}T09:00:00", "hom_qua", "A", "APPROVED", "llm1")
    _ghi(db, f"{HOM_NAY}T09:00:00", "hom_nay", "B", "APPROVED", "llm1")

    stats = report.period_report("2026-09-05", HOM_NAY, "day", db_path=db)
    assert stats["total"] == 2                      # số tổng vẫn cả kỳ
    assert stats["certificates"]["day"] == HOM_NAY
    assert [r["employee"] for r in stats["certificates"]["rows"]] == ["hom_nay"]


def test_HTML_co_ngay_va_tung_dong(db):
    _ghi(db, f"{HOM_NAY}T09:00:00", "hoabd3", "AI Trends", "APPROVED", "llm1")
    _ghi(db, f"{HOM_NAY}T10:00:00", "lamnt5", "Java", "REJECTED", "llm2",
         "Tên không khớp")

    stats = report.period_report(HOM_NAY, HOM_NAY, "day", db_path=db)
    html, _ = report_layout.build_html(stats)
    assert f"Chứng chỉ đã xử lý ngày {HOM_NAY}" in html
    assert "hoabd3" in html and "lamnt5" in html
    assert "Tên không khớp" in html


def test_TEXT_co_ngay_va_tung_dong(db):
    """Bản text là thứ duy nhất đọc được khi client chặn HTML."""
    _ghi(db, f"{HOM_NAY}T09:00:00", "hoabd3", "AI Trends", "APPROVED", "llm1")

    stats = report.period_report(HOM_NAY, HOM_NAY, "day", db_path=db)
    text = report_layout.build_text(stats)
    assert f"CHỨNG CHỈ ĐÃ XỬ LÝ NGÀY {HOM_NAY}" in text
    assert "hoabd3" in text and "AI Trends" in text


def test_ca_chua_nop_duoc_danh_dau_NGAY_TREN_DONG_DO(db):
    """elis_sent_ok NULL = chưa nộp. Không đánh dấu thì người đọc tưởng eLIS
    đã nhận.

    Phải tìm dấu hiệu NGAY TRÊN DÒNG của ca đó, không phải ở đâu đó trong
    thư: khối cảnh báo phía trên cũng có chữ "chưa nộp", nên tìm cả thư thì
    bỏ dấu ở bảng đi test vẫn xanh."""
    _ghi(db, f"{HOM_NAY}T09:00:00", "bo_qua", "A", "WAITING",
         "skipped_external_email", sent=None)
    _ghi(db, f"{HOM_NAY}T10:00:00", "elis_tu_choi", "B", "REJECTED", "llm2",
         sent=0)

    stats = report.period_report(HOM_NAY, HOM_NAY, "day", db_path=db)

    dong = [d for d in report_layout.build_text(stats).splitlines()
            if "bo_qua" in d]
    assert len(dong) == 1 and "chưa nộp" in dong[0]

    dong = [d for d in report_layout.build_text(stats).splitlines()
            if "elis_tu_choi" in d]
    assert len(dong) == 1 and "eLIS từ chối" in dong[0]

    html = report_layout.build_html(stats)[0]
    o_bo_qua = html.split("bo_qua", 1)[1][:400]
    assert "chưa nộp" in o_bo_qua


def test_ngay_rong_khong_lam_vo_bao_cao(db):
    """Ngày nghỉ: không có dòng nào, thư vẫn phải dựng được."""
    stats = report.period_report(HOM_NAY, HOM_NAY, "day", db_path=db)
    assert report_layout.build_html(stats)[0]
    assert report_layout.build_text(stats)
