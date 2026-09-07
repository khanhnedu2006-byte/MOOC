"""Test cảnh báo lỗi hệ thống qua email (test_alert).

Module alert thay cho nhánh "bỏ cuộc, nộp REJECTED" mà HR đã bỏ. Nó phải làm
đúng hai việc trái chiều nhau:

  - GỬI khi có sự cố thật, nếu không lỗi hệ thống trở nên hoàn toàn im lặng
    và chứng chỉ nằm WAITING vô thời hạn mà không ai biết.
  - KHÔNG GỬI TRÙNG, vì một sự cố hạ tầng làm cả hàng đợi cùng vượt ngưỡng,
    và job thử lại mỗi hai phút. Không chặn thì hộp thư ngập thư giống hệt
    nhau và người nhận sẽ lọc bỏ tất — cảnh báo mất tác dụng đúng lúc cần.

Phần lớn test ở đây canh vế thứ hai, vì vế thứ nhất hỏng thì thấy ngay còn vế
thứ hai hỏng thì chỉ phát hiện khi đã spam mất người nhận.
"""

import logging
import pathlib
import sys
from datetime import datetime, timedelta

import pytest

GOC = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GOC))
sys.path.insert(0, str(GOC / "src"))

import alert                              # noqa: E402

logging.disable(logging.CRITICAL)

BAY_GIO = datetime(2026, 8, 18, 10, 0, 0)


@pytest.fixture
def state_rieng(tmp_path, monkeypatch):
    """File trạng thái riêng cho từng test, không đụng file thật của dự án."""
    monkeypatch.setattr(alert, "STATE_FILE", tmp_path / ".alert_state.json")
    monkeypatch.setattr(alert.settings, "alert_cooldown_hours", 1)
    monkeypatch.setattr(alert.settings, "alert_mail_to", "hoabd5@fpt.com")
    monkeypatch.setattr(alert.settings, "technical_alert_after", 5)
    monkeypatch.setattr(alert.settings, "technical_retry_cooldown_minutes", 2)
    return tmp_path


def _ca(uc_id="uc-1", stage="stage2_error", attempts=5):
    return {"id": uc_id, "employee_name": "Bùi Đức Hòa",
            "course_name": "ISO 27001", "failure_count": attempts,
            "stage": stage, "reason": "Azure timeout"}


class _BatThu:
    """Hàm gửi giả, ghi lại mọi lời gọi."""

    def __init__(self, loi=None):
        self.calls = []
        self.loi = loi

    def __call__(self, title, html, text, images=None, mail_to=None):
        if self.loi:
            raise self.loi
        self.calls.append({"title": title, "html": html, "text": text,
                           "mail_to": mail_to})
        return None


# ===== Gửi khi có sự cố =====

def test_gui_khi_qua_nguong(state_rieng):
    thu = _BatThu()
    assert alert.send_alert([_ca()], now=BAY_GIO, send=thu) is True
    assert len(thu.calls) == 1


def test_gui_dung_dia_chi_canh_bao(state_rieng, monkeypatch):
    """Cảnh báo đi tới ALERT_MAIL_TO, KHÔNG phải MAIL_TO của báo cáo định kỳ.

    Trộn hai luồng vào một hộp thư thì cảnh báo chìm giữa báo cáo hàng ngày.
    """
    monkeypatch.setattr(alert.settings, "alert_mail_to", "hoabd5@fpt.com")
    thu = _BatThu()
    alert.send_alert([_ca()], now=BAY_GIO, send=thu)
    assert thu.calls[0]["mail_to"] == "hoabd5@fpt.com"


def test_khong_gui_khi_danh_sach_rong(state_rieng):
    thu = _BatThu()
    assert alert.send_alert([], now=BAY_GIO, send=thu) is False
    assert thu.calls == []


# ===== Nội dung thư =====

