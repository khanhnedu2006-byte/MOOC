"""Test canh mẫu prompt (test_prompt).

Rủi ro cụ thể: prompt là CHUỖI, chỗ thay thế là CHUỖI, và không có gì bắt hai
bên phải khớp nhau. Lệch tên thì `str.replace()` chỉ lặng lẽ không thay gì —
không exception, không cảnh báo. Prompt vẫn được gửi đi, LLM vẫn trả lời, chỉ
là trả lời "vui lòng cung cấp nội dung {ocr_text}" thay vì dữ liệu.

Đã xảy ra thật: sau đợt đổi tên định danh sang tiếng Anh, PROMPT được đổi
thành {ocr_text} nhưng dòng .replace("{text_ocr}") thì không (nó nằm trong
chuỗi nháy đơn, không phải docstring). Tầng 2 hỏng hoàn toàn trong khi vẫn
trả tiền cho Azure OCR ở bước trước đó. Lỗi chỉ lộ ra ở log production.
"""

import sys
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GOC))
sys.path.insert(0, str(GOC / "src"))

import llm_text                        # noqa: E402
import llm_vision                      # noqa: E402
from schemas import ExtractedInfo      # noqa: E402


class _LlmGia:
    """LLM giả, giữ lại prompt nhận được để test soi."""

    def __init__(self, tra_ve: str):
        self.tra_ve = tra_ve
        self.da_nhan = None

    def invoke(self, messages):
        self.da_nhan = messages[0].content

        class R:
            content = self.tra_ve
        return R()


JSON_HOP_LE = ('{"recipient_name":"Bui Duc Hoa",'
               '"certificate_name":"ISO 27001","issue_date":"10/07/2026"}')


def test_placeholder_ton_tai_trong_prompt():
    """Hằng số thay thế phải THẬT SỰ xuất hiện trong PROMPT."""
    assert llm_text.PLACEHOLDER in llm_text.PROMPT, (
        f"PROMPT không chứa {llm_text.PLACEHOLDER!r} — mọi lần gọi LLM2 sẽ "
        f"gửi đi prompt chưa điền text OCR."
    )


def test_text_ocr_that_su_di_vao_prompt():
    """Đây là test bắt được lỗi đã xảy ra ở production."""
    llm = _LlmGia(JSON_HOP_LE)
    moc = "GIAY CHUNG NHAN — Bui Duc Hoa — ISO 27001 — 10/07/2026"
    llm_text.extract_from_text(moc, llm=llm)

    assert moc in llm.da_nhan, "text OCR không được đưa vào prompt"
    assert llm_text.PLACEHOLDER not in llm.da_nhan, (
        "prompt gửi đi CÒN NGUYÊN placeholder — LLM sẽ hỏi lại thay vì trích "
        "xuất, và lỗi chỉ lộ ra ở bước parse JSON."
    )


def test_khong_con_dau_ngoac_nhon_la_trong_prompt_gui_di():
    """Quét mọi {...} còn sót — mỗi cái là một chỗ thay thế bị quên.

    PROMPT có ví dụ JSON nên bản thân nó chứa nhiều dấu ngoặc nhọn. Chỉ soi
    các cụm dạng {ten_bien} (một từ định danh, không khoảng trắng, không dấu
    nháy) — đó mới là hình dạng của một placeholder.
    """
    import re

    llm = _LlmGia(JSON_HOP_LE)
    llm_text.extract_from_text("text ocr bat ky", llm=llm)
    con_sot = re.findall(r"\{[a-z_][a-z0-9_]*\}", llm.da_nhan)
    assert not con_sot, f"placeholder chưa được thay: {con_sot}"


def test_ocr_rong_bao_loi_ro_thay_vi_goi_llm():
    """Text rỗng phải chặn TRƯỚC khi gọi LLM — gọi cũng vô ích mà vẫn mất tiền."""
    llm = _LlmGia(JSON_HOP_LE)
    with pytest.raises(llm_text.LlmTextError):
        llm_text.extract_from_text("", llm=llm)
    with pytest.raises(llm_text.LlmTextError):
        llm_text.extract_from_text("   \n  ", llm=llm)
    assert llm.da_nhan is None, "vẫn gọi LLM dù text rỗng"


