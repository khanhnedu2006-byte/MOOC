"""Cấu hình hệ thống (config).

Đọc mọi key và cấu hình từ file .env, và cung cấp hàm get_llm() để tạo client
gọi model qua FPT.

Cách dùng ở module khác:
    from config import settings, get_llm
    llm = get_llm()

File .env đặt ở thư mục gốc dự án (mooc/.env), KHÔNG commit lên git.
"""

from langchain_openai import ChatOpenAI
from pydantic import AliasChoices, Field
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
    # "loose"  : người nhập chỉ cần là TẬP CON của tên khóa trên ảnh cũng khớp.
    #           Vd nhập "khóa học code online", ảnh "khóa học code online
    #           (code-bc-06)" -> khớp (ảnh có thừa mã lớp, người nhập thiếu).
    # "strict"  : tên khóa phải trùng KHỚP HOÀN TOÀN (cùng tập từ).
    # Mặc định "loose". Đổi thành "strict" nếu muốn siết chặt.
    course_match_mode: str = Field(default="loose")

    # ===== Luật thời gian hoàn thành =====
    # Chứng chỉ hợp lệ nếu ngày hoàn thành nằm TRONG khoảng [đầu, cuối].
    # Ngoài khoảng -> REJECTED (lý do: thời gian hoàn thành không hợp lệ).
    # Đổi hai giá trị này khi sang năm mới. Định dạng: YYYY-MM-DD.
    valid_from: str = Field(default="2026-01-01")
    valid_to: str = Field(default="2026-09-30")

    # ===== Gửi báo cáo qua email =====
    # Báo cáo NỘI BỘ gửi cho mentor, không phải cho khách hàng eLIS.
    #
    # SMTP_PASSWORD luôn phải là App Password, KHÔNG phải mật khẩu đăng nhập:
    #   - Gmail: bật Xác minh 2 bước trước, rồi tạo App Password 16 ký tự.
    #     Google đã bỏ hẳn "Quyền truy cập của ứng dụng kém an toàn" từ 2022,
    #     nên mật khẩu Gmail thường CHẮC CHẮN bị từ chối.
    #   - Office 365: không nhận mật khẩu thường khi tài khoản bật MFA, và
    #     admin còn phải bật SMTP AUTH riêng cho từng hộp thư.
    #
    # HẠN SỬ DỤNG (chỉ với Office 365): Microsoft đang khai tử Basic Auth cho
    # SMTP AUTH trên Exchange Online, mốc hiện tại là 31/12/2026. Gmail không
    # bị mốc này.
    #
    # NHẬN NHIỀU TÊN BIẾN: SMTP_USER và SMTP_USERNAME là một; MAIL_TO,
    # MANAGER_EMAIL cũng vậy. Lý do: tên biến trong tài liệu/mẫu mỗi nơi một
    # khác, mà đặt sai tên thì pydantic không báo lỗi — nó chỉ lặng lẽ dùng
    # giá trị mặc định rỗng, và bạn nhận được thông báo "thiếu cấu hình" dù
    # đã điền đủ. Chấp nhận cả hai tên rẻ hơn nhiều so với việc đi tìm lỗi đó.
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

    # Số chứng chỉ xử lý trong MỘT lô: tải file -> scan -> nộp kết quả.
    #
    # Lô càng nhỏ thì mất mát càng ít khi có sự cố giữa chừng (rớt mạng,
    # container restart): những lô đã nộp xong vẫn được giữ, chỉ lô đang dở
    # phải làm lại. Đổi lại là gọi API nhiều lần hơn.
    #
    # Đặt 1 nghĩa là nộp ngay sau mỗi chứng chỉ — an toàn nhất, và với lượng
    # chứng chỉ hiện tại thì chi phí gọi API thêm không đáng kể.
    #
    # eLIS giới hạn 20 cặp mỗi request tải file, nên giá trị lớn hơn 20 sẽ
    # bị ép về 20 (xem run.py).
    batch_size: int = Field(default=1)
    # Số lần thử lại khi gọi API gặp lỗi tạm thời (vd 502, timeout).
    retry_count: int = Field(default=3)
    # Số giây nghỉ giữa các lần thử lại.
    retry_delay_seconds: int = Field(default=5)
    # Timeout (giây) cho lời gọi API.
    timeout_seconds: int = Field(default=60)

    # ===== Kho lưu chứng chỉ (phục vụ đánh giá lại) =====
    # Sau khi nộp kết quả, bản ghi trên eLIS rời trạng thái WAITING nên vòng
    # getCert sau KHÔNG trả về nó nữa — data thật chỉ đi qua MỘT lần. Bật cờ
    # này để giữ lại ảnh + thông tin getCert, nhờ đó chạy lại bộ đánh giá
    # (thư mục evaluation/) bao nhiêu lần cũng được mà không cần eLIS.
    #
    # MẶC ĐỊNH TẮT có chủ đích: chứng chỉ thật chứa tên, mã và email nhân
    # viên. Một container production âm thầm tích trữ dữ liệu cá nhân là thứ
    # không ai muốn phát hiện ra về sau. Bật khi cần thu thập, tắt khi chạy thật.
    save_certificates: bool = Field(default=False)

    # Thư mục chứa kho, tương đối so với gốc dự án.
    archive_dir: str = Field(default="cert_archive")

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