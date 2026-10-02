"""Test chọn nhà cung cấp OCR cho tầng 2 (test_ocr_provider).

Tầng 2 có hai nhà cung cấp: Azure Document Intelligence và OCR.space. Đổi
bằng OCR_PROVIDER trong .env hoặc trên tab Cấu hình, không sửa code.

Ba chỗ dễ hỏng, và cả ba đều hỏng trong IM LẶNG:

  - pipeline gọi nhầm nhà cung cấp vì client cũ còn sót lại sau khi người
    vận hành đổi cấu hình giữa chừng;
  - trường cấu hình mới không hiện trên tab Cấu hình, người vận hành phải mở
    .env sửa tay — đúng cái bẫy TIMEOUT_SECONDS đã dính;
  - engine 1 của OCR.space không đọc được tiếng Việt, gọi vào trả E201 và cả
    hàng đợi đứng mà thông báo không nói vì sao.
"""

import logging
import pathlib
import sys
from unittest.mock import patch

import pytest

GOC = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GOC))
sys.path.insert(0, str(GOC / "src"))

import llm_error                    # noqa: E402
import ocr                          # noqa: E402
import ocr_ocrspace                 # noqa: E402
import settings_file                # noqa: E402
from config import Settings, settings   # noqa: E402

logging.disable(logging.CRITICAL)


@pytest.fixture
def ocrspace(monkeypatch):
    monkeypatch.setattr(settings, "ocr_provider", "ocrspace")
    monkeypatch.setattr(settings, "ocrspace_api_key", "key-gia")
    monkeypatch.setattr(settings, "ocrspace_engine", 2)
    monkeypatch.setattr(settings, "ocrspace_language", "vnm")


# ===== Bộ chọn =====

def test_mac_dinh_van_la_azure():
    """Đổi mặc định là lặng lẽ đẩy dữ liệu nhân viên sang bên thứ ba."""
    assert Settings.model_fields["ocr_provider"].default == "azure"


@pytest.mark.parametrize("ten, mong_doi", [
    ("azure", "ocr_azure"), ("ocrspace", "ocr_ocrspace"),
    ("  OCRSPACE  ", "ocr_ocrspace"),      # thừa khoảng trắng, viết hoa
])
def test_chon_dung_module(monkeypatch, ten, mong_doi):
    monkeypatch.setattr(settings, "ocr_provider", ten)
    assert ocr._provider_module(ocr.provider_name()).__name__ == mong_doi


def test_ten_la_thi_BAO_NGAY_chu_khong_am_tham_dung_azure(monkeypatch):
    """Gõ nhầm "ocr space" mà lặng lẽ chạy Azure thì người vận hành tưởng đã
    đổi, trong khi hóa đơn Azure vẫn chạy."""
    monkeypatch.setattr(settings, "ocr_provider", "ocr space")
    with pytest.raises(ValueError) as e:
        ocr.provider_name()
    assert "ocr space" in str(e.value)
    assert "azure" in str(e.value) and "ocrspace" in str(e.value)


def test_goi_dung_nha_cung_cap(ocrspace):
    with patch.object(ocr_ocrspace, "ocr_images", return_value="CHỮ") as goi:
        assert ocr.ocr_images(None, [b"anh"]) == "CHỮ"
    assert goi.call_count == 1


# ===== Client cũ sau khi đổi nhà cung cấp giữa chừng =====

def test_doi_nha_cung_cap_luc_dang_chay_thi_DUNG_LAI_client(monkeypatch):
    """main() tạo client MỘT LẦN, nhưng tab Cấu hình đổi được lúc đang chạy.

    Không dựng lại thì client Azure cũ bị đem đi gọi OCR.space — hỏng theo
    kiểu rất khó đoán vì thông báo lỗi đến từ sai thư viện.
    """
    monkeypatch.setattr(settings, "ocr_provider", "azure")
    cu = ocr.OcrClient("azure", "client-azure")

    monkeypatch.setattr(settings, "ocr_provider", "ocrspace")
    monkeypatch.setattr(settings, "ocrspace_api_key", "key-gia")

    with patch.object(ocr_ocrspace, "create_client", return_value="client-moi"), \
         patch.object(ocr_ocrspace, "ocr_images", return_value="CHỮ") as goi:
        ocr.ocr_images(cu, [b"anh"])

    assert goi.call_args.args[0] == "client-moi", \
        "vẫn truyền client Azure cũ sang OCR.space"


