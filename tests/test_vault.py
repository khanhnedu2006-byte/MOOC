"""Test kho khóa Windows và thứ tự ưu tiên nguồn cấu hình (test_vault).

Ba câu hỏi, sai câu nào cũng hỏng im lặng:

  1. Khóa trong kho có thắng .env không? Không thì nút "Lưu khóa" là nút giả.
  2. Biến môi trường có thắng kho khóa không? Không thì
     `set AZURE_KEY=... && python run.py` mất tác dụng, và bản Docker có nguy
     cơ bị kho khóa của máy dev đè lên.
  3. Trên máy không phải Windows, code có động tới keyring không? Phải là
     không, kể cả import — container không có D-Bus.
"""

import sys

import pytest

import vault
from config import Settings

SECRET_ENV_NAMES = ("FPT_API_KEY", "AZURE_KEY", "AZURE_ENDPOINT",
                    "ELIS_API_KEY", "SMTP_PASSWORD")


class FakeKeyring:
    """Bản giả của module keyring, đủ dùng cho vault.py."""

    def __init__(self, store=None, fail_on_read=False):
        self.store = dict(store or {})
        self.fail_on_read = fail_on_read
        self.writes = []
        self.deletes = []

    def get_password(self, service, name):
        if self.fail_on_read:
            raise RuntimeError("kho khóa hỏng")
        return self.store.get((service, name))

    def set_password(self, service, name, value):
        self.writes.append((service, name, value))
        self.store[(service, name)] = value

    def delete_password(self, service, name):
        self.deletes.append((service, name))
        if (service, name) not in self.store:
            raise RuntimeError("không có gì để xóa")
        del self.store[(service, name)]


@pytest.fixture
def dung_kho(monkeypatch):
    """Cắm một kho khóa giả vào vault, trả về nó để kiểm lại."""
    def _cam(store=None, fail_on_read=False):
        fake = FakeKeyring(store, fail_on_read)
        monkeypatch.setattr(vault, "_load_keyring", lambda: fake)
        return fake
    return _cam


def _settings(tmp_path, monkeypatch, dong_env, khoa_trong_kho, bien_moi_truong=None):
    """Dựng một Settings sạch với .env riêng và kho khóa giả."""
    # conftest.py đã đặt sẵn các biến này; phải dọn đi thì mới đo được
    # đúng thứ tự ưu tiên chứ không phải đo lại chính conftest.
    for name in SECRET_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    for name, value in (bien_moi_truong or {}).items():
        monkeypatch.setenv(name, value)

    monkeypatch.setattr(vault, "read_all", lambda: khoa_trong_kho)

    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(dong_env), encoding="utf-8")
    return Settings(_env_file=str(env_file))


DONG_ENV_TOI_THIEU = [
    "FPT_API_KEY=tu_dotenv",
    "AZURE_KEY=azure_tu_dotenv",
    "AZURE_ENDPOINT=https://dotenv.invalid/",
]


# ---------------------------------------------------- thứ tự ưu tiên

def test_kho_khoa_THANG_dotenv(tmp_path, monkeypatch):
    """Có khóa trong kho thì .env bị bỏ qua — nếu không, nút Lưu là nút giả."""
    s = _settings(tmp_path, monkeypatch, DONG_ENV_TOI_THIEU,
                  {"fpt_api_key": "tu_kho_khoa"})
    assert s.fpt_api_key == "tu_kho_khoa"


def test_bien_moi_truong_THANG_kho_khoa(tmp_path, monkeypatch):
    """Đặt biến môi trường là cách ghi đè cho một lần chạy. Phải thắng."""
    s = _settings(tmp_path, monkeypatch, DONG_ENV_TOI_THIEU,
                  {"fpt_api_key": "tu_kho_khoa"},
                  bien_moi_truong={"FPT_API_KEY": "tu_bien_moi_truong"})
    assert s.fpt_api_key == "tu_bien_moi_truong"


def test_kho_rong_thi_roi_ve_dotenv(tmp_path, monkeypatch):
    """Bản Docker đi đúng đường này: kho rỗng, mọi thứ như trước."""
    s = _settings(tmp_path, monkeypatch, DONG_ENV_TOI_THIEU, {})
    assert s.fpt_api_key == "tu_dotenv"
    assert s.azure_key == "azure_tu_dotenv"


def test_kho_chi_co_mot_khoa_thi_cac_khoa_khac_van_lay_tu_dotenv(
        tmp_path, monkeypatch):
    """Trộn hai nguồn phải ra đúng từng trường, không phải được ăn cả."""
    s = _settings(tmp_path, monkeypatch, DONG_ENV_TOI_THIEU,
                  {"azure_key": "azure_tu_kho"})
    assert s.azure_key == "azure_tu_kho"
    assert s.fpt_api_key == "tu_dotenv"


