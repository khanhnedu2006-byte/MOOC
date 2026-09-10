"""Thiết lập chung cho toàn bộ test (conftest).

VẤN ĐỀ ĐANG SỬA: config.Settings() bắt buộc phải có FPT_API_KEY, AZURE_ENDPOINT
và AZURE_KEY. Ba biến đó nằm trong .env, mà .env KHÔNG được commit (nó chứa
key thật). Hệ quả: người clone repo về chạy `pytest` sẽ nhận ba lỗi
ValidationError ngay ở bước thu thập test — chưa test nào kịp chạy — và dễ
kết luận là dự án hỏng.

Đặt giá trị giả ở đây làm test chạy được trên máy sạch, và cũng làm test
KHÔNG phụ thuộc vào key cá nhân của người chạy: hai người chạy cùng bộ test
phải ra cùng kết quả.

Dùng setdefault chứ không gán đè: ai đã export biến môi trường thật thì giá
trị của họ vẫn được giữ.

pytest nạp conftest.py TRƯỚC mọi test module, nên việc gán ở mức module này
xảy ra trước khi `import config` chạy lần đầu.
"""

import os

# Giá trị giả, cố ý trông rõ là giả. Không test nào ở đây gọi mạng thật:
# mọi lời gọi API và LLM đều được thay bằng bản giả trong từng test.
_GIA = {
    "FPT_API_KEY": "test-key-khong-that",
    "AZURE_ENDPOINT": "https://test.cognitiveservices.azure.com/",
    "AZURE_KEY": "test-key-khong-that",
    "ELIS_BASE_URL": "https://test.invalid/elis",
    "ELIS_FILE_BASE_URL": "https://test.invalid/elis",
    "ELIS_API_KEY": "test-key-khong-that",
    # Tắt hẳn hai thứ có thể gây tác dụng phụ ra ngoài khi chạy test.
    "REPORT_SCHEDULE": "off",
    "SAVE_CERTIFICATES": "0",
    # Luật chống nộp trùng phải KÉO LỊCH SỬ TỪ eLIS trước khi chạy. Bật mặc
    # định thì mọi test đi qua process_one_round đều gọi mạng thật — chậm,
    # phụ thuộc mạng, và bẩn. Test nào cần luật này thì tự bật lấy
    # (xem tests/test_duplicate.py).
    "DUPLICATE_CHECK": "0",
}

for _k, _v in _GIA.items():
    os.environ.setdefault(_k, _v)