def test_client_dung_nha_cung_cap_thi_GIU_NGUYEN(ocrspace):
    """Dựng lại mỗi vòng là mở lại kết nối vô ích."""
    giu = ocr.OcrClient("ocrspace", "client-dang-dung")
    with patch.object(ocr_ocrspace, "create_client") as tao, \
         patch.object(ocr_ocrspace, "ocr_images", return_value="CHỮ") as goi:
        ocr.ocr_images(giu, [b"anh"])
    tao.assert_not_called()
    assert goi.call_args.args[0] == "client-dang-dung"


def test_client_None_truyen_thang_khong_tu_tao(ocrspace):
    """Test truyền None vào; tự tạo thay sẽ che mất chỗ gọi sai."""
    with patch.object(ocr_ocrspace, "create_client") as tao, \
         patch.object(ocr_ocrspace, "ocr_images", return_value="CHỮ"):
        ocr.ocr_images(None, [b"anh"])
    tao.assert_not_called()


# ===== OCR.space =====

def test_thieu_key_thi_bao_CAN_NGUOI_XU_LY(monkeypatch):
    """Thiếu key không tự khỏi. Không gắn mốc này thì email cảnh báo hẹn
    "sự cố khắc phục xong sẽ tự chạy tiếp" — nói sai."""
    monkeypatch.setattr(settings, "ocrspace_api_key", "")
    with pytest.raises(ocr_ocrspace.OcrError) as e:
        ocr_ocrspace.create_client()
    assert llm_error.TAG_NEEDS_HUMAN in str(e.value)


def test_engine_1_bi_chan_TU_TRUOC_khi_goi(ocrspace, monkeypatch):
    """Engine 1 không hỗ trợ tiếng Việt. Để nó gọi thật thì tốn một lượt hạn
    mức rồi nhận E201 khó hiểu; chặn từ đây và nói thẳng phải đổi sang 2."""
    monkeypatch.setattr(settings, "ocrspace_engine", 1)
    with patch.object(ocr_ocrspace.requests, "Session") as phien:
        with pytest.raises(ocr_ocrspace.OcrError) as e:
            ocr_ocrspace.ocr_bytes(phien, b"anh")
    assert "tiếng Việt" in str(e.value)
    phien.post.assert_not_called()


def test_anh_qua_1MB_bi_chan_truoc(ocrspace):
    """Trần của bậc miễn phí. Gửi lên vẫn hỏng, chỉ tốn thêm một lượt."""
    with pytest.raises(ocr_ocrspace.OcrError) as e:
        ocr_ocrspace.ocr_bytes(None, b"x" * (ocr_ocrspace.SIZE_LIMIT_BYTES + 1))
    assert "1 MB" in str(e.value)


def test_engine_2_khai_ngon_ngu_con_engine_3_thi_KHONG(ocrspace, monkeypatch):
    """OCR.space chỉ nhận MỘT mã ngôn ngữ; engine 3 tự nhận diện và ép mã vào
    có thể bị từ chối."""
    def goi_thu(engine):
        monkeypatch.setattr(settings, "ocrspace_engine", engine)
        phien = _phien_gia({"ParsedResults": [{"ParsedText": "CHỮ"}]})
        ocr_ocrspace.ocr_bytes(phien, b"anh")
        return phien.da_gui["data"]

    assert goi_thu(2).get("language") == "vnm"
    assert "language" not in goi_thu(3)


def test_200_kem_E201_noi_ro_phai_doi_engine(ocrspace):
    phien = _phien_gia({"IsErroredOnProcessing": True,
                        "ErrorMessage": "E201: engine not supported"})
    with pytest.raises(ocr_ocrspace.OcrError) as e:
        ocr_ocrspace.ocr_bytes(phien, b"anh")
    assert llm_error.TAG_NEEDS_HUMAN in str(e.value)
    assert "OCRSPACE_ENGINE" in str(e.value)