def test_cau_hinh_khong_bi_mat_KHONG_doc_tu_kho(tmp_path, monkeypatch):
    """Kho khóa chỉ giữ 4 khóa. Poll interval mà chui vào đây là sai thiết kế."""
    s = _settings(tmp_path, monkeypatch,
                  DONG_ENV_TOI_THIEU + ["POLL_INTERVAL_SECONDS=11"], {})
    assert s.poll_interval_seconds == 11


def test_ten_khoa_khop_voi_ten_truong_trong_Settings():
    """Đổi tên trường trong Settings mà quên sửa vault thì kho khóa im lặng
    mất tác dụng."""
    fields = set(Settings.model_fields)
    thieu = {n.lower() for n in vault.SECRET_NAMES} - fields
    assert not thieu, f"vault.SECRET_NAMES có tên không còn trong Settings: {thieu}"


# ---------------------------------------------------- vault.py

def test_khong_phai_windows_thi_KHONG_dung_toi_keyring(monkeypatch):
    """Chốt cho bản Docker: container không có D-Bus, chạm keyring là hỏng.

    Cấm luôn câu import chứ không chỉ xem giá trị trả về: bỏ chốt
    `sys.platform` mà chỉ kiểm giá trị thì test vẫn xanh, vì trên Linux
    keyring trả về backend `fail` nên kết quả cuối vẫn là None.
    """
    monkeypatch.setattr(sys, "platform", "linux")

    import builtins
    import_that = builtins.__import__

    def cam_import(name, *args, **kwargs):
        if name.split(".")[0] == "keyring":
            raise AssertionError(
                "Không phải Windows mà vẫn import keyring — trong container "
                "không có D-Bus, câu import này treo hoặc ném lỗi.")
        return import_that(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", cam_import)

    assert vault._load_keyring() is None
    assert vault.available() is False
    assert vault.read_all() == {}


def test_doc_duoc_thi_tra_ve_ten_thuong(dung_kho):
    dung_kho({(vault.SERVICE, "FPT_API_KEY"): "abc",
              (vault.SERVICE, "AZURE_KEY"): "xyz"})
    assert vault.read_all() == {"fpt_api_key": "abc", "azure_key": "xyz"}


def test_kho_hong_thi_tra_ve_rong_chu_khong_nem_loi(dung_kho):
    """Kho khóa hỏng chỉ được làm hệ thống rơi về .env, không làm job chết."""
    dung_kho({(vault.SERVICE, "FPT_API_KEY"): "abc"}, fail_on_read=True)
    assert vault.read_all() == {}


def test_khoa_rong_khong_duoc_tinh_la_da_co(dung_kho):
    """Chuỗi rỗng trong kho phải rơi về .env, không được đè lên bằng rỗng."""
    dung_kho({(vault.SERVICE, "FPT_API_KEY"): ""})
    assert vault.read_all() == {}


def test_describe_khong_tra_ve_gia_tri(dung_kho):
    dung_kho({(vault.SERVICE, "AZURE_KEY"): "bi-mat-that"})
    mo_ta = vault.describe()
    assert mo_ta["AZURE_KEY"] is True
    assert mo_ta["FPT_API_KEY"] is False
    assert "bi-mat-that" not in str(mo_ta)


def test_ghi_dung_service_va_ten_hoa(dung_kho):
    """Sai SERVICE hay sai kiểu chữ là ghi một chỗ, đọc một nẻo."""
    fake = dung_kho()
    vault.set_secret("azure_key", "  gia-tri  ")
    assert fake.writes == [(vault.SERVICE, "AZURE_KEY", "gia-tri")]


def test_ghi_ten_la_bi_tu_choi(dung_kho):
    dung_kho()
    with pytest.raises(vault.VaultError):
        vault.set_secret("POLL_INTERVAL_SECONDS", "5")


def test_ghi_gia_tri_rong_bi_tu_choi(dung_kho):
    dung_kho()
    with pytest.raises(vault.VaultError):
        vault.set_secret("AZURE_KEY", "   ")


def test_khong_co_backend_thi_ghi_phai_BAO_LOI(monkeypatch):
    """Chỗ duy nhất được phép ném lỗi: người dùng vừa bấm Lưu và đang chờ
    kết quả."""
    monkeypatch.setattr(vault, "_load_keyring", lambda: None)
    with pytest.raises(vault.VaultError):
        vault.set_secret("AZURE_KEY", "gia-tri")


def test_xoa_khoa_chua_ton_tai_thi_khong_bao_loi(dung_kho):
    dung_kho()
    vault.delete_secret("ELIS_API_KEY")   # không được ném gì


def test_xoa_khoa_dang_co_thi_bien_mat(dung_kho):
    fake = dung_kho({(vault.SERVICE, "ELIS_API_KEY"): "abc"})
    vault.delete_secret("ELIS_API_KEY")
    assert fake.store == {}
