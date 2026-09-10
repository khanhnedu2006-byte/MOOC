"""Kho khóa của Windows (vault) — cất khóa bí mật ra ngoài file .env.

VẤN ĐỀ ĐANG SỬA: bốn giá trị bí mật của hệ thống nằm trong .env dưới dạng
chữ thường. File đó không được commit, nhưng nó vẫn nằm ngay trong thư mục
dự án — mở Notepad là đọc được, kéo nhầm vào commit là lộ, chụp màn hình lúc
demo là lộ. Đã có một lần key FPT lộ theo đúng kiểu này.

CÁCH XỬ LÝ: đưa bốn giá trị đó vào Credential Manager của Windows. Windows
mã hóa bằng DPAPI, khóa gắn với tài khoản đang đăng nhập.

NÓI CHO ĐÚNG NÓ BẢO VỆ CÁI GÌ: nó bảo vệ FILE, không bảo vệ MÁY. Ai đăng
nhập được đúng tài khoản Windows đó vẫn đọc ra được bằng chính thư viện này.
Đừng báo cáo là "đã bảo mật"; câu đúng là "khóa không còn nằm dạng chữ
thường trên đĩa".

HỆ QUẢ: chép thư mục dự án sang máy khác thì khóa KHÔNG đi theo — phải nhập
lại. Đây là tính năng, không phải lỗi.

BẢN DOCKER KHÔNG ĐỔI GÌ. Mọi hàm ở đây trả về rỗng khi không phải Windows
hoặc khi không có backend, nên config.py rơi về .env đúng như trước.
"""

import logging
import sys

logger = logging.getLogger(__name__)

# Tên "service" trong Credential Manager. Đổi tên này là mất hết khóa cũ.
SERVICE = "MOOC"

# Chỉ bốn giá trị này là bí mật thật. Mọi thứ còn lại (URL, số phút, luật
# nghiệp vụ) cứ để file thường cho dễ đối chiếu khi chuyển UAT/production.
SECRET_NAMES = ("FPT_API_KEY", "AZURE_KEY", "ELIS_API_KEY", "SMTP_PASSWORD")


class VaultError(RuntimeError):
    """Ghi/xóa khóa thất bại. Chỉ ném ra từ set_secret/delete_secret."""


def _load_keyring():
    """Module keyring nếu dùng được thật, ngược lại None.

    Ba lớp chặn, cố ý xếp theo thứ tự rẻ tiền trước:

    1. Không phải Windows -> thôi. Trong container Linux, `import keyring`
       kéo theo SecretStorage + jeepney và đi hỏi D-Bus; không có D-Bus thì
       tùy phiên bản mà nó ném lỗi hoặc treo. Job chạy trong Docker không
       được phép phụ thuộc vào chuyện đó.
    2. Chưa cài keyring -> thôi. Gói này CỐ Ý không nằm trong
       requirements-job.txt.
    3. Có keyring nhưng không tìm được backend nào -> thôi. Trường hợp này
       keyring trả về `backends.fail.Keyring`, mà bản đó vẫn cho gọi
       set_password rồi ném lỗi — im lặng coi như đã lưu là kiểu hỏng tệ
       nhất, nên phải nhận diện nó ở đây.

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
    """Đọc mọi khóa đang có, trả về dict TÊN THƯỜNG -> giá trị.

    Tên trả về viết thường (fpt_api_key) để khớp thẳng tên trường trong
    Settings — bên config.py không phải chuyển đổi gì thêm.

    Đọc hỏng thì trả về rỗng chứ không ném lỗi: kho khóa hỏng không được
    phép làm cả job không khởi động nổi, nó chỉ nên rơi về .env.
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
    """TÊN HOA -> đã có khóa hay chưa. Dùng để vẽ giao diện, không trả giá trị.

    CỐ Ý không có hàm nào trả về giá trị thật cho giao diện. Nhập vào được,
    xóa được, nhưng không đọc ngược ra màn hình — bớt một đường lộ khóa qua
    ảnh chụp màn hình.
    """
    stored = read_all()
    return {name: name.lower() in stored for name in SECRET_NAMES}


def set_secret(name: str, value: str) -> None:
    """Ghi một khóa. Ném VaultError nếu không ghi được.

    Ở đây ném lỗi chứ không nuốt như read_all: người dùng vừa bấm "Lưu" và
    đang chờ biết kết quả. Báo "đã lưu" trong khi không lưu được là cách chắc
    chắn nhất để mất một buổi chiều đi tìm vì sao key vẫn sai.
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
        # keyring ném PasswordDeleteError khi không có gì để xóa. Kết quả
        # mong muốn (khóa không còn trong kho) đã đạt rồi.
        logger.debug("Xóa %s: %s", name, e)
