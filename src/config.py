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

    # ===== ELIS Partner API — AI Certificate Scan (3 API) =====
    # Theo tài liệu "Partner Integration Guide — AI Certificate Scan v1.1".
    #
    # Mọi request đều đi qua Kong (API Gateway). Kong định tuyến dựa vào
    # PREFIX trong URL, dẫn tới hai service khác nhau phía sau:
    #   - eLIS API   (nghiệp vụ) -> API ① getCert, API ③ ProcessUserCourseStatus
    #   - FileService (kho file) -> API ② download-certificates-zip
    # Vì vậy có HAI cặp base+prefix, và có thể là hai key khác nhau.

    # --- Nhánh eLIS API: dùng cho API ① và ③ ---
    # Vd: https://apitest.fpt.com + /uat-elis-gw
    api_base_url: str = Field(description="Host Kong dẫn tới eLIS API")
    kong_api_prefix: str = Field(
        default="", description="Route prefix của eLIS API trên Kong, vd /uat-elis-gw"
    )
    api_key: str = Field(description="API key gọi eLIS API (giá trị của header apikey)")

    # --- Nhánh FileService: dùng cho API ② ---
    # Trên UAT thực tế, FileService nằm CÙNG host và CÙNG prefix với eLIS API
    # (đều là https://apitest.fpt.com/uat-elis-gw) — tài liệu tách riêng vì
    # môi trường khác có thể mount ở chỗ khác.
    kong_base_url: str = Field(default="", description="Host Kong dẫn tới FileService")
    kong_file_prefix: str = Field(
        default="", description="Route prefix của FileService trên Kong"
    )
    # Để RỖNG nghĩa là dùng chung key với eLIS API (trường hợp phổ biến khi
    # hai service nằm sau cùng một route Kong). Chỉ điền khi eLIS cấp key riêng.
    kong_api_key: str = Field(default="", description="Key riêng cho FileService, rỗng = dùng chung api_key")

    # Tên header mang API key. Tách thành config vì tài liệu ghi "thường là
    # apikey" — môi trường khác có thể đổi (x-api-key, Ocp-Apim-...).
    api_key_header: str = Field(default="apikey")

    env: str = Field(default="UAT", description="UAT hoặc Production")

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

    # ===== Tham số vận hành =====
    # Hai nhịp nghỉ khác nhau, tùy vòng vừa rồi có việc hay không:
    #
    #   poll_interval_giay      — nghỉ sau khi VỪA xử lý xong một mẻ.
    #       Để ngắn, vì nếu vừa có người nộp thì nhiều khả năng còn cái khác
    #       đang xếp hàng; xử lý nốt cho nhanh.
    #
    #   poll_interval_rong_giay — nghỉ khi KHÔNG có gì để làm.
    #       Để dài hơn, tránh gọi API liên tục vô ích suốt đêm.
    poll_interval_giay: int = Field(default=10)
    poll_interval_rong_giay: int = Field(default=60)

    so_lan_retry: int = Field(default=3)

    # ----- Helper ghép URL -----
    # Đặt ở đây để không nơi nào phải tự nối chuỗi (dễ quên prefix, dễ thừa
    # hoặc thiếu dấu "/").

    def url_api(self, path: str) -> str:
        """URL đầy đủ cho API ① và ③ (nhánh eLIS API).

        Vd: url_api("/api/v1/UserCourse/elearning/getCert")
            -> https://apitest.fpt.com/uat-elis-gw/api/v1/UserCourse/elearning/getCert
        """
        return f"{self.api_base_url.rstrip('/')}{self.kong_api_prefix.rstrip('/')}{path}"

    def url_file(self, path: str) -> str:
        """URL đầy đủ cho API ② (nhánh FileService).

        Ném ValueError nếu chưa cấu hình — thà báo lỗi rõ ràng còn hơn gửi
        request tới URL rỗng rồi nhận lỗi khó hiểu.
        """
        if not self.kong_base_url:
            raise ValueError(
                "Chưa cấu hình KONG_BASE_URL trong .env — không gọi được API "
                "download-certificates-zip. Hỏi đội eLIS host + route prefix "
                "của FileService trên Kong."
            )
        return f"{self.kong_base_url.rstrip('/')}{self.kong_file_prefix.rstrip('/')}{path}"

    @property
    def khoa_file(self) -> str:
        """Key thực tế dùng cho API ② (FileService).

        Trả kong_api_key nếu có, ngược lại dùng chung api_key. Nhờ vậy khi
        hai service nằm sau cùng một route Kong — như trên UAT hiện tại —
        chỉ phải dán key MỘT lần vào .env, không lo dán lệch hai chỗ.
        """
        return self.kong_api_key or self.api_key


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