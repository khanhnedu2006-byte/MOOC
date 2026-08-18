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
    # Base URL cho API nghiệp vụ (getCert, ProcessUserCourseStatus).
    elis_base_url: str = Field(
        default="https://apitest.fpt.com/uat-elis-gw",
        description="Base URL API nghiệp vụ ELIS",
    )
    # Base URL cho download ZIP (có thể trùng elis_base_url ở môi trường test).
    elis_file_base_url: str = Field(
        default="https://apitest.fpt.com/uat-elis-gw",
        description="Base URL service download ZIP chứng chỉ",
    )
    # API key — ELIS cấp qua kênh bảo mật, gửi trong header 'apikey'.
    elis_api_key: str = Field(default="", description="API key ELIS (header apikey)")

    # ===== Chế độ khớp khóa học =====
    # "long"  : người nhập chỉ cần là TẬP CON của tên khóa trên ảnh cũng khớp.
    #           Vd nhập "khóa học code online", ảnh "khóa học code online
    #           (code-bc-06)" -> khớp (ảnh có thừa mã lớp, người nhập thiếu).
    # "chat"  : tên khóa phải trùng KHỚP HOÀN TOÀN (cùng tập từ).
    # Mặc định "long". Đổi thành "chat" nếu muốn siết chặt.
    che_do_khop_khoa_hoc: str = Field(default="long")

    # ===== Luật thời gian hoàn thành =====
    # Chứng chỉ hợp lệ nếu ngày hoàn thành nằm TRONG khoảng [đầu, cuối].
    # Ngoài khoảng -> REJECTED (lý do: thời gian hoàn thành không hợp lệ).
    # Đổi hai giá trị này khi sang năm mới. Định dạng: YYYY-MM-DD.
    thoi_gian_hop_le_tu: str = Field(default="2026-01-01")
    thoi_gian_hop_le_den: str = Field(default="2026-09-30")

    # ===== Gửi báo cáo =====
    # "teams" hoặc "email". Mặc định teams vì không cần mật khẩu và không
    # phụ thuộc việc công ty có bật SMTP AUTH hay không.
    kenh_bao_cao: str = Field(default="teams")

    # --- Teams (Workflows webhook) ---
    # Tạo: mở kênh Teams -> ... -> Workflows -> mẫu "Post to a channel when
    # a webhook request is received" -> chọn kênh -> Create -> copy URL.
    # Cách cũ qua Connectors đã bị Microsoft TẮT VĨNH VIỄN trong 5/2026.
    #
    # URL này CHÍNH LÀ thứ xác thực — ai có nó cũng đăng bài vào kênh được.
    # Coi như mật khẩu, đừng commit lên git.
    teams_webhook_url: str = Field(default="")

    # ===== Gửi báo cáo qua email =====
    # Báo cáo NỘI BỘ gửi cho mentor, không phải cho khách hàng eLIS.
    #
    # SMTP_PASSWORD phải là App Password, KHÔNG phải mật khẩu đăng nhập —
    # Office 365 không nhận mật khẩu thường khi tài khoản có bật MFA.
    #
    # HẠN SỬ DỤNG: Microsoft đang khai tử Basic Auth cho SMTP AUTH trên
    # Exchange Online, mốc hiện tại là 31/12/2026. Sau đó phải chuyển sang
    # Microsoft Graph API hoặc SMTP relay nội bộ của công ty.
    smtp_host: str = Field(default="smtp.office365.com")
    smtp_port: int = Field(default=587)
    smtp_user: str = Field(default="", description="Email công ty dùng để gửi")
    smtp_password: str = Field(default="", description="App Password")
    mail_tu: str = Field(default="", description="Địa chỉ From; rỗng = dùng smtp_user")
    mail_den: str = Field(default="", description="Email nhận, nhiều người cách nhau dấu phẩy")

    # ===== Tham số vận hành =====
    # Số giây nghỉ giữa mỗi vòng lặp hỏi ELIS (khi không còn việc).
    poll_interval_giay: int = Field(default=60)
    # Số lần thử lại khi gọi API gặp lỗi tạm thời (vd 502, timeout).
    so_lan_retry: int = Field(default=3)
    # Số giây nghỉ giữa các lần thử lại.
    retry_delay_giay: int = Field(default=5)
    # Timeout (giây) cho lời gọi API.
    timeout_giay: int = Field(default=60)


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