def test_thu_noi_ro_KHONG_bi_tu_choi(state_rieng):
    """Câu này là điểm mấu chốt của luật HR.

    Thiếu nó, người nhận đọc "lỗi hệ thống, 40 chứng chỉ" rồi đi báo học viên
    rằng chứng chỉ bị từ chối — đúng thứ luật mới sinh ra để tránh.
    """
    thu = _BatThu()
    alert.send_alert([_ca()], now=BAY_GIO, send=thu)
    text = thu.calls[0]["text"].lower()
    assert "không bị từ chối" in text
    assert "thử lại" in text


def test_thu_liet_ke_nguyen_nhan_theo_stage(state_rieng):
    """Người nhận là người vận hành, 'stage2_error' không nói cho họ điều gì."""
    thu = _BatThu()
    alert.send_alert([_ca(stage="stage2_error")], now=BAY_GIO, send=thu)
    assert "Azure" in thu.calls[0]["text"]


def test_thu_liet_ke_moi_ca(state_rieng):
    thu = _BatThu()
    ds = [_ca(f"uc-{i}") for i in range(3)]
    alert.send_alert(ds, now=BAY_GIO, send=thu)
    assert thu.calls[0]["text"].count("Bùi Đức Hòa") == 3
    assert "3" in thu.calls[0]["title"]


# ===== Chặn gửi trùng =====

def test_khong_gui_lai_trong_thoi_gian_cho(state_rieng):
    """Sự cố kéo dài + thử lại mỗi 2 phút = hàng trăm thư nếu không chặn.

    Job tính lại danh sách vượt ngưỡng ở MỌI vòng, và ca hỏng 5 lần thì vòng
    sau hỏng 6 lần — vẫn vượt. Nên không chặn thì mỗi 2 phút một thư.
    """
    thu = _BatThu()
    alert.send_alert([_ca()], now=BAY_GIO, send=thu)
    alert.send_alert([_ca()], now=BAY_GIO + timedelta(minutes=2), send=thu)
    alert.send_alert([_ca()], now=BAY_GIO + timedelta(minutes=30), send=thu)
    assert len(thu.calls) == 1, "gửi lại khi chưa hết giãn cách"


def test_gui_lai_sau_moi_tieng(state_rieng):
    """Sự cố vẫn còn sau một tiếng thì NHẮC LẠI — không được im luôn.

    Chặn vĩnh viễn thì một sự cố kéo dài cả tuần chỉ được báo đúng một lần,
    mà thư đó người nhận có thể đã bỏ lỡ.
    """
    thu = _BatThu()
    alert.send_alert([_ca()], now=BAY_GIO, send=thu)
    alert.send_alert([_ca()], now=BAY_GIO + timedelta(minutes=70), send=thu)
    assert len(thu.calls) == 2


def test_thu_dau_tien_KHONG_bi_cooldown_lam_cham(state_rieng, monkeypatch):
    """Cooldown chỉ chi phối nhịp NHẮC LẠI, không chi phối thư đầu.

    Đặt cooldown rất dài mà thư đầu vẫn phải đi ngay: nếu ai đó nhầm hai khái
    niệm này, một cấu hình ALERT_COOLDOWN_HOURS=24 sẽ nuốt mất cảnh báo đầu
    tiên và không ai biết cho tới hôm sau.
    """
    monkeypatch.setattr(alert.settings, "alert_cooldown_hours", 24)
    thu = _BatThu()
    assert alert.send_alert([_ca()], now=BAY_GIO, send=thu) is True


def test_danh_sach_ca_doi_KHONG_lam_moi_bo_dem(state_rieng):
    """Mốc chặn tính theo LOẠI sự cố, không theo danh sách chứng chỉ.

    Danh sách đổi mỗi vòng (ca cũ xong, ca mới vào) nên lấy nó làm mốc thì
    thư nào cũng là 'sự cố mới' và chặn không còn tác dụng — đúng cái bẫy
    biến cơ chế chặn thành vô dụng mà vẫn trông như đang hoạt động.
    """
    thu = _BatThu()
    alert.send_alert([_ca("uc-1")], now=BAY_GIO, send=thu)
    alert.send_alert([_ca("uc-2"), _ca("uc-3")],
                     now=BAY_GIO + timedelta(minutes=4), send=thu)
    assert len(thu.calls) == 1


