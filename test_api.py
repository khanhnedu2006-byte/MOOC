"""Script test từng API ELIS riêng lẻ (test_api).

Chạy từng phần để kiểm tra kết nối ELIS trước khi ghép vào run.py.

    python test_api.py 1      # test API ① getCert (chỉ đọc, an toàn)
    python test_api.py 2      # test API ② download ZIP (cần id từ ①)
    # KHÔNG có test ③ ở đây vì ③ thay đổi trạng thái thật.

Cần .env có ELIS_BASE_URL, ELIS_FILE_BASE_URL, ELIS_API_KEY.
"""

import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import client


def test_api_1():
    """Test API ① — lấy danh sách chờ duyệt."""
    print("=== Test API ① getCert (status=WAITING) ===\n")
    try:
        ds = client.lay_danh_sach_cho_duyet(page=1, size=10)
    except client.ElisError as e:
        print(f"LỖI: {e}")
        return

    print(f"Lấy được {len(ds)} chứng chỉ chờ duyệt.\n")
    for i, item in enumerate(ds[:5], 1):
        print(f"[{i}] id={item.get('id')}")
        print(f"    Nhân viên: {item.get('employeeName')} (mã {item.get('employeeId')})")
        print(f"    Khóa học:  {item.get('courseName')}")
        print(f"    certificate_id: {item.get('certificate_id')}")
        print(f"    courseId:       {item.get('courseId')}")
        print()

    if ds:
        print("=> Copy id + certificate_id của 1 item để test API ②.")


def test_api_2():
    """Test API ② — cần nhập id thủ công từ kết quả API ①."""
    print("=== Test API ② download ZIP ===\n")
    print("Nhập UserCourseId và certificate_id (lấy từ API ①):")
    uc_id = input("  UserCourseId: ").strip()
    cert_id = input("  certificate_id: ").strip()

    if not uc_id or not cert_id:
        print("Thiếu id, dừng.")
        return

    try:
        ket = client.tai_zip_chung_chi([
            {"UserCourseId": uc_id, "certificate_id": cert_id}
        ])
    except client.ElisError as e:
        print(f"LỖI: {e}")
        return

    print(f"\nTải được {len(ket)} file chứng chỉ.")
    for item in ket:
        print(f"  File: {item['ten_file']} ({len(item['anh_bytes'])} bytes)")
        # Lưu file ra đĩa để kiểm tra
        ten = f"test_cert_{item['userCourseId'][:8]}.bin"
        Path(ten).write_bytes(item["anh_bytes"])
        print(f"  Đã lưu: {ten}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Dùng: python test_api.py [1|2]")
        sys.exit(1)
    if sys.argv[1] == "1":
        test_api_1()
    elif sys.argv[1] == "2":
        test_api_2()
    else:
        print("Chỉ hỗ trợ 1 hoặc 2.")