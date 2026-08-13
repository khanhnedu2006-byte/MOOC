"""Kiểm tra kết nối ELIS trước khi chạy run.py (check_elis).

Chạy cái này TRƯỚC. Nó gọi thật lên ELIS nhưng CHỈ ĐỌC — không duyệt, không
từ chối, không sửa gì cả. An toàn để chạy nhiều lần.

    python check_elis.py            # kiểm tra API ① (nhanh, không tải file)
    python check_elis.py --tai-thu  # kiểm tra thêm API ②: tải thử 1 file rồi xóa

--tai-thu vẫn an toàn: chỉ TẢI file về thư mục tạm để xem có tải được
không, in ra manifest, rồi xóa ngay. Không chạy AI, không nộp kết quả,
không đổi trạng thái bản ghi nào trên ELIS.

Không cần key Azure/FPT để chạy script này — chỉ cần API_KEY của ELIS.
"""

import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import argparse
import io
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

_args = argparse.ArgumentParser(description="Kiểm tra kết nối ELIS")
_args.add_argument("--tai-thu", action="store_true",
                   help="Tải thử 1 file qua API ② rồi xóa (không chạy AI, không nộp gì)")
ARGS = _args.parse_args()

OK = "[ OK ]"
LOI = "[LỖI ]"
CANH_BAO = "[ ! ]"


def gach(tieu_de=""):
    print("─" * 64)
    if tieu_de:
        print(tieu_de)
        print("─" * 64)


def che(chuoi: str) -> str:
    """Che key khi in ra màn hình, chỉ hiện 4 ký tự đầu/cuối."""
    if not chuoi:
        return "(rỗng)"
    if len(chuoi) <= 12:
        return chuoi[:2] + "***"
    return f"{chuoi[:4]}...{chuoi[-4:]} ({len(chuoi)} ký tự)"


# =====================================================================
gach("BƯỚC 1 — Kiểm tra file .env")
# =====================================================================

try:
    from config import settings
except Exception as e:
    print(f"{LOI} Không nạp được .env: {e}")
    print()
    print("  Thường do thiếu biến bắt buộc, hoặc chưa cài package.")
    print("  Thử: pip install -r requirements.txt")
    raise SystemExit(1)

print(f"{OK} Nạp .env thành công")
print(f"       Môi trường     : {settings.env}")
print(f"       API_BASE_URL   : {settings.api_base_url}")
print(f"       KONG_API_PREFIX: {settings.kong_api_prefix}")
print(f"       Header key     : {settings.api_key_header}")
print(f"       API_KEY        : {che(settings.api_key)}")

loi_config = []
if not settings.api_key or "DAN_KEY" in settings.api_key.upper() or "DIEN_KEY" in settings.api_key.upper():
    loi_config.append("API_KEY vẫn còn là placeholder — cần dán key thật mentor gửi.")

if loi_config:
    print()
    for l in loi_config:
        print(f"{LOI} {l}")
    print()
    print("  Mở file .env, tìm dòng API_KEY= và thay bằng giá trị")
    print("  đứng sau 'apikey:' trong curl mentor gửi.")
    raise SystemExit(1)

url_get = settings.url_api("/api/v1/UserCourse/elearning/getCert")
print(f"{OK} URL API ① sẽ gọi:")
print(f"       {url_get}")

# =====================================================================
print()
gach("BƯỚC 2 — Gọi thử API ① getCert (chỉ đọc, không thay đổi gì)")
# =====================================================================

import client

try:
    body = client.lay_mot_trang_cho_duyet(page=1, size=5)
except Exception as e:
    print(f"{LOI} Gọi API thất bại:")
    print(f"       {type(e).__name__}: {e}")
    print()
    print("  Cách đọc lỗi:")
    print("   • 401 / Unauthorized  -> API_KEY sai hoặc dán thiếu ký tự")
    print("   • 403 / Forbidden     -> IP máy bạn chưa nằm trong allowlist của eLIS,")
    print("                            hoặc key không có quyền. Báo mentor IP hiện tại.")
    print("   • 404 / Not Found     -> KONG_API_PREFIX sai (đang dùng:",
          f"{settings.kong_api_prefix!r})")
    print("   • Timeout / ConnectionError -> mạng công ty chặn, thử VPN")
    raise SystemExit(1)

items = body.get("data") or []
tong = body.get("totalRecords") or 0

print(f"{OK} Gọi API ① THÀNH CÔNG — key và IP đều hợp lệ")
print(f"       Tổng chứng chỉ đang chờ duyệt: {tong}")

if not items:
    print()
    print(f"{CANH_BAO} Hiện không có chứng chỉ nào ở trạng thái WAITING.")
    print("       Không phải lỗi — chỉ là chưa ai nộp, hoặc đã duyệt hết.")
    print("       Nhờ mentor tạo một bản ghi thử trên UAT để test tiếp.")