def test_stage_moi_thi_gui_ngay(state_rieng):
    """Sự cố loại KHÁC là tin mới, không được nuốt vì đang trong giãn cách."""
    thu = _BatThu()
    alert.send_alert([_ca(stage="stage2_error")], now=BAY_GIO, send=thu)
    alert.send_alert([_ca(stage="download_error")],
                     now=BAY_GIO + timedelta(minutes=4), send=thu)
    assert len(thu.calls) == 2


def test_fingerprint_khong_phu_thuoc_thu_tu(state_rieng):
    """Cùng tập stage, khác thứ tự -> vẫn là một sự cố."""
    a = alert.fingerprint([_ca(stage="stage2_error"), _ca(stage="no_file")])
    b = alert.fingerprint([_ca(stage="no_file"), _ca(stage="stage2_error")])
    assert a == b


# ===== Gửi hỏng =====

def test_gui_hong_thi_khong_ghi_moc_da_gui(state_rieng):
    """Thư không đi được thì lần sau vẫn phải thử — nhưng không phải ngay.

    Ghi mốc 'đã gửi' khi thật ra chưa gửi được là mất luôn cảnh báo trong 6
    tiếng, đúng lúc hệ thống đang hỏng.
    """
    failed = _BatThu(loi=OSError("SMTP chết"))
    assert alert.send_alert([_ca()], now=BAY_GIO, send=failed) is False

    thu = _BatThu()
    # Ngay sau đó: chưa tới lượt thử gửi lại.
    alert.send_alert([_ca()], now=BAY_GIO + timedelta(minutes=2), send=thu)
    assert thu.calls == []
    # Sau RETRY_SEND_MINUTES: được thử lại.
    alert.send_alert([_ca()],
                     now=BAY_GIO + timedelta(minutes=alert.RETRY_SEND_MINUTES + 1),
                     send=thu)
    assert len(thu.calls) == 1


def test_gui_hong_KHONG_nem_loi_ra_ngoai(state_rieng):
    """send_alert được gọi từ giữa vòng xử lý chứng chỉ.

    Một lỗi SMTP làm chết vòng đó nghĩa là sự cố mạng nhỏ biến thành job
    ngừng chạy — tệ hơn hẳn việc không gửi được thư.
    """
    failed = _BatThu(loi=RuntimeError("bất kỳ lỗi gì"))
    assert alert.send_alert([_ca()], now=BAY_GIO, send=failed) is False


def test_file_trang_thai_hong_van_gui_duoc(state_rieng):
    """File trạng thái hỏng thì thà gửi thừa còn hơn im lặng."""
    alert.STATE_FILE.write_text("{ không phải JSON", encoding="utf-8")
    thu = _BatThu()
    assert alert.send_alert([_ca()], now=BAY_GIO, send=thu) is True


# ===== Lỗi API eLIS =====
#
# Ba API của eLIS trước đây được đối xử rất khác nhau, và hai trong ba KHÔNG
# BAO GIỜ gửi được cảnh báo:
#   ① getCert  -> lỗi bay lên run_forever, không ghi DB -> bộ đếm không nhích.
#   ② download -> ghi stage download_error -> có đếm, có cảnh báo.
#   ③ Process  -> chỉ đánh dấu elis_sent_ok=0, không phải stage kỹ thuật.
# Nghĩa là đúng hai ca nặng nhất (① hệ thống đứng im, ③ đốt tiền quét lại) thì
# im lặng. Bộ đếm API dưới đây là thứ bịt hai lỗ đó.

