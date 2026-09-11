"""Kho khóa Windows (vault) — cất bốn khóa bí mật ra ngoài .env.

Ghi vào Credential Manager. Windows mã hóa bằng DPAPI, khóa gắn với tài
khoản đang đăng nhập.

BẢO VỆ FILE, KHÔNG BẢO VỆ MÁY. Ai đăng nhập được đúng tài khoản Windows đó
vẫn đọc ra được bằng chính thư viện này. Câu mô tả đúng: "khóa không còn nằm
dạng chữ thường trên đĩa".

Chép thư mục dự án sang máy khác thì khóa không đi theo — phải nhập lại.

Docker không đổi gì: mọi hàm trả về rỗng khi không phải Windows hoặc không
có backend, nên config.py rơi về .env.
"""

import logging
import sys

logger = logging.getLogger(__name__)

# Tên "service" trong Credential Manager. Đổi tên này là mất hết khóa cũ.
SERVICE = "MOOC"

# Chỉ bốn giá trị này là bí mật. URL, số phút, luật nghiệp vụ để file thường
# cho dễ đối chiếu khi chuyển UAT/production.
SECRET_NAMES = ("FPT_API_KEY", "AZURE_KEY", "ELIS_API_KEY", "SMTP_PASSWORD")


class VaultError(RuntimeError):
    """Ghi/xóa khóa thất bại. Chỉ ném ra từ set_secret/delete_secret."""


def _load_keyring():
    """Module keyring nếu dùng được thật, ngược lại None.

    Ba lớp chặn, xếp theo thứ tự rẻ tiền trước:

    1. Không phải Windows. Trên Linux `import keyring` kéo theo SecretStorage
       + jeepney rồi hỏi D-Bus; container không có D-Bus.
    2. Chưa cài keyring. Gói này cố ý không nằm trong requirements-job.txt.
    3. Có keyring nhưng không backend nào. Lúc đó keyring trả về
       `backends.fail.Keyring`, mà bản đó vẫn cho gọi set_password rồi mới
       ném lỗi.

    Test thay thẳng hàm này để chạy được trên Linux.
    """
    if sys.platform != "win32":
        return None
    try:
        import keyring
        from keyring.backends.fail import Keyring as NoBackend
    except ImportError:
        logger.debug("Chưa cài keyring — dùng .env.")
        return None
    try:
        if isinstance(keyring.get_keyring(), NoBackend):
            logger.warning("Có keyring nhưng không tìm được backend nào — "
                           "đang dùng .env. Kiểm tra: python -c "
                           "\"import keyring; print(keyring.get_keyring())\"")
            return None
    except Exception as e:
        logger.warning("Không hỏi được kho khóa Windows (%s) — dùng .env.", e)
        return None
    return keyring


def available() -> bool:
    """Máy này có kho khóa dùng được không."""
    return _load_keyring() is not None


def read_all() -> dict[str, str]:
    """Đọc mọi khóa đang có, trả về dict tên-thường -> giá trị.

    Tên viết thường để khớp thẳng tên trường trong Settings.

    Đọc hỏng thì trả về rỗng, không ném lỗi: kho khóa hỏng chỉ nên làm hệ
    thống rơi về .env, không làm job chết lúc khởi động.
    """
    keyring = _load_keyring()
    if keyring is None:
        return {}

    found = {}
    for name in SECRET_NAMES:
        try:
            value = keyring.get_password(SERVICE, name)
        except Exception as e:
            logger.warning("Đọc %s từ kho khóa thất bại (%s) — bỏ qua cả kho, "
                           "dùng .env.", name, e)
            return {}
        if value:
            found[name.lower()] = value
    return found


def describe() -> dict[str, bool]:
    """Tên hoa -> đã có khóa hay chưa. Không trả giá trị.

    Cố ý không có hàm nào đọc ngược giá trị ra giao diện: nhập được, xóa
    được, không xem lại được.
    """
    stored = read_all()
    return {name: name.lower() in stored for name in SECRET_NAMES}


def set_secret(name: str, value: str) -> None:
    """Ghi một khóa. Ném VaultError nếu không ghi được.

    Ném lỗi chứ không nuốt như read_all: người dùng vừa bấm "Lưu" và đang
    chờ kết quả. Báo "đã lưu" khi chưa lưu được là kiểu hỏng khó lần nhất.
    """
    name = str(name).upper()
    if name not in SECRET_NAMES:
        raise VaultError(f"{name} không nằm trong danh sách khóa bí mật.")
    if not str(value).strip():
        raise VaultError("Giá trị rỗng. Muốn bỏ khóa thì dùng delete_secret.")

    keyring = _load_keyring()
    if keyring is None:
        raise VaultError("Máy này không có kho khóa Windows dùng được.")
    try:
        keyring.set_password(SERVICE, name, str(value).strip())
    except Exception as e:
        raise VaultError(f"Không ghi được {name} vào kho khóa: {e}") from e


def delete_secret(name: str) -> None:
    """Xóa một khóa. Chưa có sẵn thì coi như xong, không báo lỗi."""
    name = str(name).upper()
    if name not in SECRET_NAMES:
        raise VaultError(f"{name} không nằm trong danh sách khóa bí mật.")

    keyring = _load_keyring()
    if keyring is None:
        raise VaultError("Máy này không có kho khóa Windows dùng được.")
    try:
        keyring.delete_password(SERVICE, name)
    except Exception as e:
        # keyring ném PasswordDeleteError khi không có gì để xóa — kết quả
        # mong muốn đã đạt.
        logger.debug("Xóa %s: %s", name, e)
