"""Các field CẦN THÊM vào class Settings trong src/config.py hiện có.
 
Không phải file độc lập chạy được — copy các dòng Field bên dưới vào đúng
class Settings, và xoá/thay elis_base_url + elis_token cũ (đó là placeholder
chưa khớp với spec thật của tài liệu "AI Certificate Scan v1.1").
 
Lý do tách api_base_url/kong_base_url: API ① getCert và API ③
ProcessUserCourseStatus đi qua base khác với API ② download-zip (Kong
FileService). Xem mục 2 và mục 4.1/5.1 của tài liệu.
"""
 
# ===== ELIS Partner API (AI Certificate Scan) =====
# API ① getCert và API ③ ProcessUserCourseStatus dùng cặp này:
api_base_url: str = Field(description="Host API nghiệp vụ eLIS, vd https://apitest.fpt.com")
kong_api_prefix: str = Field(
    default="", description="Path mount route API qua Kong, vd /elis/api hoặc rỗng"
)
api_key: str = Field(description="API key nghiệp vụ (eLIS.API) — header apikey")
 
# API ② download-certificates-zip dùng cặp Kong FileService riêng:
kong_base_url: str = Field(description="Host download ZIP (Kong FileService)")
kong_file_prefix: str = Field(
    default="", description="Path mount route FileService, vd /elis/fileservice"
)
kong_api_key: str = Field(description="API key riêng cho Kong FileService download")
 
api_key_header: str = Field(default="apikey", description="Tên header chứa API key")
env: str = Field(default="UAT", description="UAT hoặc Production")
 
# poll_interval_giay, so_lan_retry: ĐÃ CÓ SẴN trong config.py, dùng luôn,
# không cần thêm lại.
 
 
# ----- Helper để build URL, có thể thêm làm method của Settings hoặc hàm rời -----
def url_api(self, path: str) -> str:
    """Ghép URL cho API ① / ③ (API_BASE_URL + KONG_API_PREFIX + path)."""
    return f"{self.api_base_url.rstrip('/')}{self.kong_api_prefix}{path}"
 
 
def url_file(self, path: str) -> str:
    """Ghép URL cho API ② (KONG_BASE_URL + KONG_FILE_PREFIX + path)."""
    return f"{self.kong_base_url.rstrip('/')}{self.kong_file_prefix}{path}"