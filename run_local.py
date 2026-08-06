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
from schemas import ThongTinNhap


def main():
    parser = argparse.ArgumentParser(description="Chạy thử core với một ảnh chứng chỉ")
    parser.add_argument("anh", help="Đường dẫn ảnh hoặc PDF chứng chỉ")
    parser.add_argument("--ten", required=True, help="Tên nhân viên (như nhập trên ELIS)")
    parser.add_argument("--khoa-hoc", required=True, help="Tên khóa học (như nhập trên ELIS)")
    parser.add_argument("--ma", default=None, help="Mã nhân viên (tùy chọn)")
    parser.add_argument("--verbose", action="store_true", help="In thêm log chi tiết")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    # 1. Chuẩn bị ảnh (kiểm tra định dạng + render PDF nếu cần)
    try:
        anh_list = file_utils.doc_thanh_anh(args.anh)
    except file_utils.FileKhongHopLe as e:
        print(f"Lỗi file: {e}")
        return 1

    print(f"Đã đọc {len(anh_list)} ảnh từ {args.anh}")

    # 2. Thông tin người nhập
    nhap = ThongTinNhap(
        ten_nhan_vien=args.ten,
        ten_khoa_hoc=args.khoa_hoc,
        ma_nhan_vien=args.ma,
    )

    # 3. Tạo client Azure (chỉ tạo 1 lần)
    azure_client = ocr_azure.tao_client()

    # 4. Chạy pipeline — ghép các hàm THẬT vào
    print("Đang xử lý (Gemma đọc ảnh, nếu cần thì Azure + LLM2)...\n")
    ket_qua = pipeline.xu_ly(
        anh_list=anh_list,
        nhap=nhap,
        trich_tu_anh=llm_vision.trich_tu_anh,
        ocr_nhieu_anh=ocr_azure.ocr_nhieu_anh,
        trich_tu_text=llm_text.trich_tu_text,
        azure_client=azure_client,
    )

    # 5. In kết quả
    print("=" * 55)
    print(f"KẾT QUẢ:     {ket_qua.ket_qua.value}")
    print(f"Lý do:       {ket_qua.ly_do}")
    print(f"Tầng xử lý:  {ket_qua.tang_xu_ly}")
    if ket_qua.trich_xuat:
        t = ket_qua.trich_xuat
        print("-" * 55)
        print("Thông tin trích được từ ảnh:")
        print(f"  Tên người nhận: {t.ten_nguoi_nhan}")
        print(f"  Tên chứng chỉ:  {t.ten_chung_chi}")
        print(f"  Ngày nhận:      {t.ngay_nhan}")
        print(f"  Ngày hết hạn:   {t.ngay_het_han}")
    print("-" * 55)
    print("So với thông tin nhập:")
    print(f"  Tên nhập:      {nhap.ten_nhan_vien}")
    print(f"  Khóa học nhập: {nhap.ten_khoa_hoc}")
    print("=" * 55)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())