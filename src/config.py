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

    # ===== Thử lại ca hỏng kỹ thuật =====
    #
    # LUẬT DO HR CHỐT: ca hỏng kỹ thuật (eLIS không trả file, Azure timeout,
    # AI lỗi, hết hạn mức) KHÔNG BAO GIỜ bị nộp REJECTED. Lý do của HR: lỗi
    # hệ thống thì cả dãy cùng lỗi, nên nộp REJECTED là từ chối oan hàng loạt
    # chứng chỉ hợp lệ chỉ vì hạ tầng chập trong mười phút.
    #
    # Hệ quả: chứng chỉ ở lại WAITING trên eLIS và job thử lại MÃI, mỗi
    # technical_retry_cooldown_minutes một lần, cho tới khi sự cố khỏi.
    # KHÔNG còn nhánh bỏ cuộc nào.
    #
    # Thứ THAY CHO nhánh bỏ cuộc là CẢNH BÁO: hỏng tới lần thứ
    # technical_alert_after thì gửi email cho người vận hành. Máy đã không tự
    # quyết được thì phải có người biết — nếu không, chứng chỉ nằm WAITING vô
    # thời hạn mà không ai hay, đúng cái tình trạng nhánh bỏ cuộc từng chặn.

    # Hỏng tới lần thứ mấy thì gửi email cảnh báo.
    #
    # TÊN CŨ TECHNICAL_RETRY_MAX VẪN NHẬN, nhưng NGHĨA ĐÃ ĐỔI HẲN: trước là
    # "thử ngần này lần rồi bỏ cuộc, nộp REJECTED", giờ là "hỏng ngần này lần
    # thì báo người, và VẪN THỬ TIẾP". Nhận tên cũ để .env đang chạy không
    # hỏng; đọc tên mới để không ai tưởng nhánh bỏ cuộc vẫn còn.
    technical_alert_after: int = Field(
        default=5,
        validation_alias=AliasChoices("TECHNICAL_ALERT_AFTER",
                                      "TECHNICAL_RETRY_MAX"))

    # Nghỉ bao nhiêu phút trước khi thử lại cùng một chứng chỉ.
    #
    # Không có giãn cách thì với POLL_INTERVAL_SECONDS=5, năm lượt thử cháy
    # hết trong vài chục giây và email cảnh báo bay đi trước khi một sự cố
    # chớp nhoáng kịp tự khỏi.
    #
    # 2 phút: đủ để eLIS/Azure chập vài giây tự qua, mà vẫn phục hồi nhanh —
    # sự cố khỏi lúc nào thì chậm nhất 2 phút sau chứng chỉ được xử lý. Mỗi
    # vòng thử mỗi chứng chỉ ĐÚNG MỘT LẦN, nên với ngưỡng cảnh báo 5 lần thì
    # email đi sau đúng 5 vòng, khoảng 10 phút hỏng liên tục.
    #
    # ĐÃ ĐỔI TỪ 360 (6 tiếng): mốc 6 tiếng hợp lý khi còn nhánh bỏ cuộc, vì
    # khi đó mỗi lượt thử là một bước tiến tới quyết định REJECTED nên phải
    # tiến thật chậm. Giờ không còn quyết định nào để tiến tới; mục tiêu duy
    # nhất là bắt lại sớm nhất khi hạ tầng khỏe lại, nên giãn cách phải ngắn.
    technical_retry_cooldown_minutes: int = Field(default=2)

    # ===== Chống nộp trùng khóa học =====
    # Nhân viên nộp lại một khóa đã được duyệt -> REJECTED ngay, không quét
    # LLM. Có hai luồng cùng đẩy chứng chỉ vào eLIS (hệ thống này, và luồng
    # đồng bộ tự động của FPT Elearning) nên trùng lặp là chuyện thường xảy ra
    # chứ không phải ca hiếm.
    #
    # Đối chiếu bằng EMAIL + TÊN KHÓA HỌC (phương án mentor chốt), không phải
    # employeeId + courseId: getCert không lọc được theo nhân viên nên phải kéo
    # cả danh sách APPROVED về, mà trong đó email là trường luôn có và luôn duy
    # nhất — 208.426 dòng dữ liệu thật không có dòng nào thiếu email, cũng
    # không có email nào ứng với hai mã nhân viên.
    duplicate_check: bool = Field(default=True)

    # Bao lâu nạp lại lịch sử đã duyệt một lần.
    # Nạp lại tốn ~194 request trên production (size trần 1000 bản ghi/trang),
    # nên đừng đặt quá dày. Chỉ mục cũ KHÔNG gây từ chối oan — nó chỉ làm hệ
    # thống bỏ sót ca trùng, tức xử lý y như khi chưa có luật này.
    history_refresh_minutes: int = Field(default=60)

    # ===== Email cảnh báo lỗi hệ thống =====
    # Người nhận cảnh báo. KHÁC mail_to (nơi nhận báo cáo định kỳ): cảnh báo
    # là việc phải xử lý ngay, báo cáo là số liệu đọc cuối ngày. Trộn hai
    # luồng vào một hộp thư thì cảnh báo bị chìm giữa báo cáo.
    alert_mail_to: str = Field(
        default="hoabd5@fpt.com",
        description="Email nhận cảnh báo lỗi hệ thống, nhiều người cách nhau dấu phẩy",
        validation_alias=AliasChoices("ALERT_MAIL_TO", "ALERT_EMAIL"))

    # Nhịp NHẮC LẠI khi sự cố vẫn còn: mỗi ngần này tiếng một thư.
    #
    # KHÔNG làm chậm thư ĐẦU TIÊN. Thư đầu đi ngay khi ca đầu tiên chạm
    # technical_alert_after; giá trị này chỉ quyết định bao lâu thì nhắc lại.
    #
    # BẮT BUỘC PHẢI CÓ: job thử lại mỗi 2 phút và mỗi vòng đều tính lại ai đã
    # vượt ngưỡng — mà ca hỏng 5 lần thì vòng sau hỏng 6 lần, vẫn vượt. Không
    # chặn thì một sự cố kéo dài 6 tiếng sinh ra 180 thư giống hệt nhau. Người
    # nhận sẽ tạo rule lọc bỏ ngay trong ngày đầu, và từ đó cảnh báo mất tác
    # dụng vĩnh viễn, kể cả cho những sự cố sau.
    #
    # 1 tiếng: đủ thưa để không ai lọc bỏ, đủ dày để một sự cố bị bỏ quên vẫn
    # nổi lên lại trong ca trực tiếp theo. Đặt 0 là tắt chặn — đừng làm.
    alert_cooldown_hours: int = Field(default=1)

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
