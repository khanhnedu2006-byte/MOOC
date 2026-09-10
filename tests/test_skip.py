"""Test ca BỎ QUA vì không xác minh được danh tính (test_skip).

Hai luật, cùng một kết cục — để nguyên WAITING cho người duyệt xử lý:

  1. Chứng chỉ ghi email NGOÀI công ty thay cho tên ("minhnt4487@gmail.com").
     Nhân viên học khóa ngoài hay đăng ký bằng mail cá nhân.        1/162 ca.
  2. Tên trên ảnh THIẾU họ hoặc tên đệm ("Lê Tiến" / "Lê Xuân Tiến").
     Nhà cấp chứng chỉ in tên người học tự gõ lúc đăng ký.          9/162 ca.

Cả hai đều KHÔNG phải "sai" — hệ thống chỉ không nối được chuỗi đọc ra với
nhân viên nào. Từ chối là từ chối oan, nên đẩy sang người xem.

Ca này KHÁC ca hỏng kỹ thuật ở ba điểm, và ba test cuối canh đúng ba điểm đó:
không chặn hàng đợi, không tính vào ngưỡng gửi mail, không thử lại.

Bẫy dễ sập nhất: email CÔNG TY cũng là email, nhưng phần trước "@" chính là mã
nhân viên nên match_code vẫn khớp và chứng chỉ vẫn được duyệt bình thường. Luật
viết rộng tay một chút là nuốt luôn 6/7 ca email đang chạy đúng.
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
import compare                         # noqa: E402
import pipeline                        # noqa: E402
import run                             # noqa: E402
from database import database          # noqa: E402
from schemas import (                  # noqa: E402
    ExtractedInfo, InputInfo, ProcessResult, Verdict,
)

logging.disable(logging.CRITICAL)


# ===== Nhận diện email ngoài công ty =====
#
# Bảy ca dưới đây là TOÀN BỘ ca có email trong recipient_name của bộ đánh giá
# thật (7/162). Sáu ca đầu phải giữ nguyên hành vi cũ, chỉ ca cuối được đổi.

@pytest.mark.parametrize("name_on_image, expected", [
    ("doannv19@fpt.com", None),
    ("dungha31@fpt.com", None),
    ("thangnh54@fpt.com", None),
    ("NGUYEN THUY LINH LinhNT8@fpt.com", None),
    ("PHAM THI LAM LamPT31@fpt.com", None),
    ("NGẪU NGỌC LAN LanNN7@fpt.com", None),
    ("minhnt4487@gmail.com", "gmail.com"),
])
def test_bay_ca_email_that_trong_bo_danh_gia(name_on_image, expected):
    assert compare.external_email(name_on_image) == expected


def test_ten_nguoi_binh_thuong_KHONG_bi_coi_la_email():
    assert compare.external_email("Lê Hoàng Anh") is None
    assert compare.external_email("") is None
    assert compare.external_email(None) is None


def test_chay_tren_chuoi_GOC_chu_khong_qua_normalize():
    """normalize() cắt "@" thành khoảng trắng, sau đó không còn phân biệt được
    email với tên người nữa — nên phép kiểm tra phải đứng TRƯỚC nó."""
    from process_data import normalize
    assert "@" not in normalize("minhnt4487@gmail.com")
    assert compare.external_email("minhnt4487@gmail.com") == "gmail.com"


# ===== Thứ tự: chỉ hỏi tới email SAU KHI so tên/mã đã trượt =====

def _given(name="Nguyễn Thúy Linh", code="linhnt8", course="Python"):
    return InputInfo(employee_name=name, course_name=course, employee_code=code)


def _extracted(recipient, course="Python"):
    return ExtractedInfo(recipient_name=recipient, certificate_name=course,
                         certificate_name_alt=None, issue_date="01/06/2026")


def test_ten_dung_NHUNG_kem_email_ca_nhan_van_BI_BO_QUA():
    """GIỚI HẠN ĐÃ BIẾT, ghi lại ở đây để không ai sửa nhầm thành "lỗi".

    Ảnh in "NGUYEN THUY LINH minhnt4487@gmail.com" — tên đúng nằm ngay đó, mắt
    người đọc là xác minh được. Nhưng match_name so TẬP HỢP TỪ tuyệt đối, nên
    ba từ thừa ("minhnt4487", "gmail", "com") làm phép so trượt.

    Không chữa ở đây, và đó là lựa chọn CÓ CHỦ ĐÍCH: chữa nghĩa là nới
    match_name thành so "tập con", mà nới ra thì "Nguyễn Tuấn" khớp với nhiều
    nhân viên khác nhau — mua vài ca đúng bằng một lỗ hổng danh tính. Hướng sai
    hiện tại an toàn (ca đó về tay người duyệt, không bị từ chối oan).

    Ca email CÔNG TY không dính giới hạn này vì match_code cứu: "LinhNT8" nằm
    trong ảnh dưới dạng một từ trọn vẹn sau normalize.
    """
    reason = pipeline._unverifiable_identity(
        _extracted("NGUYEN THUY LINH minhnt4487@gmail.com"),
        _given())
    assert "gmail.com" in reason
    # Cùng cấu trúc nhưng email công ty -> match_code khớp -> KHÔNG bỏ qua.
    assert pipeline._unverifiable_identity(
        _extracted("NGUYEN THUY LINH LinhNT8@fpt.com"), _given()) is None


def test_ma_nhan_vien_KHOP_thi_KHONG_bo_qua():
    assert pipeline._unverifiable_identity(
        _extracted("linhnt8@fpt.com"), _given()) is None


def test_ma_KHOP_thi_email_ca_nhan_di_kem_KHONG_lam_bo_qua():
    """Canh đúng THỨ TỰ trong _unverifiable_identity.

    Ảnh in cả mã nhân viên lẫn email cá nhân — match_code tìm thấy "linhnt8"
    như một từ trọn vẹn nên danh tính đã xác minh xong; cái email đi kèm chỉ là
    thông tin liên hệ, không phải thứ định danh.

    Đảo thứ tự (hỏi email trước) thì ca này bị bỏ qua oan, mà không test nào
    khác trong file bắt được — vì mọi ca còn lại đều dùng email @fpt.com, tức
    external_email() trả None nên thứ tự không lộ ra.
    """
    assert pipeline._unverifiable_identity(
        _extracted("LINHNT8 linhnt8@gmail.com"), _given()) is None


def test_khong_khop_gi_va_email_ngoai_thi_BO_QUA():
    reason = pipeline._unverifiable_identity(
        _extracted("minhnt4487@gmail.com"), _given())
    assert reason and "gmail.com" in reason


def test_khong_khop_gi_nhung_KHONG_phai_email_thi_van_TU_CHOI():
    """Tên đọc được là tên người thật, chỉ khác người — đó là kết luận nghiệp
    vụ bình thường, không phải ca không xác minh được."""
    assert pipeline._unverifiable_identity(
        _extracted("Trần Văn Bê"), _given()) is None


# ===== Chạy hết pipeline =====

def _process(llm1_name, llm2_name, given=None):
    llm1, llm2 = _extracted(llm1_name), _extracted(llm2_name)
    return pipeline.process(
        images=[b"x"], given=given or _given(),
        extract_from_image=lambda img: llm1,
        ocr_images=lambda cli, imgs: "text",
        extract_from_text=lambda t: llm2,
        azure_client=None)


def test_hai_llm_dong_thuan_doc_ra_email_ngoai_thi_BO_QUA():
    result = _process("minhnt4487@gmail.com", "minhnt4487@gmail.com")
    assert result.verdict == Verdict.WAITING
    assert result.stage == database.SKIP_STAGE
    assert "gmail.com" in result.reason


def test_hai_llm_LECH_nhau_ma_llm2_ra_email_ngoai_thi_BO_QUA():
    result = _process("minhnt 4487", "minhnt4487@gmail.com")
    assert result.verdict == Verdict.WAITING
    assert result.stage == database.SKIP_STAGE


def test_ca_TU_CHOI_binh_thuong_KHONG_bi_doi_thanh_bo_qua():
    result = _process("Trần Văn Bê", "Trần Văn Bê")
    assert result.verdict == Verdict.REJECTED
    assert result.stage != database.SKIP_STAGE


# ===== Hành vi ở run.py =====

def _item(uc_id: str) -> dict:
    return {"id": uc_id, "certificate_id": f"c-{uc_id}", "courseId": "K1",
            "employeeId": "003", "employeeName": "Bùi Đức Hòa",
            "employeeEmail": "hoabd3@fpt.com", "courseName": "ISO 27001",
            "providerName": "Coursera"}


def _bo_qua():
    return ProcessResult(verdict=Verdict.WAITING,
                         reason="Không xác minh được danh tính: chứng chỉ ghi "
                                "email ngoài công ty (@gmail.com)",
                         stage=database.SKIP_STAGE)


def _duyet():
    return ProcessResult(verdict=Verdict.APPROVED, reason="Khớp", stage="llm1")


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


def _chay(items, ket_qua_scan):
    """Chạy một vòng, trả về (RoundResult, DTO đã nộp, số lần gọi pipeline)."""
    da_nop = []
    da_quet = []
    it_kq = iter(ket_qua_scan)

    def nop(dtos):
        da_nop.extend(dtos)
        return {"successList": [{"id": d["id"]} for d in dtos], "failList": []}

    def quet(*a):
        da_quet.append(a)
        return next(it_kq)

    with patch.object(client, "get_pending_list", return_value=items), \
         patch.object(client, "download_certificates",
                      side_effect=lambda cc: [
                          {"userCourseId": c["UserCourseId"], "anh_bytes": b"x"}
                          for c in cc]), \
         patch.object(client, "update_status", side_effect=nop), \
         patch.object(run, "scan_certificate", side_effect=quet), \
         patch.object(run.archive, "save", return_value=None), \
         patch.object(run.archive, "write_verdict", return_value=None):
        result = run.process_one_round(None)
    return result, da_nop, len(da_quet)


def test_ca_bo_qua_KHONG_BAO_GIO_duoc_nop_ve_elis(moi_truong):
    """Nộp nghĩa là bản ghi rời WAITING và học viên nhận một kết luận mà hệ
    thống chưa hề đưa ra được.

    Trường comment trên eLIS có thể đang mang câu của bản code CŨ ("Chưa xử lý
    được chứng chỉ, vui lòng thử lại sau") và sẽ nằm lại đó vĩnh viễn vì không
    có gì ghi đè. Đó là dấu vết đã biết, KHÔNG phải lý do để gọi API ③ — gọi
    ③ nghĩa là gửi status cho một ca mà hệ thống chưa kết luận được.
    """
    _, da_nop, _ = _chay([_item("A")], [_bo_qua()])
    assert da_nop == [], f"đã nộp {da_nop} cho một ca không kết luận được"


def test_ca_bo_qua_KHONG_chan_cac_ca_sau(moi_truong):
    """Khác hẳn ca hỏng kỹ thuật: đây không phải sự cố cả lô, chứng chỉ B và C
    hoàn toàn bình thường nên phải được xử lý ngay trong vòng này."""
    _, da_nop, so_lan_quet = _chay(
        [_item("A"), _item("B"), _item("C")],
        [_bo_qua(), _duyet(), _duyet()])
    assert so_lan_quet == 3, "ca bỏ qua đã chặn hàng đợi"
    assert [d["id"] for d in da_nop] == ["B", "C"]


def test_vong_sau_KHONG_tai_lai_ca_da_bo_qua(moi_truong):
    """Đây là lý do tồn tại của skipped_ids: bản ghi vẫn WAITING nên getCert
    trả về nó mãi, mà mỗi lượt xử lý lại là một lượt Gemma + Azure + LLM2 trả
    tiền cho một kết quả không bao giờ đổi."""
    _chay([_item("A")], [_bo_qua()])
    _, da_nop, so_lan_quet = _chay([_item("A"), _item("B")], [_duyet()])
    assert so_lan_quet == 1, "đã quét lại ca bỏ qua — đốt thêm một lượt LLM"
    assert [d["id"] for d in da_nop] == ["B"]


def test_ca_bo_qua_KHONG_tinh_vao_nguong_gui_mail(moi_truong):
    """Chu kỳ poll 5 giây mà ngưỡng cảnh báo là 5 lần: nếu ca bỏ qua bị đếm
    như hỏng kỹ thuật thì 25 giây sau người vận hành nhận mail báo động về một
    chứng chỉ mà hệ thống chẳng làm sai gì cả."""
    _chay([_item("A")], [_bo_qua()])
    assert database.SKIP_STAGE not in database.TECHNICAL_STAGES
    assert database.technical_retry_state(["A"], moi_truong) == {}


def test_bo_qua_TU_HET_khi_chung_chi_duoc_xu_ly_binh_thuong(moi_truong):
    """skipped_ids lấy dòng MỚI NHẤT, không phải "từng có dòng skip".

    Nếu lấy nhầm thành "từng có", một lần bỏ qua sai (luật viết hụt, model đọc
    lỗi) sẽ khóa chứng chỉ đó vĩnh viễn, và cách chữa duy nhất là vào SQLite
    trên production xóa tay.
    """
    conn = sqlite3.connect(moi_truong)
    conn.execute("INSERT INTO process_log (created_at,user_course_id,verdict,stage)"
                 " VALUES ('2026-08-01T10:00:00','A','WAITING',?)",
                 (database.SKIP_STAGE,))
    conn.commit()
    conn.close()
    assert database.skipped_ids(["A"], moi_truong) == {"A"}

    conn = sqlite3.connect(moi_truong)
    conn.execute("INSERT INTO process_log (created_at,user_course_id,verdict,stage)"
                 " VALUES ('2026-08-01T10:00:00','A','APPROVED','llm1')")
    conn.commit()
    conn.close()
    assert database.skipped_ids(["A"], moi_truong) == set()


# ===== Thiếu họ hoặc tên đệm =====
#
# Chín ca dưới đây là TOÀN BỘ ca "tên trên ảnh là tập con của tên eLIS" trong
# bộ đánh giá thật (9/162 — nguyên nhân từ chối oan lớn nhất của hệ thống,
# nhiều gấp chín lần ca email cá nhân). Nhà cấp chứng chỉ ngoài in tên người
# học tự gõ lúc đăng ký, mà người Việt hay bỏ tên đệm khi gõ.

@pytest.mark.parametrize("name_on_image, employee_name", [
    ("Thanh Nga", "Tô Thị Thanh Nga"),
    ("Nguyễn Tuấn", "Nguyễn Chánh Tuấn"),
    ("Lê Tiến", "Lê Xuân Tiến"),
    ("Giang Pham", "Phạm Ngân Giang"),
    ("Anh Long", "Nguyễn Trần Long Anh"),
    ("Giang Lê", "Lê Trà Giang"),
    ("The Pham", "Phạm Thị The"),
    ("Hong Phong", "Lê Hồng Phong"),
    ("Tan Nguyen", "Nguyễn Nhật Tân"),
])
def test_chin_ca_thieu_ten_dem_that_deu_duoc_BO_QUA(name_on_image, employee_name):
    reason = pipeline._unverifiable_identity(
        _extracted(name_on_image),
        InputInfo(employee_name=employee_name, course_name="Python",
                  employee_code="khongkhop"))
    assert reason is not None, "vẫn đang bị từ chối oan"
    assert "thiếu họ hoặc tên đệm" in reason


def test_ten_KHAC_HAN_thi_KHONG_phai_thieu_ten_dem():
    """Không chung từ nào thì không phải "bỏ bớt tên đệm" — đó là chuyện khác,
    và gộp chung sẽ giấu mất ca nộp nhầm chứng chỉ của người khác."""
    assert compare.name_missing_words("Trần Văn Bê", "Nguyễn Thúy Linh") is False


def test_anh_THUA_tu_KHONG_tinh_la_thieu_ten_dem():
    """Chiều ngược lại cố ý không bắt: ảnh thừa từ có thể là chức danh, cũng có
    thể là tên người khác in kèm — hai thứ đó không quy về một luật được."""
    assert compare.name_missing_words("Nguyễn Thị Thanh Nga", "Thanh Nga") is False


def test_tap_hop_BANG_nhau_khong_vao_nhanh_thieu_ten_dem():
    """Bằng nhau thì match_name đã bắt từ trước; dùng "<" chứ không "<=" để
    nhánh này không bao giờ cướp quyền của phép so tên."""
    assert compare.name_missing_words("Lê Xuân Tiến", "Lê Xuân Tiến") is False


# ===== Danh tính chỉ thắng khi tên là lý do DUY NHẤT =====
#
# 6/10 ca vướng danh tính trong bộ đánh giá CÒN sai cả khóa học hoặc ngày. Phép
# so khóa học không cần biết người đó là ai, nên kết luận đó vẫn đứng vững —
# bỏ qua chúng là vứt đi 60% số ca vốn kết luận được, và học viên mất luôn câu
# "Tên khóa học không khớp" vốn cho họ biết đường nộp lại.

def test_sai_ca_khoa_hoc_thi_TU_CHOI_chu_khong_bo_qua():
    result = _process("Lê Tiến", "Lê Tiến",
                      given=InputInfo(employee_name="Lê Xuân Tiến",
                                      course_name="Java nâng cao",
                                      employee_code="tienlx6"))
    assert result.verdict == Verdict.REJECTED
    assert result.stage != database.SKIP_STAGE
    assert "Tên khóa học không khớp" in result.reason


def test_sai_ca_NGAY_thi_TU_CHOI_chu_khong_bo_qua():
    llm = ExtractedInfo(recipient_name="Lê Tiến", certificate_name="Python",
                        certificate_name_alt=None, issue_date="01/01/1999")
    result = pipeline.process(
        images=[b"x"],
        given=InputInfo(employee_name="Lê Xuân Tiến", course_name="Python",
                        employee_code="tienlx6"),
        extract_from_image=lambda img: llm,
        ocr_images=lambda cli, imgs: "text",
        extract_from_text=lambda t: llm,
        azure_client=None)
    assert result.verdict == Verdict.REJECTED
    assert result.stage != database.SKIP_STAGE
    assert "Ngày không hợp lệ" in result.reason


def test_email_ngoai_MA_sai_khoa_hoc_cung_TU_CHOI():
    result = _process("minhnt4487@gmail.com", "minhnt4487@gmail.com",
                      given=InputInfo(employee_name="Nguyễn Thanh Minh",
                                      course_name="Java nâng cao",
                                      employee_code="minhnt159"))
    assert result.verdict == Verdict.REJECTED
    assert "Tên khóa học không khớp" in result.reason


def test_chi_sai_moi_TEN_thi_van_bo_qua():
    """Cửa mới không được nuốt luôn ca mà nó sinh ra để phục vụ."""
    result = _process("Lê Tiến", "Lê Tiến",
                      given=InputInfo(employee_name="Lê Xuân Tiến",
                                      course_name="Python",
                                      employee_code="tienlx6"))
    assert result.verdict == Verdict.WAITING
    assert result.stage == database.SKIP_STAGE