def test_llm_tra_loi_khong_phai_json_bao_loi_ro():
    llm = _LlmGia("Vui lòng cung cấp nội dung để tôi trích xuất.")
    with pytest.raises(llm_text.LlmTextError, match="không phải JSON"):
        llm_text.extract_from_text("text ocr", llm=llm)


def test_boc_duoc_json_trong_rao_markdown():
    """Model hay bọc JSON trong ```json — phải bóc được, không thì hỏng oan."""
    llm = _LlmGia(f"```json\n{JSON_HOP_LE}\n```")
    r = llm_text.extract_from_text("text ocr", llm=llm)
    assert r.recipient_name == "Bui Duc Hoa"


def test_llm_vision_khong_dung_placeholder():
    """LLM1 gửi ảnh kèm prompt tĩnh — không có chỗ thay thế nào để lệch.

    Test này chốt lại điều đó: nếu sau này ai thêm placeholder vào PROMPT của
    llm_vision mà quên thay, test sẽ đỏ.
    """
    import re
    con_sot = re.findall(r"\{[a-z_][a-z0-9_]*\}", llm_vision.PROMPT)
    assert not con_sot, (
        f"llm_vision.PROMPT có placeholder {con_sot} nhưng "
        f"extract_from_image() không thay gì cả."
    )


def test_hai_prompt_cung_yeu_cau_dung_bo_truong():
    """LLM1 và LLM2 phải trích cùng bộ trường, nếu không pipeline so lệch.

    pipeline._llms_agree() so recipient_name và certificate_name của hai bên.
    Một bên đổi tên trường trong prompt mà bên kia không đổi thì hai model
    trả về hai schema khác nhau, và bước đồng thuận mất ý nghĩa.
    """
    truong = set(ExtractedInfo.model_fields)
    for ten, prompt in (("llm_vision", llm_vision.PROMPT),
                        ("llm_text", llm_text.PROMPT)):
        for t in truong:
            assert t in prompt, f"{ten}.PROMPT không nhắc tới trường {t!r}"


# =====================================================================
# OCR: thông báo lỗi phải mang theo nguyên nhân thật
# =====================================================================

def test_ocr_hong_het_van_giu_ly_do_that():
    """Hết quota / sai key KHÔNG được hiện ra giống hệt ảnh mờ.

    Bản trước nuốt mọi OcrError rồi ném đúng một câu "Không trang nào đọc
    được chữ." Câu đó đúng về hình thức nhưng che mất nguyên nhân — người vận
    hành nhìn log không biết phải đi sửa gì: đổi key, xin thêm quota, hay bảo
    nhân viên chụp lại ảnh.
    """
    from unittest.mock import patch

    import ocr_azure

    for dau_hieu, loi in (
        ("403", "[Azure 403] Out of call volume quota."),
        ("401", "[Azure 401] Access denied. Sai key Azure."),
        ("mờ", "Azure không đọc được chữ nào (ảnh có thể mờ hoặc trống)."),
    ):
        with patch.object(ocr_azure, "ocr_bytes",
                          side_effect=ocr_azure.OcrError(loi)):
            with pytest.raises(ocr_azure.OcrError) as e:
                ocr_azure.ocr_images(None, [b"anh"])
        assert dau_hieu in str(e.value), (
            f"thông báo mất dấu hiệu {dau_hieu!r}: {e.value}")


def test_ocr_mot_trang_hong_van_lay_duoc_trang_con_lai():
    """PDF nhiều trang: một trang hỏng không được làm mất cả tài liệu."""
    from unittest.mock import patch

    import ocr_azure

    ket = iter([ocr_azure.OcrError("trang bìa trống"), "NỘI DUNG TRANG 2"])

    def gia(client, image):
        v = next(ket)
        if isinstance(v, Exception):
            raise v
        return v

    with patch.object(ocr_azure, "ocr_bytes", side_effect=gia):
        assert ocr_azure.ocr_images(None, [b"a", b"b"]) == "NỘI DUNG TRANG 2"