else:
    print()
    print(f"       {len(items)} bản ghi đầu tiên:")
    print()
    for i, it in enumerate(items, 1):
        print(f"       [{i}] {it.get('employeeName')}  (mã NV: {it.get('employeeId')!r})")
        print(f"           Khóa học : {it.get('courseName')}")
        print(f"           Nộp lúc  : {it.get('submitDatetime')}")
        print(f"           File     : {it.get('certificateName')}")
        print(f"           id       : {it.get('id')}")
        print(f"           cert_id  : {it.get('certificate_id')}")
        print()

    # Kiểm tra dữ liệu có đủ field để chạy pipeline không.
    thieu = []
    for it in items:
        for f in ("id", "certificate_id", "courseId", "employeeId"):
            if not it.get(f):
                thieu.append(f"{f} (bản ghi {it.get('id') or '?'})")
    if thieu:
        print(f"{CANH_BAO} Một số bản ghi thiếu field: {', '.join(set(thieu))}")
        print("       Những bản ghi này sẽ bị ELIS từ chối ở API ③.")
    else:
        print(f"{OK} Mọi bản ghi đều đủ field bắt buộc (id/certificate_id/courseId/employeeId)")

    # Mã NV có số 0 đầu là ca dễ sai nhất -> nhắc rõ.
    co_so_0 = [it["employeeId"] for it in items if str(it.get("employeeId", "")).startswith("0")]
    if co_so_0:
        print(f"{OK} Có mã NV bắt đầu bằng số 0 ({co_so_0[0]!r}) — code đã giữ dạng chuỗi")

# =====================================================================
print()
gach("BƯỚC 3 — Kiểm tra API ② download-zip")
# =====================================================================

if not settings.kong_base_url:
    print(f"{LOI} CHƯA cấu hình KONG_BASE_URL / KONG_FILE_PREFIX trong .env.")
    san_sang = False
else:
    print(f"{OK} Đã cấu hình:")
    print(f"       {settings.url_file('/api/v1/files/download-certificates-zip')}")
    if settings.kong_api_key:
        print(f"       Key riêng: {che(settings.kong_api_key)}")
    else:
        print(f"       Key: dùng chung với API ①/③ ({che(settings.api_key)})")
    san_sang = True

    if not ARGS.tai_thu:
        print()
        print(f"{CANH_BAO} Chưa gọi thử API ② (thêm --tai-thu để tải thử 1 file).")
    elif not items:
        print()
        print(f"{CANH_BAO} Không có bản ghi WAITING nào để tải thử.")
    else:
        print()
        print("       Đang tải thử 1 file (sẽ xóa ngay sau khi kiểm tra)...")
        mau = items[0]
        try:
            zip_bytes = client.tai_zip_chung_chi([{
                "UserCourseId": mau["id"],
                "certificate_id": mau["certificate_id"],
            }])
        except Exception as e:
            print(f"{LOI} Tải thất bại: {type(e).__name__}: {e}")
            print()
            print("  Cách đọc lỗi:")
            print("   • file_102 -> cặp id/certificate_id không khớp. Chạy lại")
            print("                 check_elis.py để lấy danh sách mới.")
            print("   • file_105 -> body rỗng (lỗi code, báo lại)")
            print("   • 403      -> key không có quyền trên FileService")
            san_sang = False
        else:
            print(f"{OK} Tải ZIP thành công ({len(zip_bytes):,} bytes)")
            try:
                with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                    ten_entry = zf.namelist()
                    manifest = json.loads(zf.read("manifest.json"))
            except Exception as e:
                print(f"{LOI} ZIP hỏng hoặc thiếu manifest.json: {e}")
                san_sang = False
            else:
                print(f"{OK} Giải nén được, có {len(ten_entry)} entry")
                for m in manifest:
                    if m.get("success"):
                        print(f"{OK} File đọc được: {m.get('originalFileName')}")
                        print(f"       entryName: {m.get('entryName')}")
                    else:
                        print(f"{CANH_BAO} Soft-fail {m.get('errorCode')} "
                              f"— eLIS không có file này trên đĩa.")
                        print("       Chứng chỉ kiểu này sẽ bị REJECTED, không gọi AI.")
                # Không ghi ra đĩa — chỉ đọc trong bộ nhớ rồi bỏ.
                print(f"{OK} Đã kiểm tra xong, không lưu file nào xuống đĩa.")

# =====================================================================
print()
gach("KẾT LUẬN")
# =====================================================================

if san_sang:
    print(f"{OK} Cấu hình ELIS đã đủ.")
    print()
    if not ARGS.tai_thu:
        print("  Nên chạy thêm bước tải thử trước khi chạy thật:")
        print("      python check_elis.py --tai-thu")
        print()
    print("  Sau đó chạy 1 vòng THẬT rồi dừng (có duyệt/từ chối trên ELIS):")
    print("      python run.py --once --verbose")
    print()
    print("  Chạy liên tục:")
    print("      python run.py")
    print()
    print("  Xem thống kê bất cứ lúc nào:")
    print("      python run.py --thong-ke")
    print()
    print(f"  {CANH_BAO} run.py GHI THẬT lên ELIS (đổi trạng thái WAITING ->")
    print("     APPROVED/REJECTED). Nên chạy --once trên UAT với vài bản ghi")
    print("     trước, kiểm tra kết quả rồi mới cho chạy liên tục.")
else:
    print(f"{LOI} CHƯA chạy được run.py — xem lỗi ở trên.")

print()