@pytest.mark.parametrize("ma", sorted(ocr_ocrspace.TRANSIENT_CODES))
def test_loi_tam_thoi_duoc_thu_lai(ocrspace, monkeypatch, ma):
    """Một cú 429 thoáng qua mà không thử lại thì chứng chỉ HỢP LỆ thành hỏng
    kỹ thuật và chặn cả hàng đợi."""
    monkeypatch.setattr(ocr_ocrspace.time, "sleep", lambda *_: None)
    phien = _phien_gia([(ma, ""), (200, {"ParsedResults": [{"ParsedText": "CHỮ"}]})])
    assert ocr_ocrspace.ocr_bytes(phien, b"anh") == "CHỮ"


@pytest.mark.parametrize("ma", [400, 401, 403])
def test_loi_vinh_vien_KHONG_thu_lai(ocrspace, monkeypatch, ma):
    """Sai key hay hết hạn mức thì thử lại ra đúng kết quả đó, chỉ kẹt lâu thêm."""
    monkeypatch.setattr(ocr_ocrspace.time, "sleep", lambda *_: None)
    phien = _phien_gia([(ma, "")] * 3)
    with pytest.raises(ocr_ocrspace.OcrError):
        ocr_ocrspace.ocr_bytes(phien, b"anh")
    assert phien.so_lan == 1, "lỗi vĩnh viễn mà vẫn gọi lại"


@pytest.mark.parametrize("ma", [401, 403])
def test_sai_key_va_het_han_muc_gan_moc_CAN_NGUOI(ocrspace, monkeypatch, ma):
    monkeypatch.setattr(ocr_ocrspace.time, "sleep", lambda *_: None)
    phien = _phien_gia([(ma, "")])
    with pytest.raises(ocr_ocrspace.OcrError) as e:
        ocr_ocrspace.ocr_bytes(phien, b"anh")
    assert llm_error.TAG_NEEDS_HUMAN in str(e.value)


def test_mot_trang_hong_khong_lam_hong_ca_tai_lieu(ocrspace):
    """PDF nhiều trang: trang bìa trống không được làm mất trang có chữ."""
    ket = iter([ocr_ocrspace.OcrError("trang bìa trống"), "NỘI DUNG TRANG 2"])

    def gia(_s, _b):
        v = next(ket)
        if isinstance(v, Exception):
            raise v
        return v

    with patch.object(ocr_ocrspace, "ocr_bytes", side_effect=gia):
        assert ocr_ocrspace.ocr_images(None, [b"a", b"b"]) == "NỘI DUNG TRANG 2"


def test_hong_het_thi_bao_LY_DO_TUNG_TRANG(ocrspace):
    """Hết hạn mức, sai key và ảnh mờ nếu không nói rõ thì hiện ra y hệt nhau,
    người vận hành không biết phải đi sửa gì."""
    with patch.object(ocr_ocrspace, "ocr_bytes",
                      side_effect=ocr_ocrspace.OcrError("hết hạn mức")):
        with pytest.raises(ocr_ocrspace.OcrError) as e:
            ocr_ocrspace.ocr_images(None, [b"a", b"b"])
    assert "trang 1" in str(e.value) and "trang 2" in str(e.value)
    assert "hết hạn mức" in str(e.value)


# ===== Hiện trên tab Cấu hình =====

@pytest.mark.parametrize("truong", ["ocr_provider", "ocrspace_engine",
                                    "ocrspace_language"])
def test_truong_moi_hien_tren_tab_cau_hinh(truong):
    """Thiếu ở đây thì biến vẫn chạy nhưng người vận hành không thấy, phải mở
    .env sửa tay — đúng cái bẫy TIMEOUT_SECONDS đã dính."""
    co_mat = {ten for _, nhom in settings_file.CONFIG_GROUPS for ten, _ in nhom}
    assert truong in co_mat


