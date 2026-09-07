"""Test đọc file (test_file_utils).

LỖI ĐÃ XẢY RA THẬT, và là lỗi tốn kém nhất tới giờ: trên Windows, libmagic
nhận đường dẫn dưới dạng byte theo bảng mã hệ thống (CP1258/CP1252). Mọi file
có dấu tiếng Việt trong TÊN đều hỏng — không phải nội dung file sai, chỉ vì
cái tên. Hai thông báo nhận được đều không chỉ về nguyên nhân:

    'utf-8' codec can't decode bytes in position 74-75: invalid continuation byte
    Loại file không hỗ trợ: cannot open `...\\Mở Khoá AI_cẩm nang...`

Đo trên bộ dữ liệu thật 133 chứng chỉ FPT: 84 file tên có dấu -> hỏng 84/84,
49 file tên thuần ASCII -> chạy 49/49. Tách sạch, không một ngoại lệ.

Job chạy thật che mất lỗi này: nó ghi byte tải từ eLIS ra file tạm tên ASCII
do tempfile sinh, nên libmagic không bao giờ thấy tên tiếng Việt. Chỉ khi đọc
thẳng file người dùng tự đặt tên mới lòi ra — nghĩa là không test nào ở tầng
job bắt được, phải test đúng tại đây.
"""

import pathlib
import sys

import pytest

GOC = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GOC))
sys.path.insert(0, str(GOC / "src"))

import file_utils                                     # noqa: E402

# Ảnh PNG 1x1 hợp lệ nhỏ nhất, đủ để libmagic nhận ra image/png.
PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080200000090"
    "7753de0000000c49444154789c63f8cfc0000003010100c9fe92ef000000"
    "0049454e44ae426082"
)

# Header PDF tối thiểu — libmagic chỉ cần vài byte đầu để nhận application/pdf.
PDF_HEADER = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"


# Đúng những cái tên đã làm hỏng 84 ca trên máy khanh.
TEN_CO_DAU = [
    "CUONGDM23_Mở Khoá AI_cẩm nang viết Prompt của ChatGPT, Gemini hiệu quả.png",
    "HAHS4_Định luật Parkinson trong quản lý thời gian.png",
    "THANGLQ2_Kỹ năng đặt câu hỏi.png",
    "DUONGLX2_C++ cho người mới bắt đầu.png",
    "LEPV_ AI CƠ BẢN_AI FOR EVERYONE.png",
]


@pytest.mark.parametrize("name", TEN_CO_DAU)
def test_ten_file_co_dau_tieng_viet_van_doc_duoc(tmp_path, name):
    """Tên file có dấu KHÔNG được ảnh hưởng tới việc nhận dạng nội dung."""
    p = tmp_path / name
    p.write_bytes(PNG_1X1)
    assert file_utils.check_mime(p) == "image/png"


def test_ten_file_ascii_van_doc_duoc(tmp_path):
    """Đối chứng: sửa cho tên có dấu không được làm hỏng tên thường."""
    p = tmp_path / "THIENND8_Introduction to SAN and NAS Storage.png"
    p.write_bytes(PNG_1X1)
    assert file_utils.check_mime(p) == "image/png"


def test_pdf_ten_co_dau(tmp_path):
    p = tmp_path / "NGUYENTD36_ AI CƠ BẢN_AI FOR EVERYONE.pdf"
    p.write_bytes(PDF_HEADER + b"\n" + b"0" * 500)
    assert file_utils.check_mime(p) == "application/pdf"


def test_khong_doc_ca_file_chi_de_doan_loai(tmp_path, monkeypatch):
    """Chỉ được đọc phần đầu file, không nạp cả file vào RAM để đoán loại."""
    p = tmp_path / "to.png"
    p.write_bytes(PNG_1X1 + b"\x00" * (5 * 1024 * 1024))

    da_nhan = {}

    def gia(buf, mime=False):
        da_nhan["so_byte"] = len(buf)
        return "image/png"

    monkeypatch.setattr(file_utils.magic, "from_buffer", gia)
    file_utils.check_mime(p)

    assert da_nhan["so_byte"] <= file_utils._MAGIC_BYTES


def test_file_rong_bao_ro(tmp_path):
    """File 0 byte phải báo đúng nguyên nhân, không để libmagic đoán lung tung."""
    p = tmp_path / "rong.png"
    p.write_bytes(b"")
    with pytest.raises(file_utils.InvalidFileError) as e:
        file_utils.check_mime(p)
    assert "rỗng" in str(e.value)


def test_file_khong_ton_tai(tmp_path):
    with pytest.raises(file_utils.InvalidFileError) as e:
        file_utils.check_mime(tmp_path / "khong-co.png")
    assert "Không tìm thấy file" in str(e.value)


def test_loai_khong_ho_tro_van_bao_ten_file(tmp_path):
    """Thông báo phải kèm TÊN FILE, nếu không người vận hành không biết ca nào."""
    p = tmp_path / "Tệp lạ có dấu.png"
    p.write_bytes(b"GIF89a" + b"\x00" * 100)
    with pytest.raises(file_utils.InvalidFileError) as e:
        file_utils.check_mime(p)
    assert "Tệp lạ có dấu.png" in str(e.value)


def test_read_as_images_ten_co_dau(tmp_path):
    """Cả đường đọc ảnh (không chỉ check_mime) phải chịu được tên có dấu."""
    p = tmp_path / "HAHS4_Định luật Parkinson trong quản lý thời gian.png"
    p.write_bytes(PNG_1X1)
    images = file_utils.read_as_images(p)
    assert len(images) == 1 and images[0]


def test_khong_bao_gio_dua_duong_dan_cho_libmagic(tmp_path, monkeypatch):
    """Guard THẬT của bản sửa này, và là test duy nhất chạy được trên Linux.

    Lỗi gốc chỉ tái hiện trên Windows (libmagic + bảng mã hệ thống), nên các
    test tên-có-dấu ở trên vẫn XANH trên Linux/CI kể cả khi code quay lại
    dùng from_file — chúng không bảo vệ được gì ngoài Windows.

    Test này thì có: nó chặn ở mức API. Chỉ cần ai đó đổi về from_file là đỏ,
    trên mọi hệ điều hành, vì tên file KHÔNG được phép tới tay libmagic.
    """
    p = tmp_path / "Tên có dấu.png"
    p.write_bytes(PNG_1X1)

    def cam(*a, **kw):
        raise AssertionError(
            "check_mime đã gọi magic.from_file — đường dẫn lại tới tay libmagic, "
            "tên file có dấu sẽ hỏng trên Windows (xem docstring đầu file).")

    monkeypatch.setattr(file_utils.magic, "from_file", cam)
    assert file_utils.check_mime(p) == "image/png"


def test_render_pdf_khong_dua_duong_dan_cho_pdfium(tmp_path, monkeypatch):
    """pypdfium2 cũng là thư viện C — không được nhận tên file, cùng lý do."""
    p = tmp_path / "Chứng chỉ tiếng Việt.pdf"
    p.write_bytes(PDF_HEADER + b"0" * 200)

    da_nhan = {}

    class PdfGia:
        def __init__(self, nguon):
            da_nhan["kieu"] = type(nguon)
        def __len__(self):
            return 0
        def close(self):
            pass

    monkeypatch.setattr(file_utils.pdfium, "PdfDocument", PdfGia)
    with pytest.raises(file_utils.InvalidFileError):
        file_utils._render_pdf(p)          # 0 trang -> báo lỗi, đúng thiết kế

    assert da_nhan["kieu"] is bytes, "đã truyền đường dẫn thay vì byte cho pdfium"
