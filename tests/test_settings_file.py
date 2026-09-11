"""Test sửa cấu hình từ app và ghi ngược vào .env (test_settings_file).

Ba chuyện sai thì hỏng im lặng:

  1. Ghi đè .env làm mất chú thích. File .env có chú thích giải thích từng
     tham số; dựng lại file từ một dict là xóa sạch.
  2. Ghi sai tên biến. Vài trường nhận nhiều tên (TECHNICAL_ALERT_AFTER còn
     ăn TECHNICAL_RETRY_MAX), ghi mù theo tên chính thì .env có hai dòng cho
     một tham số.
  3. Gán vào bộ nhớ trước khi ghi đĩa. Đĩa đầy thì hệ thống chạy cấu hình
     mới còn file giữ cấu hình cũ.

Không test nào động vào .env thật: mọi lần ghi đều vào tmp_path.
"""

import ast
import pathlib

import pytest

import settings_file as sf
from config import Settings, settings


@pytest.fixture(autouse=True)
def tra_lai_settings():
    """`settings` là object dùng chung cả phiên. Test đổi mà không trả lại
    thì mọi test sau chạy trên cấu hình khác."""
    truoc = {name: getattr(settings, name) for name in Settings.model_fields}
    yield
    for name, value in truoc.items():
        setattr(settings, name, value)


@pytest.fixture
def env_gia(tmp_path, monkeypatch):
    """Một .env tạm CÓ CHÚ THÍCH, và chuyển sổ ghi thay đổi sang tmp."""
    path = tmp_path / ".env"
    path.write_text(
        "# Cấu hình MOOC\n"
        "\n"
        "# Số giây nghỉ giữa mỗi vòng\n"
        "POLL_INTERVAL_SECONDS=5\n"
        "RETRY_COUNT=3   # ba lần là đủ\n"
        "SMTP_HOST=smtp.office365.com\n"
        "\n"
        "# ===== Không đụng tới =====\n"
        "AZURE_ENDPOINT=https://cu.invalid/\n",
        encoding="utf-8")
    monkeypatch.setattr(sf, "AUDIT_PATH", tmp_path / "config_changes.log")
    return path


# ------------------------------------------------- danh sách trường

def test_khoa_bi_mat_KHONG_nam_trong_danh_sach_sua_duoc():
    """Bốn khóa bí mật thuộc kho khóa Windows. Cho sửa ở đây là ghi chúng
    ngược vào .env dạng chữ thường."""
    duoc = set(sf.editable_fields())
    assert not (duoc & sf.SECRET_FIELDS)
    assert "fpt_api_key" not in duoc
    assert "poll_interval_seconds" in duoc


def test_CONFIG_GROUPS_phu_DU_moi_truong_sua_duoc():
    """Thêm trường vào config.py mà quên thêm vào CONFIG_GROUPS thì nó biến
    mất khỏi giao diện, không báo lỗi gì."""
    trong_nhom = [f for _, cap in sf.CONFIG_GROUPS for f, _ in cap]
    thieu = set(sf.editable_fields()) - set(trong_nhom)
    assert not thieu, f"CONFIG_GROUPS còn thiếu: {sorted(thieu)}"


def test_CONFIG_GROUPS_khong_co_ten_la_va_khong_lap():
    trong_nhom = [f for _, cap in sf.CONFIG_GROUPS for f, _ in cap]
    la = set(trong_nhom) - set(Settings.model_fields)
    assert not la, f"tên không có trong Settings: {sorted(la)}"
    lap = [f for f in set(trong_nhom) if trong_nhom.count(f) > 1]
    assert not lap, f"khai hai lần: {lap}"
    assert not (set(trong_nhom) & sf.SECRET_FIELDS)


def test_gia_tri_cho_chon_deu_hop_le():
    """Danh sách chọn lệch với thứ config.py nhận thì chọn xong lưu không
    được, hoặc lưu được một giá trị vô nghĩa."""
    for field, cac_lua in sf.CONFIG_CHOICES.items():
        assert field in Settings.model_fields
        for lua in cac_lua:
            sf.validate({field: lua})       # không được ném lỗi


