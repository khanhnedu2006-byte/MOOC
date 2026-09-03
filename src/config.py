from langchain_openai import ChatOpenAI
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
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
    course_match_mode: str = Field(default="loose")

    valid_from: str = Field(default="2026-01-01")
    valid_to: str = Field(default="2026-09-30")

    # ===== Gửi báo cáo qua email =====
    smtp_host: str = Field(default="smtp.office365.com")
    smtp_port: int = Field(default=587)
    smtp_user: str = Field(
        default="", description="Email dùng để gửi",
        validation_alias=AliasChoices("SMTP_USER", "SMTP_USERNAME"))
    smtp_password: str = Field(default="", description="App Password")
    mail_from: str = Field(default="", description="Địa chỉ From; rỗng = dùng smtp_user")
    mail_to: str = Field(
        default="", description="Email nhận, nhiều người cách nhau dấu phẩy",
        validation_alias=AliasChoices("MAIL_TO", "MANAGER_EMAIL", "MAIL_DEN"))

    # ===== Tham số vận hành =====
    # Số giây nghỉ giữa mỗi vòng lặp hỏi ELIS.
    poll_interval_seconds: int = Field(default=5)

    batch_size: int = Field(default=1)
    # Số lần thử lại khi gọi API gặp lỗi tạm thời (vd 502, timeout).
    retry_count: int = Field(default=3)
    # Số giây nghỉ giữa các lần thử lại.
    retry_delay_seconds: int = Field(default=5)
    # Timeout (giây) cho lời gọi API.
    timeout_seconds: int = Field(default=60)

    # ===== Kho lưu chứng chỉ (phục vụ đánh giá lại) =====
    save_certificates: bool = Field(default=False)

    # Thư mục chứa kho, tương đối so với gốc dự án.
    archive_dir: str = Field(default="cert_archive")

    # ===== Thử lại ca hỏng kỹ thuật =====
    technical_retry_max: int = Field(default=5)

    technical_retry_cooldown_minutes: int = Field(default=360)

    # ===== Lịch gửi báo cáo tự động =====
    # "off"     : không tự gửi (chỉ gửi tay bằng send_report.py --send)
    # "daily"   : cuối mỗi ngày
    # "weekly"  : cuối tuần
    # "monthly" : cuối tháng
    report_schedule: str = Field(default="off")

    # Giờ gửi, dạng "HH:MM" giờ Việt Nam. Mặc định 18:00 — sau giờ làm, số
    # liệu trong ngày đã đủ.
    report_time: str = Field(default="18:00")

    # Với weekly: gửi vào thứ mấy (0=Thứ Hai ... 6=Chủ nhật). Mặc định 4 =
    # Thứ Sáu, để mentor đọc trước khi nghỉ cuối tuần chứ không phải sáng
    # Thứ Hai lẫn với việc mới.
    report_weekday: int = Field(default=4)

    # Với monthly: gửi vào ngày mấy. 1 = ngày đầu tháng, báo cáo tháng TRƯỚC.
    report_monthday: int = Field(default=1)

    # Mốc gom số liệu trong biểu đồ: "day" | "week" | "month".
    # Rỗng = tự chọn theo report_schedule (xem scheduler.py).
    report_bucket: str = Field(default="")


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