def test_api_hong_chua_toi_nguong_thi_khong_gui(state_rieng):
    for _ in range(4):                       # ngưỡng = 5
        alert.api_failed(1, "getCert HTTP 403")
    thu = _BatThu()
    assert alert.send_alert([], now=BAY_GIO, send=thu) is False


def test_api_hong_qua_nguong_thi_gui_du_ca_KHONG_co_chung_chi_nao(state_rieng):
    """API ① chết thì KHÔNG có chứng chỉ nào để liệt kê — vẫn phải gửi.

    Đây chính là ca nặng nhất: không lấy được hàng đợi nên hệ thống đứng im
    hoàn toàn. Bản trước trả về False ngay khi ca_hong rỗng, tức là im lặng
    đúng lúc cần nói nhất.
    """
    for _ in range(5):
        alert.api_failed(1, "getCert HTTP 403: IP not allowed")
    thu = _BatThu()
    assert alert.send_alert([], now=BAY_GIO, send=thu) is True
    text = thu.calls[0]["text"]
    assert "getCert" in text
    assert "IP not allowed" in text, "không nói VÌ SAO thất bại"
    assert "ĐỨNG IM" in text, "không nói hệ quả"


def test_thu_liet_ke_du_CA_BA_api(state_rieng):
    """Chỉ in API đang hỏng thì người đọc không biết hai cái kia đã được kiểm
    tra hay chưa. 'Không nhắc tới' và 'vẫn tốt' là hai chuyện khác nhau."""
    for _ in range(5):
        alert.api_failed(2, "Download HTTP 500")
    thu = _BatThu()
    alert.send_alert([], now=BAY_GIO, send=thu)
    text = thu.calls[0]["text"]
    assert "getCert" in text and "download-certificates" in text
    assert "ProcessUserCourseStatus" in text
    assert text.count("bình thường") == 2, "không nói rõ hai API kia vẫn tốt"


def test_api_thanh_cong_thi_xoa_bo_dem(state_rieng):
    """eLIS sống lại -> bộ đếm về 0, không gửi cảnh báo nữa."""
    for _ in range(5):
        alert.api_failed(3, "ProcessStatus HTTP 502")
    alert.api_succeeded(3)
    thu = _BatThu()
    assert alert.send_alert([], now=BAY_GIO, send=thu) is False


def test_api_va_chung_chi_hong_cung_luc_thi_GOP_MOT_thu(state_rieng):
    """API ② chết VỪA là lỗi API VỪA làm chứng chỉ hỏng.

    Gửi hai thư riêng là nói hai lần về đúng một sự cố. Gộp lại còn cho ra thứ
    hai thư riêng không có: nguyên nhân nằm cạnh hậu quả.
    """
    for _ in range(5):
        alert.api_failed(2, "Download HTTP 500")
    thu = _BatThu()
    alert.send_alert([_ca(stage="download_error")], now=BAY_GIO, send=thu)
    assert len(thu.calls) == 1
    text = thu.calls[0]["text"]
    assert "download-certificates" in text        # nguyên nhân
    assert "Bùi Đức Hòa" in text                  # hậu quả


def test_khoa_chong_trung_phan_biet_duoc_api_hong(state_rieng):
    """Khóa chống gửi trùng phải mang cả mã API.

    Nếu chỉ lấy stage của chứng chỉ thì lúc API ① chết (ca_hong rỗng) khóa sẽ
    là chuỗi rỗng — trùng với mọi sự cố rỗng khác, và cơ chế chống trùng sẽ
    nuốt luôn thư báo API hỏng.
    """
    assert alert.fingerprint([], {1: {}}) != alert.fingerprint([], {})
    assert alert.fingerprint([], {1: {}}) != alert.fingerprint([], {3: {}})