def test_khong_cho_nao_doc_settings_o_muc_module():
    """Sửa `settings` lúc đang chạy chỉ có tác dụng nếu mọi nơi đọc lại
    trong thân hàm. Viết `NGUONG = settings.technical_alert_after` ở mức
    module là nút Lưu mất tác dụng cho riêng chỗ ấy."""
    goc = pathlib.Path(__file__).resolve().parent.parent
    pham = []
    for f in sorted(list(goc.glob("*.py")) + list((goc / "src").glob("*.py"))):
        cay = ast.parse(f.read_text(encoding="utf-8"))
        for node in cay.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                continue
            for n in ast.walk(node):
                if (isinstance(n, ast.Attribute)
                        and isinstance(n.value, ast.Name)
                        and n.value.id == "settings"):
                    pham.append(f"{f.name}:{n.lineno} settings.{n.attr}")
    assert not pham, "đọc settings ở mức module: " + ", ".join(pham)


# ------------------------------------------------- tên biến .env

def test_env_names_tra_ve_ten_chinh_truoc():
    assert sf.env_names("poll_interval_seconds") == ["POLL_INTERVAL_SECONDS"]
    assert sf.env_names("technical_alert_after")[0] == "TECHNICAL_ALERT_AFTER"
    assert "TECHNICAL_RETRY_MAX" in sf.env_names("technical_alert_after")
    assert "SMTP_USERNAME" in sf.env_names("smtp_user")


def test_sua_DUNG_dong_dang_dung_ten_cu(tmp_path):
    """`.env` dùng tên cũ TECHNICAL_RETRY_MAX. Phải sửa đúng dòng đó, không
    thêm dòng TECHNICAL_ALERT_AFTER thứ hai."""
    goc = "# cũ nhưng vẫn nhận\nTECHNICAL_RETRY_MAX=5\n"
    moi = sf.update_env_text(goc, {"technical_alert_after": "9"})
    assert moi == "# cũ nhưng vẫn nhận\nTECHNICAL_RETRY_MAX=9\n"
    assert "TECHNICAL_ALERT_AFTER" not in moi


# ------------------------------------------------- ghi .env

def test_giu_nguyen_chu_thich_va_dong_khac(env_gia):
    sf.apply_changes({"poll_interval_seconds": "11"}, env_path=env_gia)
    text = env_gia.read_text(encoding="utf-8")
    assert "POLL_INTERVAL_SECONDS=11" in text
    assert "# Cấu hình MOOC" in text
    assert "# Số giây nghỉ giữa mỗi vòng" in text
    assert "# ===== Không đụng tới =====" in text
    assert "AZURE_ENDPOINT=https://cu.invalid/" in text


def test_giu_chu_thich_CUOI_DONG(env_gia):
    """`RETRY_COUNT=3   # ba lần là đủ` — phần sau dấu # phải còn nguyên."""
    sf.apply_changes({"retry_count": "7"}, env_path=env_gia)
    dong = [d for d in env_gia.read_text(encoding="utf-8").splitlines()
            if d.startswith("RETRY_COUNT")]
    assert dong == ["RETRY_COUNT=7   # ba lần là đủ"]


def test_truong_chua_co_trong_file_thi_them_vao_cuoi(env_gia):
    assert "MAIL_TO" not in env_gia.read_text(encoding="utf-8")
    sf.apply_changes({"mail_to": "sep@fpt.com"}, env_path=env_gia)
    text = env_gia.read_text(encoding="utf-8")
    assert "MAIL_TO=sep@fpt.com" in text
    assert "POLL_INTERVAL_SECONDS=5" in text        # dòng cũ vẫn nguyên


def test_khong_sua_dong_nao_khac(env_gia):
    truoc = env_gia.read_text(encoding="utf-8").splitlines()
    sf.apply_changes({"smtp_host": "smtp.fpt.com"}, env_path=env_gia)
    sau = env_gia.read_text(encoding="utf-8").splitlines()
    assert len(truoc) == len(sau)
    khac = [(a, b) for a, b in zip(truoc, sau, strict=True) if a != b]
    assert khac == [("SMTP_HOST=smtp.office365.com", "SMTP_HOST=smtp.fpt.com")]


def test_bool_ghi_dang_true_false(env_gia):
    sf.apply_changes({"save_certificates": True}, env_path=env_gia)
    assert "SAVE_CERTIFICATES=true" in env_gia.read_text(encoding="utf-8")


def test_gia_tri_co_dau_cach_hoac_thang_thi_boc_nhay():
    """python-dotenv coi phần sau `#` là chú thích, không bọc nháy là mất
    đuôi giá trị."""
    assert sf.format_value("a b") == '"a b"'
    assert sf.format_value("x#y") == '"x#y"'
    assert sf.format_value("binhthuong") == "binhthuong"
    assert sf.format_value(True) == "true"
    assert sf.format_value(False) == "false"
    assert sf.format_value(11) == "11"