def test_azure_loi_tam_thoi_duoc_thu_lai():
    """408/429/5xx là lỗi TẠM THỜI — không thử lại thì một lần Azure trở chứng
    làm chứng chỉ HỢP LỆ bị từ chối vĩnh viễn trên eLIS.

    Đã gặp thật: Azure 408 "The operation was timeout" sau 43 giây.
    """
    from unittest.mock import MagicMock, patch

    from azure.core.exceptions import HttpResponseError

    import ocr_azure

    def loi(ma):
        e = HttpResponseError(message="tạm thời")
        e.status_code = ma
        return e

    def client(ket_qua):
        it = iter(ket_qua)
        c = MagicMock()

        def bat_dau(*a, **k):
            v = next(it)
            if isinstance(v, Exception):
                raise v
            p = MagicMock()
            p.result.return_value = MagicMock(content=v)
            return p
        c.begin_analyze_document.side_effect = bat_dau
        return c

    with patch.object(ocr_azure.time, "sleep"):
        for ma in (408, 429, 500, 503):
            assert ocr_azure.ocr_bytes(client([loi(ma), "TEXT"]), b"x") == "TEXT", (
                f"Azure {ma} không được thử lại")


def test_azure_loi_vinh_vien_khong_thu_lai():
    """Sai key / ảnh hỏng: thử lại chỉ tốn thời gian, kết quả không đổi."""
    from unittest.mock import MagicMock, patch

    from azure.core.exceptions import HttpResponseError

    import ocr_azure

    for ma in (400, 401, 403):
        e = HttpResponseError(message="vĩnh viễn")
        e.status_code = ma
        c = MagicMock()
        c.begin_analyze_document.side_effect = e
        with patch.object(ocr_azure.time, "sleep"):
            with pytest.raises(ocr_azure.OcrError):
                ocr_azure.ocr_bytes(c, b"x")
        assert c.begin_analyze_document.call_count == 1, (
            f"Azure {ma} bị thử lại {c.begin_analyze_document.call_count} lần")


def test_moi_ma_loi_tam_thoi_deu_co_giai_thich():
    """Mã lỗi không có lời giải thích thì log chỉ còn con số vô nghĩa."""
    from azure.core.exceptions import HttpResponseError

    import ocr_azure

    for ma in ocr_azure.MA_LOI_TAM_THOI | {400, 401, 403}:
        e = HttpResponseError(message="x")
        e.status_code = ma
        mo_ta = ocr_azure._explain_error(e)
        assert str(ma) in mo_ta
        if ma not in (502, 504):        # hai mã này hiếm, dùng chung 5xx
            assert len(mo_ta) > len(f"[Azure {ma}] x."), (
                f"mã {ma} không có lời giải thích nào")


# ===== Luật chống các lỗi đã đo được trên dữ liệu thật =====
#
# Các lỗi dưới đây đều quan sát được trên bộ 133 chứng chỉ thật. Prompt rất dễ
# bị sửa "cho gọn" mà không ai nhận ra đã mất luật nào, vì KHÔNG có gì báo lỗi
# — chỉ có tỷ lệ từ chối oan lặng lẽ tăng lại sau vài tháng. Mỗi test dưới đây
# neo một luật kèm lý do nó tồn tại.

import pytest as _pytest


def _hai_prompt():
    import llm_text
    import llm_vision
    return [("llm_vision", llm_vision.PROMPT), ("llm_text", llm_text.PROMPT)]


@_pytest.mark.parametrize("ten,prompt", _hai_prompt())
def test_co_truong_rieng_cho_nguoi_ky(ten, prompt):
    """Chứng chỉ Codelearn của 'tienpham89' bị đọc thành 'ĐỖ VĂN KHẮC'.

    Tên người nhận in chữ mảnh ở giữa trang; tên giám đốc IN ĐẬM VIẾT HOA ở
    cuối, cạnh nét ký. Model chọn chữ nổi bật nhất. Cho người ký một ô riêng
    buộc model phải PHÂN BIỆT hai vai trò thay vì chọn bừa một cái tên.
    """
    assert "signatory_name" in prompt, f"{ten}: mất trường tên người ký"