def test_nha_cung_cap_cho_CHON_chu_khong_bat_go():
    """Gõ "Azure" hoa chữ A thì provider_name() hạ chữ thường nên vẫn chạy,
    nhưng gõ "azur" thì cả hệ thống đứng. Cho chọn là hết đoán."""
    assert settings_file.CONFIG_CHOICES["ocr_provider"] == list(ocr.PROVIDERS)


def test_key_ocrspace_la_KHOA_BI_MAT():
    """Khóa bí mật không được ghi xuống .env — chúng thuộc kho khóa Windows."""
    assert "ocrspace_api_key" in settings_file.SECRET_FIELDS
    assert "ocrspace_api_key" not in settings_file.editable_fields()


# ===== Phiên HTTP giả =====

class _PhienGia:
    """Thay requests.Session. Trả lần lượt các (mã HTTP, thân JSON) đã xếp."""

    def __init__(self, ket_qua):
        self.ket_qua = ket_qua
        self.so_lan = 0
        self.da_gui = None

    def post(self, url, data=None, files=None, timeout=None):
        self.da_gui = {"url": url, "data": data, "files": files}
        ma, body_text = self.ket_qua[min(self.so_lan, len(self.ket_qua) - 1)]
        self.so_lan += 1
        return _TraLoiGia(ma, body_text)


class _TraLoiGia:
    def __init__(self, status_code, body_text):
        self.status_code = status_code
        self._than = body_text
        self.text = "" if isinstance(body_text, dict) else str(body_text)
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        if not isinstance(self._than, dict):
            raise ValueError("không phải JSON")
        return self._than


def _phien_gia(ket_qua):
    if isinstance(ket_qua, dict):
        ket_qua = [(200, ket_qua)]
    return _PhienGia(ket_qua)


# ===== Hình dạng request multipart =====

def test_ten_truong_multipart_GIU_DUNG_dang_da_goi_that(ocrspace):
    """OCR.space nhận file bằng multipart, và TÊN TRƯỜNG chính là tên file.

    Dạng này đã gọi thật được bằng tools/thu_ocrspace.py. Đổi sang
    files={"file": ...} là đổi sang thứ chưa ai gọi thử.
    """
    phien = _phien_gia({"ParsedResults": [{"ParsedText": "CHỮ"}]})
    ocr_ocrspace.ocr_bytes(phien, b"\x89PNG\r\n\x1a\n byte anh")
    ten = list(phien.da_gui["files"])
    assert ten == ["chungchi.png"], ten


@pytest.mark.parametrize("dau, duoi", [
    (b"\x89PNG\r\n\x1a\n", "png"), (b"%PDF-1.4", "pdf"),
    (b"\xff\xd8\xff\xe0", "jpg"), (b"khong ro", "jpg"),
])
def test_duoi_ten_theo_CHU_KY_chu_khong_doan_cung(ocrspace, dau, duoi):
    """file_utils trả JPEG khi phải nén, nhưng trả nguyên byte gốc (thường
    PNG) khi ảnh đã đủ nhỏ. Đoán cứng một đuôi là gắn sai cho nửa số ảnh."""
    phien = _phien_gia({"ParsedResults": [{"ParsedText": "CHỮ"}]})
    ocr_ocrspace.ocr_bytes(phien, dau + b" phan con lai")
    assert list(phien.da_gui["files"]) == [f"chungchi.{duoi}"]


# ===== Câu chữ trả cho người học =====

def test_ly_do_KHONG_goi_ten_nha_cung_cap():
    """Lý do của ProcessResult đi vào log, vào báo cáo ngày, và vào `comment`
    nộp ngược về eLIS cho người học đọc.

    Ghi cứng "Azure" trong đó là nói sai mỗi khi chạy OCR.space — và người
    đọc không có cách nào biết.
    """
    import pipeline
    nguon = pathlib.Path(pipeline.__file__).read_text(encoding="utf-8")
    cau = [d.strip() for d in nguon.splitlines()
           if "Khớp ở LLM2" in d or "Tầng 2 lỗi" in d]
    assert cau, "không tìm thấy câu lý do của tầng 2"
    for d in cau:
        assert "Azure" not in d, f"còn ghi cứng tên nhà cung cấp: {d}"