# ------------------------------------------------- kiểm giá trị

def test_gia_tri_sai_kieu_bi_chan(env_gia):
    with pytest.raises(sf.SettingsError):
        sf.apply_changes({"poll_interval_seconds": "abc"}, env_path=env_gia)


def test_gia_tri_sai_thi_KHONG_doi_gi_ca(env_gia):
    """Ba trường, trường thứ ba sai. Hai trường đầu không được kịp đổi."""
    truoc_file = env_gia.read_text(encoding="utf-8")
    truoc_host = settings.smtp_host

    with pytest.raises(sf.SettingsError):
        sf.apply_changes({"smtp_host": "smtp.fpt.com",
                          "retry_count": "9",
                          "poll_interval_seconds": "khong-phai-so"},
                         env_path=env_gia)

    assert settings.smtp_host == truoc_host
    assert env_gia.read_text(encoding="utf-8") == truoc_file


def test_khoa_bi_mat_bi_tu_choi(env_gia):
    with pytest.raises(sf.SettingsError):
        sf.apply_changes({"azure_key": "khoa-moi"}, env_path=env_gia)


def test_ten_truong_khong_ton_tai_bi_tu_choi(env_gia):
    with pytest.raises(sf.SettingsError):
        sf.apply_changes({"khong_co_truong_nay": "1"}, env_path=env_gia)


def test_validate_KHONG_dong_vao_settings_that():
    truoc = settings.poll_interval_seconds
    sf.validate({"poll_interval_seconds": "99"})
    assert settings.poll_interval_seconds == truoc


# ------------------------------------------------- áp dụng

def test_ap_dung_vao_settings_dang_chay(env_gia):
    sf.apply_changes({"poll_interval_seconds": "11"}, env_path=env_gia)
    assert settings.poll_interval_seconds == 11        # đã ép về int


def test_khong_doi_gi_thi_khong_ghi_file(env_gia):
    truoc = env_gia.read_text(encoding="utf-8")
    ket_qua = sf.apply_changes(
        {"poll_interval_seconds": str(settings.poll_interval_seconds)},
        env_path=env_gia)
    assert ket_qua == []
    assert env_gia.read_text(encoding="utf-8") == truoc


def test_ghi_dia_THAT_BAI_thi_settings_khong_doi(env_gia, monkeypatch):
    """Ghi đĩa trước, gán bộ nhớ sau. Làm ngược thì khi đĩa đầy, hệ thống
    chạy cấu hình mới còn file giữ cấu hình cũ."""
    truoc = settings.poll_interval_seconds

    def khong_ghi_duoc(*a, **k):
        raise OSError("đĩa đầy")
    monkeypatch.setattr(pathlib.Path, "write_text", khong_ghi_duoc)

    with pytest.raises(sf.SettingsError):
        sf.apply_changes({"poll_interval_seconds": "11"}, env_path=env_gia)
    assert settings.poll_interval_seconds == truoc


def test_tra_ve_dung_cai_gi_da_doi(env_gia):
    doi = sf.apply_changes({"poll_interval_seconds": "11",
                            "smtp_host": settings.smtp_host},
                           env_path=env_gia)
    assert [f for f, _, _ in doi] == ["poll_interval_seconds"]
    _, cu, moi = doi[0]
    assert (cu, moi) == (5, 11)


# ------------------------------------------------- sổ ghi thay đổi

def test_ghi_so_ai_doi_gi_luc_nao(env_gia):
    """Sửa cấu hình bằng vài cú bấm chuột thì phải có dấu vết."""
    sf.apply_changes({"course_match_mode": "strict"}, env_path=env_gia)
    so = sf.AUDIT_PATH.read_text(encoding="utf-8")
    assert "COURSE_MATCH_MODE" in so
    assert "'loose' -> 'strict'" in so


def test_so_ghi_hong_KHONG_lam_hong_viec_luu(env_gia, monkeypatch):
    """Mất sổ thì tiếc, nhưng không được làm hỏng chính việc lưu."""
    monkeypatch.setattr(sf, "AUDIT_PATH", pathlib.Path("/khong/ton/tai/x.log"))
    sf.apply_changes({"poll_interval_seconds": "11"}, env_path=env_gia)
    assert settings.poll_interval_seconds == 11
