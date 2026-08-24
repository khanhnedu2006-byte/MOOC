"""Chạy thử core end-to-end với một ảnh thật (run_local).

KHÔNG phải vòng lặp ELIS — đây là script chạy tay để kiểm tra core hoạt động
đúng với Gemma + Azure thật, trước khi ghép vào ELIS.

Cách dùng:
    python run_local.py anh.jpg --ten "Nguyễn Văn A" --khoa-hoc "Python cơ bản"
    python run_local.py chung_chi.pdf --ten "Trần Thị B" --khoa-hoc "An toàn thông tin" --ma NV001

Cần có file .env với FPT_API_KEY, AZURE_ENDPOINT, AZURE_KEY.
"""

import os
# Phải đặt TRƯỚC mọi import khác. Sửa lỗi OpenMP xung đột trên Windows
# (OMP Error #15: libiomp5md.dll already initialized) do nhiều thư viện
# cùng nạp OpenMP runtime. An toàn với tác vụ đọc ảnh.
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import argparse
import logging
import sys
from pathlib import Path

# Cho phép import các module trong src/
sys.path.insert(0, str(Path(__file__).parent / "src"))

import file_utils
import llm_text
import llm_vision
import ocr_azure
import pipeline
from schemas import InputInfo


def main():
    parser = argparse.ArgumentParser(description="Chạy thử core với một ảnh chứng chỉ")
    parser.add_argument("image", help="Đường dẫn ảnh hoặc PDF chứng chỉ")
    parser.add_argument("--name", required=True, help="Tên nhân viên (như nhập trên ELIS)")
    parser.add_argument("--course", required=True, help="Tên khóa học (như nhập trên ELIS)")
    parser.add_argument("--code", default=None, help="Mã nhân viên (tùy chọn)")
    parser.add_argument("--verbose", action="store_true", help="In thêm log chi tiết")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    # 1. Chuẩn bị ảnh (kiểm tra định dạng + render PDF nếu cần)
    try:
        images = file_utils.read_as_images(args.image)
    except file_utils.InvalidFileError as e:
        print(f"Lỗi file: {e}")
        return 1

    print(f"Đã đọc {len(images)} ảnh từ {args.image}")

    # 2. Thông tin người nhập
    given = InputInfo(
        employee_name=args.name,
        course_name=args.course,
        employee_code=args.code,
    )

    # 3. Tạo client Azure (chỉ tạo 1 lần)
    azure_client = ocr_azure.create_client()

    # 4. Chạy pipeline — ghép các hàm THẬT vào
    print("Đang xử lý (Gemma đọc ảnh, nếu cần thì Azure + LLM2)...\n")
    verdict = pipeline.process(
        images=images,
        given=given,
        extract_from_image=llm_vision.extract_from_image,
        ocr_images=ocr_azure.ocr_images,
        extract_from_text=llm_text.extract_from_text,
        azure_client=azure_client,
    )

    # 5. In kết quả
    print("=" * 55)
    print(f"KẾT QUẢ:     {verdict.verdict.value}")
    print(f"Lý do:       {verdict.reason}")
    print(f"Tầng xử lý:  {verdict.stage}")
    if verdict.extracted:
        t = verdict.extracted
        print("-" * 55)
        print("Thông tin trích được từ ảnh:")
        print(f"  Tên người nhận: {t.recipient_name}")
        print(f"  Tên chứng chỉ:  {t.certificate_name}")
        print(f"  Ngày nhận:      {t.issue_date}")
        print(f"  Ngày hết hạn:   {t.expiry_date}")
    print("-" * 55)
    print("So với thông tin nhập:")
    print(f"  Tên nhập:      {given.employee_name}")
    print(f"  Khóa học nhập: {given.course_name}")
    print("=" * 55)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())