def test_api_moi_hong_thi_gui_ngay_khong_doi_gian_cach(state_rieng):
    """Đang cảnh báo vì API ② mà API ① cũng chết -> tin mới, báo ngay."""
    for _ in range(5):
        alert.api_failed(2, "Download HTTP 500")
    thu = _BatThu()
    alert.send_alert([], now=BAY_GIO, send=thu)
    for _ in range(5):
        alert.api_failed(1, "getCert HTTP 403")
    alert.send_alert([], now=BAY_GIO + timedelta(minutes=2), send=thu)
    assert len(thu.calls) == 2


def test_api_thanh_cong_khong_ghi_file_khi_khong_co_gi_de_xoa(state_rieng):
    """Hàm này chạy sau MỌI lần gọi API thành công, tức mỗi vài giây.

    Ghi file mỗi lần là hàng chục nghìn lượt ghi đĩa mỗi ngày cho một việc
    không đổi gì.
    """
    alert.api_succeeded(1)
    assert not alert.STATE_FILE.exists(), "ghi file dù không có bộ đếm nào"


# ===== Sự cố KHÔNG tự khỏi =====
#
# Thư mặc định viết "sự cố khắc phục xong thì chúng tự được xử lý, không cần
# thao tác gì thêm". Câu đó đúng với Azure quá tải hay eLIS chập, nhưng SAI
# với hết tiền / sai key / file hỏng: sẽ không có ai khắc phục gì nếu không
# được nói là phải đi làm gì. Đó cũng là ca người nhận dễ đọc lướt rồi để tới
# hôm sau nhất, mà lại là ca mất mát nhiều nhất.

def _ca_het_tien():
    return {"id": "uc-9", "employee_name": "Bùi Đức Hòa",
            "course_name": "ISO 27001", "failure_count": 5, "stage": "llm1_error",
            "reason": "[FPT 402] CẦN NGƯỜI XỬ LÝ: HẾT TIỀN hoặc hết hạn mức "
                      "FPT AI Marketplace. Thử lại sẽ KHÔNG tự khỏi — phải nạp "
                      "thêm hạn mức. — Error code: 402"}


def test_het_tien_thi_thu_KHONG_noi_tu_khoi(state_rieng):
    thu = _BatThu()
    alert.send_alert([_ca_het_tien()], now=BAY_GIO, send=thu)
    text = thu.calls[0]["text"]
    assert "KHÔNG TỰ KHỎI" in text
    assert "HẾT TIỀN" in text
    assert "tự được xử lý, không cần thao tác gì thêm" not in text, (
        "vẫn hứa sự cố tự khỏi trong khi nó cần người nạp tiền")


def test_su_co_thuong_VAN_noi_tu_khoi(state_rieng):
    """Không được đổi giọng nhầm: Azure quá tải thì đúng là tự khỏi thật."""
    thu = _BatThu()
    alert.send_alert([_ca()], now=BAY_GIO, send=thu)
    text = thu.calls[0]["text"]
    assert "tự được xử lý" in text
    assert "KHÔNG TỰ KHỎI" not in text


def test_file_hong_cung_la_ca_khong_tu_khoi(state_rieng):
    """file_error là lỗi của RIÊNG một chứng chỉ, không phải hạ tầng.

    Với luật chặn đầu hàng, nó khóa cả hàng đợi vô thời hạn — phải nói to.
    """
    thu = _BatThu()
    alert.send_alert([_ca(stage="file_error")], now=BAY_GIO, send=thu)
    text = thu.calls[0]["text"]
    assert "KHÔNG TỰ KHỎI" in text
    assert "nộp lại file" in text
    assert "CHẶN" in text


def test_viec_phai_lam_khong_lap_lai(state_rieng):
    """Azure hết quota làm 40 chứng chỉ cùng hỏng vì đúng MỘT lý do.

    In 40 dòng giống hệt nhau thì khối 'việc phải làm' dài hơn cả bảng chứng
    chỉ và không ai đọc hết.
    """
    ds = [dict(_ca_het_tien(), id=f"uc-{i}") for i in range(40)]
    assert len(alert._actions_required(ds)) == 1
