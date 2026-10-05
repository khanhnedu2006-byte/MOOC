"""Chọn nhà cung cấp OCR cho tầng 2 (ocr).

Pipeline không được biết đang dùng Azure hay OCR.space. Mọi nơi gọi OCR đi
qua đây; đổi nhà cung cấp chỉ là đổi OCR_PROVIDER trong .env hoặc trên tab
Cấu hình, không sửa dòng code nào.

Import NẰM TRONG HÀM, không ở đầu file: chạy OCR.space thì không phải cài SDK
Azure, và ngược lại. Để ở đầu file là buộc cả hai cùng có mặt.
"""

import logging

from config import settings

logger = logging.getLogger(__name__)

PROVIDERS = ("azure", "ocrspace")


class OcrClient:
    """Client kèm tên nhà cung cấp đã tạo ra nó.

    Nhà cung cấp đổi được LÚC ĐANG CHẠY trên tab Cấu hình, trong khi client
    chỉ được tạo một lần ở main(). Không nhớ tên thì sau khi đổi, client Azure
    cũ bị đem đi gọi OCR.space. Giữ tên ở đây để ocr_images tự dựng lại.
    """

    def __init__(self, provider: str, inner):
        self.provider = provider
        self.inner = inner


def provider_name() -> str:
    """Tên nhà cung cấp đang chọn, đã chuẩn hóa. Ném ValueError nếu lạ."""
    name = str(settings.ocr_provider or "").strip().lower()
    if name not in PROVIDERS:
        raise ValueError(
            f"OCR_PROVIDER={settings.ocr_provider!r} không hợp lệ. "
            f"Chọn một trong: {', '.join(PROVIDERS)}.")
    return name


def _provider_module(name: str):
    if name == "ocrspace":
        import ocr_ocrspace
        return ocr_ocrspace
    import ocr_azure
    return ocr_azure


def create_client() -> OcrClient:
    """Tạo client cho nhà cung cấp đang chọn."""
    name = provider_name()
    logger.info("OCR tầng 2 dùng: %s", name)
    return OcrClient(name, _provider_module(name).create_client())


def ocr_images(client, images: list[bytes]) -> str:
    """Đọc chữ từ danh sách ảnh, bằng nhà cung cấp đang chọn.

    Client được tạo bởi nhà cung cấp KHÁC thì dựng lại — người vận hành vừa
    đổi cấu hình giữa chừng.
    """
    name = provider_name()
    if isinstance(client, OcrClient):
        if client.provider != name:
            logger.info("Nhà cung cấp OCR đổi %s -> %s, dựng lại client.",
                        client.provider, name)
            client = create_client()
        client = client.inner
    # client không phải OcrClient (None, hoặc bản giả trong test) thì truyền
    # thẳng xuống — đừng tự tạo thay, nó che mất chỗ gọi sai.
    return _provider_module(name).ocr_images(client, images)
