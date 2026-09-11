"""Cấu hình hệ thống (config).

Đọc key và cấu hình từ .env, cung cấp get_llm() tạo client gọi model qua FPT.
File .env đặt ở gốc dự án (mooc/.env), KHÔNG commit lên git.
"""

from typing import Any

import vault
from langchain_openai import ChatOpenAI
from pydantic import AliasChoices, Field
from pydantic_settings import (BaseSettings, PydanticBaseSettingsSource,
                               SettingsConfigDict)


class WindowsVaultSource(PydanticBaseSettingsSource):
    """Đọc bốn khóa bí mật từ Credential Manager của Windows.

    Trả rỗng nếu không phải Windows hoặc thiếu keyring/backend — xem vault.py.
    """

    def get_field_value(self, field, field_name: str):
        # Lớp cha khai abstract nên phải có; __call__ đọc cả kho một lần.
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        return vault.read_all()


class Settings(BaseSettings):
    """Cấu hình đọc từ .env; tên biến khớp tên trong .env, không phân biệt
    hoa/thường (AZURE_KEY -> settings.azure_key).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # settings_file.py gán thẳng lúc chạy; thiếu cờ này thì giá trị sai
        # kiểu lọt qua, lỗi nổ ở vòng sau.
        validate_assignment=True,
    )

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings,
        dotenv_settings, file_secret_settings,
    ):
        """Ưu tiên: tham số > biến môi trường > kho khóa > .env > file.

        Kho khóa TRÊN .env, nếu không nút "Lưu khóa" trong app vô tác dụng.
        Kho khóa DƯỚI biến môi trường, để `set AZURE_KEY=...` và Docker ghi
        đè được cho một lần chạy.
        """
        return (init_settings, env_settings, WindowsVaultSource(settings_cls),
                dotenv_settings, file_secret_settings)

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
    # temperature thấp để trích xuất ổn định, không sáng tạo.
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
    elis_api_key: str = Field(default="", description="API key ELIS (header apikey)")

    # ===== Chế độ khớp khóa học =====
    # "loose"  : tên người nhập chỉ cần là TẬP CON của tên khóa trên ảnh.
    # "strict" : tên khóa phải trùng khớp hoàn toàn (cùng tập từ).
    course_match_mode: str = Field(default="loose")

    # ===== Luật thời gian hoàn thành =====
    # Ngày hoàn thành ngoài khoảng [đầu, cuối] -> REJECTED. Định dạng
    # YYYY-MM-DD; đổi khi sang năm mới.
    valid_from: str = Field(default="2026-01-01")
    valid_to: str = Field(default="2026-09-30")

    # ===== Gửi báo cáo qua email =====
    # Báo cáo NỘI BỘ cho mentor, không gửi khách hàng eLIS.
    #
    # SMTP_PASSWORD phải là App Password, KHÔNG phải mật khẩu đăng nhập. Gmail:
    # bật Xác minh 2 bước rồi tạo App Password 16 ký tự. Office 365: từ chối
    # mật khẩu thường khi bật MFA, admin phải bật SMTP AUTH riêng từng hộp thư,
    # và Basic Auth cho SMTP AUTH trên Exchange Online hết hạn 31/12/2026.
    #
    # SMTP_USER/SMTP_USERNAME và MAIL_TO/MANAGER_EMAIL là tên thay thế: đặt sai
    # tên thì pydantic lặng lẽ lấy mặc định rỗng, không báo lỗi.
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

    # Số lần thử lại khi gọi API gặp lỗi tạm thời (vd 502, timeout).
    retry_count: int = Field(default=3)
    retry_delay_seconds: int = Field(default=5)
    timeout_seconds: int = Field(default=60)

    # ===== Kho lưu chứng chỉ (phục vụ đánh giá lại) =====
    # Sau khi nộp kết quả, bản ghi rời WAITING nên getCert không trả về nữa —
    # data thật chỉ đi qua MỘT lần. Bật cờ này để giữ ảnh + thông tin getCert
    # cho evaluation/ chạy lại mà không cần eLIS.
    #
    # MẶC ĐỊNH TẮT vì chứng chỉ thật chứa tên, mã và email nhân viên.
    save_certificates: bool = Field(default=False)

    # Thư mục chứa kho, tương đối so với gốc dự án.
    archive_dir: str = Field(default="cert_archive")

    # ===== Thử lại ca hỏng kỹ thuật =====
    # LUẬT DO HR CHỐT: ca hỏng kỹ thuật (eLIS không trả file, Azure timeout,
    # AI lỗi, hết hạn mức) KHÔNG BAO GIỜ bị nộp REJECTED — lỗi hạ tầng làm cả
    # dãy cùng hỏng. Chứng chỉ ở lại WAITING và job thử lại MÃI, mỗi
    # technical_retry_cooldown_minutes một lần; không có nhánh bỏ cuộc.

    # Hỏng tới lần thứ mấy thì email cho người vận hành. TÊN CŨ
    # TECHNICAL_RETRY_MAX vẫn nhận, nhưng nghĩa đã đổi: báo người rồi VẪN
    # THỬ TIẾP.
    technical_alert_after: int = Field(
        default=5,
        validation_alias=AliasChoices("TECHNICAL_ALERT_AFTER",
                                      "TECHNICAL_RETRY_MAX"))

    # Nghỉ bao nhiêu phút trước khi thử lại cùng một chứng chỉ. Quá ngắn thì
    # với POLL_INTERVAL_SECONDS=5 cả năm lượt cháy hết trong vài chục giây,
    # email cảnh báo bay đi trước khi sự cố chớp nhoáng tự khỏi. Mỗi vòng thử
    # mỗi chứng chỉ ĐÚNG MỘT LẦN, nên 2 phút x ngưỡng 5 lần = ~10 phút.
    technical_retry_cooldown_minutes: int = Field(default=2)

    # ===== Chống nộp trùng khóa học =====
    # Nộp lại khóa đã duyệt -> REJECTED ngay, không quét LLM. Hai luồng cùng
    # đẩy chứng chỉ vào eLIS (hệ thống này và đồng bộ tự động của FPT
    # Elearning) nên trùng lặp là chuyện thường. Đối chiếu bằng EMAIL + TÊN
    # KHÓA HỌC; API ① nhận tham số `employeeEmail` nên hỏi thẳng từng người.
    duplicate_check: bool = Field(default=True)


    # ===== Email cảnh báo lỗi hệ thống =====
    # KHÁC mail_to (báo cáo định kỳ): trộn chung thì cảnh báo bị chìm.
    alert_mail_to: str = Field(
        default="hoabd5@fpt.com",
        description="Email nhận cảnh báo lỗi hệ thống, nhiều người cách nhau dấu phẩy",
        validation_alias=AliasChoices("ALERT_MAIL_TO", "ALERT_EMAIL"))

    # Nhịp NHẮC LẠI khi sự cố vẫn còn; KHÔNG làm chậm thư đầu tiên.
    # BẮT BUỘC PHẢI CÓ: job thử lại mỗi 2 phút và mỗi vòng đều tính lại ai
    # vượt ngưỡng, nên không chặn thì một sự cố 6 tiếng sinh 180 thư giống hệt
    # nhau và người nhận sẽ lọc bỏ vĩnh viễn. Đặt 0 là tắt chặn.
    alert_cooldown_hours: int = Field(default=1)

    # ===== Lịch gửi báo cáo tự động =====
    # "off" = chỉ gửi tay bằng send_report.py --send; còn lại là cuối mỗi
    # ngày / tuần / tháng.
    report_schedule: str = Field(default="off")

    # Giờ gửi "HH:MM" giờ Việt Nam.
    report_time: str = Field(default="18:00")

    # Với weekly: thứ mấy (0=Thứ Hai ... 6=Chủ nhật). 4 = Thứ Sáu.
    report_weekday: int = Field(default=4)

    # Với monthly: ngày mấy. 1 = ngày đầu tháng, báo cáo tháng TRƯỚC.
    report_monthday: int = Field(default=1)

    # Mốc gom số liệu biểu đồ: "day" | "week" | "month". Rỗng = tự chọn theo
    # report_schedule (xem scheduler.py).
    report_bucket: str = Field(default="")


# Instance dùng chung. Thiếu key bắt buộc sẽ báo lỗi ngay lúc khởi động.
settings = Settings()


def get_llm(api_key: str | None = None) -> ChatOpenAI:
    """Tạo client gọi model Gemma qua FPT; api_key rỗng thì lấy từ .env."""
    effective_api_key = api_key if api_key else settings.fpt_api_key
    return ChatOpenAI(
        model=settings.fpt_model,
        api_key=effective_api_key,
        base_url=settings.fpt_base_url,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
    )