@_pytest.mark.parametrize("ten,prompt", _hai_prompt())
def test_cam_lay_ten_canh_chu_ky(ten, prompt):
    thap = prompt.lower()
    assert "chữ ký" in thap
    assert "giám đốc" in thap, f"{ten}: không nêu chức danh để model tránh"


@_pytest.mark.parametrize("ten,prompt", _hai_prompt())
def test_chap_nhan_username_lam_ten_nguoi_nhan(ten, prompt):
    """Nhiều chứng chỉ in username ('tienpham89') chứ không phải họ tên.

    Không nói rõ thì model đi tìm 'một chuỗi trông giống tên người' ở chỗ
    khác trên trang — đúng cái bẫy sinh ra lỗi trên.
    """
    assert "username" in prompt.lower()


@_pytest.mark.parametrize("ten,prompt", _hai_prompt())
def test_tach_ten_khoa_theo_NGON_NGU(ten, prompt):
    """Tên khóa song ngữ -> tách: tiếng Việt vào certificate_name, tiếng Anh
    vào certificate_name_alt.

    Tách là ĐÚNG, với điều kiện phía so sánh thử cả bản GHÉP hai nửa — vì
    eLIS lưu tên khóa ở cả ba dạng (chỉ Việt / chỉ Anh / cả hai nối lại).
    Bản trước tôi bắt model chép nguyên cả dòng để né ca eLIS-lưu-cả-hai;
    cách đó hỏng ngược lại ở ca eLIS chỉ lưu một ngôn ngữ. Sửa ở tầng so
    sánh (match_course_bilingual) mới giải được cả ba dạng cùng lúc.
    """
    # Kiểm đúng ÁNH XẠ trường <-> ngôn ngữ, không chỉ kiểm hai chữ có mặt.
    # Bản trước chỉ tìm "TIẾNG VIỆT"/"TIẾNG ANH" nên đổi hẳn luật mà test vẫn
    # xanh — test xanh vì lý do sai.
    gon = " ".join(prompt.split())
    assert "certificate_name = bản TIẾNG VIỆT" in gon, (
        f"{ten}: không nói rõ certificate_name là bản tiếng Việt")
    assert "certificate_name_alt = bản TIẾNG ANH" in gon, (
        f"{ten}: không nói rõ certificate_name_alt là bản tiếng Anh")


@_pytest.mark.parametrize("ten,prompt", _hai_prompt())
def test_chi_lay_ten_khoa_khong_lay_cau_bao_quanh(ten, prompt):
    """Chứng chỉ in: Đã hoàn thành khoá học "Python cơ bản".

    Tên khóa là "Python cơ bản", KHÔNG phải cả câu. Lấy cả câu thì chuỗi dài
    ra và không bao giờ khớp tên khóa eLIS lưu.
    """
    assert "KHÔNG lấy câu bao quanh" in prompt
    assert "Has successfully completed the course" in prompt


@_pytest.mark.parametrize("ten,prompt", _hai_prompt())
def test_giu_nguyen_chu_khong_phai_latin(ten, prompt):
    """'AI入門講座' bị đọc thành 'AIXFEDE'.

    Bịa một chuỗi Latin còn tệ hơn bỏ trống: bỏ trống thì phần Latin còn lại
    vẫn đối chiếu được, bịa thì thêm từ rác làm hỏng cả phép so.
    """
    thap = prompt.lower()
    assert "phiên âm" in thap or "romaji" in thap
    assert "BỎ TRỐNG" in prompt


@_pytest.mark.parametrize("ten,prompt", _hai_prompt())
def test_cam_lay_ngay_tu_dong_ho_he_thong(ten, prompt):
    """Có 'chứng chỉ' thật ra là ảnh chụp màn hình Udacity.

    Trên đó ngày duy nhất là đồng hồ taskbar Windows — không phải ngày hoàn
    thành khóa học. Lấy nó là bịa ra bằng chứng không tồn tại.
    """
    assert "taskbar" in prompt.lower()


@_pytest.mark.parametrize("ten,prompt", _hai_prompt())
def test_thieu_thi_de_null_chu_khong_doan(ten, prompt):
    assert "để null" in prompt and "không bịa" in prompt
