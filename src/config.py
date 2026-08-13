"""Cấu hình hệ thống (config).

Đọc mọi key và cấu hình từ file .env, và cung cấp hàm get_llm() để tạo client
gọi model qua FPT.

Cách dùng ở module khác:
    from config import settings, get_llm
    llm = get_llm()

File .env đặt ở thư mục gốc dự án (mooc/.env), KHÔNG commit lên git.
"""

from langchain_openai import ChatOpenAI
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Toàn bộ cấu hình, đọc từ .env.

    Tên biến khớp với tên trong .env (không phân biệt hoa/thường).
    Ví dụ AZURE_KEY trong .env -> settings.azure_key.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ===== FPT (Gemma) =====
    fpt_api_key: str = Field(description="API key của FPT AI Marketplace")
    fpt_base_url: str = Field(
        default="https://mkp-api.fptcloud.com",
        description="Base URL endpoint FPT",
    )
    fpt_model: str = Field(
        default="gemma-4-31B-it",
        description="Tên model (chú ý chữ B hoa)",
    )
    # temperature thấp cho việc trích xuất (cần ổn định, không sáng tạo).
    llm_temperature: float = Field(default=0.2)
    llm_max_tokens: int = Field(default=2048)

    # ===== Azure Document Intelligence (OCR) =====
    azure_endpoint: str = Field(description="Endpoint Azure Document Intelligence")
    azure_key: str = Field(description="Key Azure Document Intelligence")

    # ===== API ELIS =====
    elis_base_url: str = Field(description="Base URL API của hệ thống ELIS")
    elis_token: str = Field(default="", description="Token xác thực với ELIS")

    # ===== Luật thời gian hoàn thành =====
    # Chứng chỉ hợp lệ nếu ngày hoàn thành nằm TRONG khoảng [đầu, cuối].
    # Ngoài khoảng -> REJECTED (lý do: thời gian hoàn thành không hợp lệ).
    # Đổi hai giá trị này khi sang năm mới. Định dạng: YYYY-MM-DD.
    thoi_gian_hop_le_tu: str = Field(default="2026-01-01")
    thoi_gian_hop_le_den: str = Field(default="2026-09-30")

    # ===== Tham số vận hành =====
    poll_interval_giay: int = Field(default=60)
    so_lan_retry: int = Field(default=3)


# Instance dùng chung. Thiếu key bắt buộc sẽ báo lỗi ngay lúc khởi động.
settings = Settings()


def get_llm(api_key: str | None = None) -> ChatOpenAI:
    """Tạo client gọi model Gemma qua FPT.

    Cho phép truyền api_key riêng (để test hoặc dùng key khác); nếu không
    truyền thì lấy fpt_api_key trong .env.
    """
    effective_api_key = api_key if api_key else settings.fpt_api_key
    return ChatOpenAI(
        model=settings.fpt_model,
        api_key=effective_api_key,
        base_url=settings.fpt_base_url,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
    )