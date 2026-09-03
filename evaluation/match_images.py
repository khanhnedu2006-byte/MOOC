"""Ghép file Excel với thư mục ảnh chứng chỉ (evaluation.match_images).

    python -m evaluation.match_images --excel data.xlsx --images anh/

BÀI TOÁN: bộ dữ liệu thật đến dưới dạng một file Excel (mỗi dòng một chứng
chỉ, có email nhân viên + tên khóa học) và một thư mục ảnh đặt tên theo dạng
    <mã NV>_<tên khóa học>.<đuôi>
trong đó mã NV là phần trước dấu @ của email. Hai bên KHÔNG cùng thứ tự.

VÌ SAO KHÔNG GHÉP THEO THỨ TỰ: lệch một dòng là mọi con số đánh giá sau đó
vẫn ra đẹp và vẫn sai — model đọc đúng ảnh nhưng bị chấm bằng nhãn của ảnh
khác, cho ra "sai tên 30%" trong khi thực tế nó đọc đúng. Người đọc sẽ đi
sửa prompt cho một lỗi không tồn tại. Sai kiểu đó không có triệu chứng, nên
phải chặn từ gốc.

CÁCH GHÉP: dựng khóa từ CẢ HAI phía rồi so tập hợp từ đã chuẩn hóa.
    dòng Excel : normalize("hoabd3" + " " + "Learning Microsoft 365 Copilot")
    tên file   : normalize("hoabd3_Learning_Microsoft_365_Copilot")
process_data.normalize() đã biến "_", "-", chữ hoa, dấu tiếng Việt về cùng
một dạng, nên ba cách đặt tên file dưới đây cho ra cùng một khóa:
    hoabd3_Learning Microsoft 365 Copilot for Work.jpg
    hoabd3_Learning_Microsoft_365_Copilot_for_Work.png
    HOABD3-Learning-Microsoft-365-Copilot-for-Work.pdf

KHÔNG TÁCH TÊN FILE THEO DẤU "_" ĐẦU TIÊN: tên khóa học có thể chứa dấu gạch
dưới, và một số username cũng có. Tách sai một lần là ghép sai cả bộ. Dựng
khóa từ toàn bộ chuỗi thì không cần biết ranh giới nằm ở đâu.

KHÔNG TỰ ĐỘNG GHÉP GẦN ĐÚNG. Ca không khớp tuyệt đối chỉ được GỢI Ý kèm số
đo độ giống, người phải tự xác nhận. Ghép mờ tự động chính là cách tạo ra
loại sai không triệu chứng nói ở trên.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path

# Xem giải thích ở evaluation/run_llm2.py: trên Windows dùng conda, hai bản
# libiomp5md.dll bị nạp cùng lúc và chương trình chết với "OMP: Error #15".
# File này chưa nổ vì openpyxl được import muộn bên trong hàm, nhưng rủi ro
# vẫn còn nguyên — đặt sẵn cho chắc, và để mọi điểm vào giống nhau.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from process_data import code_from_email, normalize        # noqa: E402

from evaluation.dataset import EvalCase, write_dataset      # noqa: E402

IMAGE_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}

# Hậu tố trình duyệt / Windows thêm khi tải trùng tên: "... (1).jpg",
# "... - Copy.jpg". Phải bỏ TRƯỚC khi chuẩn hóa, nếu không "1" thành một từ
# trong khóa và ảnh đó không bao giờ khớp được dòng Excel nào.
#
# CỐ Ý KHÔNG bắt dạng "_1": đó không phải hậu tố chuẩn của trình duyệt, mà
# lại ăn mất phần đuôi hợp lệ của tên khóa học — "Excel_Power_Query_101" sẽ
# bị cắt thành "Excel_Power_Query" và không khớp dòng nào. Bản đầu của file
# này có bắt, và test test_ten_khoa_chua_dau_gach_duoi đã đỏ vì đúng lý do đó.
#
# CHỈ 1-2 CHỮ SỐ trong ngoặc. Bản trước viết \(\d+\) và nó ăn luôn NĂM ở cuối
# tên khóa: "Luyện thi PMP_Tư duy & mẹo làm bài (Mindset & Tips) (2025)" bị
# cắt mất "(2025)", trong khi Excel vẫn giữ, nên hai bên lệch đúng MỘT từ và
# không ghép được. Trình duyệt chỉ sinh hậu tố nhỏ — (1), (2)... — nên giới
# hạn 2 chữ số vẫn bắt hết ca thật mà không đụng tới năm 4 chữ số.
_DUPLICATE_SUFFIX = re.compile(r"\s*(\(\d{1,2}\)|-\s*copy)\s*$", re.IGNORECASE)

# Từ khóa để đoán tên cột. Đoán XONG THÌ IN RA cho người kiểm, không đoán thầm.
_EMAIL_HINTS = ("email", "mail", "thu dien tu")
_COURSE_HINTS = ("course", "khoa hoc", "ten khoa", "khoahoc")
_NAME_HINTS = ("employee name", "ho ten", "ten nhan vien", "hoten", "full name",
               "ten nv", "employeename")
# Hai cột này KHÔNG bắt buộc và KHÔNG dùng để chấm điểm. Chúng được chép vào
# cột `note` để người gán nhãn thấy người duyệt thật đã kết luận gì và vì sao.
# CỐ Ý KHÔNG đổ thẳng vào gt_verdict: xem docstring của to_cases().
_STATUS_HINTS = ("submit status", "status", "trang thai")
_COMMENT_HINTS = ("comment", "ghi chu", "ly do")


def key_of(text: str) -> tuple[str, ...]:
    """Khóa ghép: tập hợp từ đã chuẩn hóa, sắp xếp để không phụ thuộc thứ tự.

    Dùng TẬP HỢP (đã bỏ trùng) chứ không dùng dãy: tên file có thể đảo thứ tự
    hoặc lặp từ so với Excel mà vẫn là cùng một chứng chỉ.
    """
    return tuple(sorted(set(normalize(text).split())))


def _clean_stem(path: Path) -> str:
    """Tên file đã bỏ đuôi và bỏ hậu tố trùng lặp."""
    return _DUPLICATE_SUFFIX.sub("", path.stem)


def list_images(folder: str | Path) -> list[Path]:
    """Liệt kê ảnh trong thư mục (kể cả thư mục con)."""
    folder = Path(folder)
    if not folder.is_dir():
        raise NotADirectoryError(f"Không thấy thư mục ảnh: {folder}")
    return sorted(p for p in folder.rglob("*")
                  if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS)


def _tim_cot(headers: list[str], hints: tuple[str, ...],
             loai_tru: set[int] | None = None) -> int | None:
    """Tìm chỉ số cột có tiêu đề chứa một trong các từ khóa. Không thấy -> None."""
    loai_tru = loai_tru or set()
    for i, h in enumerate(headers):
        if i in loai_tru:
            continue
        chuan = normalize(h)
        if any(normalize(t) in chuan for t in hints):
            return i
    return None


def read_excel(path: str | Path, col_email: str | None = None,
               col_course: str | None = None,
               col_name: str | None = None) -> tuple[list[dict], dict]:
    """Đọc Excel thành list dict, tự đoán tên cột nếu không được chỉ định.

    Trả về (danh sách dòng, thông tin cột đã dùng). Trả kèm thông tin cột để
    nơi gọi IN RA cho người kiểm — đoán tên cột mà không cho người xem là
    cách âm thầm đọc nhầm cột và sinh ra một bộ dữ liệu sai từ gốc.
    """
    try:
        from openpyxl import load_workbook
    except ImportError as e:                       # pragma: no cover
        raise ImportError(
            "Thiếu thư viện openpyxl để đọc Excel. Cài bằng:\n"
            "    pip install openpyxl"
        ) from e

    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Không thấy file Excel: {path}")

    # read_only + data_only: đọc GIÁ TRỊ đã tính của công thức, không đọc
    # chuỗi công thức. Thiếu data_only thì ô "=CONCAT(...)" trả về "=CONCAT(...)".
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook.active
    rows = list(sheet.iter_rows(values_only=True))
    workbook.close()

    if not rows:
        raise ValueError(f"File Excel rỗng: {path}")

    headers = [str(h).strip() if h is not None else "" for h in rows[0]]

    def _chi_so(ten_chi_dinh, hints, loai_tru=None):
        if ten_chi_dinh:
            chuan = normalize(ten_chi_dinh)
            for i, h in enumerate(headers):
                if normalize(h) == chuan:
                    return i
            raise ValueError(
                f"Không thấy cột {ten_chi_dinh!r}. Các cột đang có: "
                f"{', '.join(repr(h) for h in headers if h)}")
        return _tim_cot(headers, hints, loai_tru)

    i_email = _chi_so(col_email, _EMAIL_HINTS)
    i_course = _chi_so(col_course, _COURSE_HINTS)
    # Tìm cột tên NV SAU cột khóa học và loại nó ra: tiêu đề "Course Name"
    # cũng chứa chữ "name" nên tìm trước là cướp mất cột khóa học.
    i_name = _chi_so(col_name, _NAME_HINTS,
                     loai_tru={i for i in (i_course, i_email) if i is not None})
    i_status = _tim_cot(headers, _STATUS_HINTS)
    i_comment = _tim_cot(headers, _COMMENT_HINTS)

    thieu = [ten for ten, i in (("email", i_email), ("tên khóa học", i_course))
             if i is None]
    if thieu:
        raise ValueError(
            f"Không đoán được cột {' và '.join(thieu)} trong Excel.\n"
            f"Các cột đang có: {', '.join(repr(h) for h in headers if h)}\n"
            f"Chỉ định tay bằng --col-email và --col-course.")

    def _o(row, i):
        if i is None or i >= len(row) or row[i] is None:
            return ""
        return str(row[i]).strip()

    du_lieu = []
    for so_dong, row in enumerate(rows[1:], start=2):
        email, course = _o(row, i_email), _o(row, i_course)
        if not email and not course:
            continue                              # dòng trống ở cuối sheet
        du_lieu.append({
            "excel_row": so_dong,
            "email": email,
            "course_name": course,
            "employee_name": _o(row, i_name),
            "employee_code": code_from_email(email),
            "elis_status": _o(row, i_status),
            "elis_comment": _o(row, i_comment),
        })

    cot = {"email": headers[i_email], "course": headers[i_course],
           "name": headers[i_name] if i_name is not None else None,
           "status": headers[i_status] if i_status is not None else None,
           "comment": headers[i_comment] if i_comment is not None else None,
           "all": [h for h in headers if h]}
    return du_lieu, cot


def match(rows: list[dict], images: list[Path]) -> dict:
    """Ghép dòng Excel với file ảnh. KHÔNG tự quyết ca mơ hồ.

    Trả về dict gồm:
      matched      : [(dòng, ảnh)] — khớp tuyệt đối và duy nhất
      rows_no_image: dòng Excel không có ảnh nào khớp
      images_no_row: ảnh không có dòng Excel nào khớp
      ambiguous    : khóa bị trùng ở một trong hai phía -> người phải tự xử
      suggestions  : gợi ý cho dòng chưa ghép được, kèm độ giống (0..1)
    """
    khoa_dong: dict[tuple, list[dict]] = {}
    for r in rows:
        khoa_dong.setdefault(key_of(f"{r['employee_code']} {r['course_name']}"),
                             []).append(r)

    khoa_anh: dict[tuple, list[Path]] = {}
    for p in images:
        khoa_anh.setdefault(key_of(_clean_stem(p)), []).append(p)

    matched, ambiguous = [], []
    dong_da_ghep, anh_da_ghep = set(), set()

    for khoa, ds_dong in khoa_dong.items():
        ds_anh = khoa_anh.get(khoa, [])
        if not ds_anh:
            continue
        if len(ds_dong) > 1 or len(ds_anh) > 1:
            # Cùng nhân viên + cùng khóa xuất hiện nhiều lần (nộp lại?) thì
            # không có cách nào biết ảnh nào ứng dòng nào. Chọn bừa là ghép
            # sai một cách không thể phát hiện, nên báo ra để người quyết.
            ambiguous.append({
                "key": " ".join(khoa),
                "rows": [r["excel_row"] for r in ds_dong],
                "images": [str(p) for p in ds_anh],
            })
            continue
        matched.append((ds_dong[0], ds_anh[0]))
        dong_da_ghep.add(ds_dong[0]["excel_row"])
        anh_da_ghep.add(ds_anh[0])

    rows_no_image = [r for r in rows if r["excel_row"] not in dong_da_ghep
                     and not any(r["excel_row"] in a["rows"] for a in ambiguous)]
    images_no_row = [p for p in images if p not in anh_da_ghep
                     and not any(str(p) in a["images"] for a in ambiguous)]

    # Gợi ý cho ca chưa ghép: ảnh nào có tập từ giống nhất (Jaccard).
    # CHỈ GỢI Ý, không tự nhận — xem docstring đầu file.
    #
    # CHỈ XÉT ẢNH CÙNG MÃ NHÂN VIÊN. Bản trước so mọi ảnh với mọi dòng, nên
    # trên dữ liệu thật nó gợi ý toàn ảnh của NGƯỜI KHÁC trùng tên khóa:
    #     dòng 52 (ducdm45,  "Claude in Google Vertex Al")
    #        -> giống 71% với KIENNT128_Claude in Google Vertex Al.png
    # Người đọc làm theo gợi ý đó là gán chứng chỉ của Kiên cho Đức. Bộ đánh
    # giá vẫn chạy, số liệu vẫn đẹp, và cái sai đó không có triệu chứng nào —
    # đúng thứ mà cả file này sinh ra để chặn. Một gợi ý sai người còn tệ hơn
    # là không gợi ý gì.
    suggestions = []
    for r in rows_no_image:
        ma = r["employee_code"].lower()
        cung_ma = [p for p in images_no_row
                   if p.name.split("_")[0].strip().lower() == ma]
        if not cung_ma:
            continue
        k = set(key_of(f"{r['employee_code']} {r['course_name']}"))
        tot_nhat, diem_tot_nhat = None, 0.0
        for p in cung_ma:
            k2 = set(key_of(_clean_stem(p)))
            hop = k | k2
            diem = len(k & k2) / len(hop) if hop else 0.0
            if diem > diem_tot_nhat:
                tot_nhat, diem_tot_nhat = p, diem
        if tot_nhat is not None and diem_tot_nhat >= 0.5:
            suggestions.append({"excel_row": r["excel_row"],
                                "course_name": r["course_name"],
                                "image": str(tot_nhat),
                                "score": round(diem_tot_nhat, 2)})

    return {"matched": matched, "rows_no_image": rows_no_image,
            "images_no_row": images_no_row, "ambiguous": ambiguous,
            "suggestions": suggestions}


def to_cases(matched: list[tuple[dict, Path]]) -> list[EvalCase]:
    """Chuyển cặp đã ghép thành EvalCase, điền sẵn phần ĐẦU VÀO.

    Để TRỐNG toàn bộ cột gt_*: nhãn chuẩn phải do người nhìn ảnh mà gán. Lấy
    nhãn từ chính model cần đo thì model luôn "đúng 100%" và mọi con số sau
    đó vô nghĩa.

    VÌ SAO KHÔNG CHÉP "Submit Status" CỦA eLIS VÀO gt_verdict, dù cột đó có
    sẵn APPROVED/REJECTED: người duyệt thật từ chối vì NHIỀU lý do mà hệ
    thống này không hề kiểm và cũng không được thiết kế để kiểm — nộp trùng
    khóa, HR đã tự ghi nhận giờ từ Udemy, khóa không nằm trong danh mục quy
    đổi MOOC, hệ thống đã đồng bộ sẵn từ FPT Elearning. Hệ thống chỉ so ba
    thứ: tên người, tên khóa, ngày. Lấy Submit Status làm nhãn chuẩn là chấm
    AI trượt vì không phát hiện được thứ chưa bao giờ giao cho nó — precision
    sẽ tụt thảm hại và con số đó nói sai về chất lượng thật.

    Hai cột đó vẫn được chép vào `note` (không tính điểm) để người gán nhãn
    biết bối cảnh: thấy "REJECTED — CB log trùng khóa" thì hiểu ngay đây là
    ca nên gán gt_verdict = APPROVED nếu ảnh thật sự khớp cả ba trường.
    """
    cases = []
    for i, (r, p) in enumerate(sorted(matched, key=lambda x: x[0]["excel_row"]),
                               start=1):
        ghi_chu = [f"excel_row={r['excel_row']}"]
        if r.get("elis_status"):
            ghi_chu.append(f"elis={r['elis_status']}")
        if r.get("elis_comment"):
            ghi_chu.append(f"lý do người duyệt: {r['elis_comment']}")
        cases.append(EvalCase(
            case_id=f"{i:03d}_{r['employee_code'] or 'nocode'}",
            image_path=str(p).replace("\\", "/"),
            input_employee_name=r["employee_name"],
            input_course_name=r["course_name"],
            input_employee_code=r["employee_code"],
            note=" | ".join(ghi_chu),
        ))
    return cases


def ghi_bao_cao_day_du(rows, images, kq, path) -> int:
    """Ghi TOÀN BỘ ca chưa ghép ra CSV — không cắt bớt dòng nào.

    Bảng in ra màn hình cố ý cắt ở 20 dòng cho dễ đọc, nhưng cắt bớt là thứ
    KHÔNG được phép xảy ra với danh sách mang đi đối chiếu: người nhận sẽ
    tưởng 20 là tất cả và bổ sung thiếu. File này là bản đầy đủ.

    Một dòng một ca, có cột "loai" để lọc trong Excel:
      anh_thua        — có ảnh, KHÔNG có dòng Excel nào (cần bổ sung Excel)
      chua_nop_anh    — có dòng Excel, mã NV này không có ảnh nào
      ghep_hut        — mã NV CÓ ảnh khóa khác, hụt đúng khóa này
      mo_ho           — trùng khóa, công cụ không tự chọn
    """
    ma_co_anh = {p.name.split("_")[0].strip().lower() for p in images}
    dong = []

    for p in sorted(kq["images_no_row"]):
        dong.append(["anh_thua", p.name, p.name.split("_")[0].strip(), "", "",
                     "Có ảnh nhưng không có dòng nào trong Excel"])

    for r in sorted(kq["rows_no_image"], key=lambda x: x["excel_row"]):
        ma = r["employee_code"].lower()
        if ma in ma_co_anh:
            khac = "; ".join(sorted(p.name for p in images
                                    if p.name.split("_")[0].strip().lower() == ma))
            dong.append(["ghep_hut", "", r["employee_code"], r["excel_row"],
                         r["course_name"],
                         f"Mã NV có ảnh khóa khác: {khac}"])
        else:
            dong.append(["chua_nop_anh", "", r["employee_code"], r["excel_row"],
                         r["course_name"], "Mã NV này không có ảnh nào"])

    for a in kq["ambiguous"]:
        dong.append(["mo_ho", "; ".join(a["images"]), "", "; ".join(map(str, a["rows"])),
                     a["key"], "Trùng khóa — công cụ KHÔNG tự chọn, cần đổi tên ảnh"])

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["loai", "ten_file_anh", "ma_nv", "dong_excel",
                    "ten_khoa_hoc", "ghi_chu"])
        w.writerows(dong)
    return len(dong)


def _in_bao_cao(rows, images, kq, cot) -> None:
    """In báo cáo ghép. BA SỐ PHẢI CỘNG ĐÚNG thì mới tin được kết quả."""
    print("\n=== Cột đã dùng trong Excel ===")
    print(f"  email        : {cot['email']!r}")
    print(f"  tên khóa học : {cot['course']!r}")
    print(f"  tên nhân viên: {cot['name']!r}"
          + ("" if cot["name"] else "   (không thấy — cột này sẽ để trống)"))
    if cot["status"] or cot["comment"]:
        print(f"  kết luận eLIS: {cot['status']!r} + {cot['comment']!r}")
        print("     -> chép vào cột note để tham khảo. KHÔNG dùng làm gt_verdict:")
        print("        người duyệt từ chối vì cả những lý do hệ thống không kiểm")
        print("        (nộp trùng, HR đã ghi nhận, khóa ngoài danh mục MOOC...).")
    print(f"  (mọi cột có trong file: {', '.join(cot['all'])})")

    so_mo_ho_dong = sum(len(a["rows"]) for a in kq["ambiguous"])
    so_mo_ho_anh = sum(len(a["images"]) for a in kq["ambiguous"])
    print("\n=== Kết quả ghép ===")
    print(f"  Dòng Excel      : {len(rows)}")
    print(f"  File ảnh        : {len(images)}")
    print(f"  Ghép được       : {len(kq['matched'])}")
    print(f"  Dòng thiếu ảnh  : {len(kq['rows_no_image'])}")
    print(f"  Ảnh thừa        : {len(kq['images_no_row'])}")
    print(f"  Mơ hồ (trùng)   : {len(kq['ambiguous'])} nhóm "
          f"({so_mo_ho_dong} dòng, {so_mo_ho_anh} ảnh)")

    # Phép cộng kiểm tra: nếu không khớp thì chính công cụ này đang sai, và
    # người dùng cần biết ngay chứ không phải tin vào con số "ghép được".
    tong_dong = len(kq["matched"]) + len(kq["rows_no_image"]) + so_mo_ho_dong
    tong_anh = len(kq["matched"]) + len(kq["images_no_row"]) + so_mo_ho_anh
    if tong_dong != len(rows) or tong_anh != len(images):
        print(f"\n  !! PHÉP CỘNG KHÔNG KHỚP (dòng {tong_dong}/{len(rows)}, "
              f"ảnh {tong_anh}/{len(images)}) — đừng dùng kết quả này.")

    if kq["ambiguous"]:
        print("\n--- Mơ hồ: cùng mã NV + cùng khóa học xuất hiện nhiều lần ---")
        print("    Công cụ KHÔNG tự chọn. Đổi tên ảnh cho khác nhau rồi chạy lại.")
        for a in kq["ambiguous"][:20]:
            print(f"  * {a['key']}")
            print(f"      dòng Excel: {a['rows']}")
            for p in a["images"]:
                print(f"      ảnh      : {p}")

    if kq["rows_no_image"]:
        # Tách làm hai nhóm. Chúng trông giống nhau trong báo cáo cũ nhưng là
        # hai vấn đề khác hẳn, và nhầm nhóm là đi sửa nhầm chỗ:
        #
        #   - Mã KHÔNG có ảnh nào  -> người này chưa nộp ảnh, hoặc Excel và thư
        #     mục ảnh là hai đợt dữ liệu khác nhau. KHÔNG phải lỗi ghép.
        #   - Mã CÓ ảnh khóa khác  -> ghép hụt đúng khóa này. Đây mới là ca
        #     đáng ngờ: có thể tên khóa viết lệch giữa hai bên.
        ma_co_anh = {p.name.split("_")[0].strip().lower() for p in images}
        chua_nop = [r for r in kq["rows_no_image"]
                    if r["employee_code"].lower() not in ma_co_anh]
        hut = [r for r in kq["rows_no_image"]
               if r["employee_code"].lower() in ma_co_anh]

        if chua_nop:
            print(f"\n--- {len(chua_nop)} dòng: mã NV này KHÔNG có ảnh nào trong thư mục ---")
            print("    (chưa nộp ảnh, hoặc Excel và thư mục ảnh là hai đợt khác nhau —")
            print("     KHÔNG phải lỗi ghép, đừng đi sửa luật so tên)")
            for r in chua_nop[:12]:
                print(f"  dòng {r['excel_row']}: {r['employee_code']} | {r['course_name'][:52]}")
            if len(chua_nop) > 12:
                print(f"  ... còn {len(chua_nop) - 12} dòng nữa")

        if hut:
            print(f"\n--- {len(hut)} dòng: mã NV CÓ ảnh khóa khác, nhưng hụt đúng khóa này ---")
            print("    (đây mới là ca đáng ngờ: tên khóa có thể viết lệch giữa hai bên)")
            for r in hut[:12]:
                khac = sorted(p.name for p in images
                              if p.name.split("_")[0].strip().lower()
                              == r["employee_code"].lower())
                print(f"  dòng {r['excel_row']}: {r['employee_code']} | "
                      f"Excel ghi: {r['course_name'][:46]!r}")
                for t in khac[:3]:
                    print(f"        ảnh đang có: {t[:62]}")
            if len(hut) > 12:
                print(f"  ... còn {len(hut) - 12} dòng nữa")

    if kq["images_no_row"]:
        print(f"\n--- {len(kq['images_no_row'])} ảnh không tìm thấy dòng Excel ---")
        for p in kq["images_no_row"][:20]:
            print(f"  {p.name}")
        if len(kq["images_no_row"]) > 20:
            print(f"  ... còn {len(kq['images_no_row']) - 20} ảnh nữa")

    if kq["suggestions"]:
        print("\n--- Gợi ý (KHÔNG tự ghép — bạn tự xác nhận rồi sửa tên file) ---")
        for s in kq["suggestions"][:20]:
            print(f"  dòng {s['excel_row']} ({s['course_name'][:40]})")
            print(f"      giống {s['score']:.0%} với: {s['image']}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Ghép file Excel dữ liệu thật với thư mục ảnh chứng chỉ")
    p.add_argument("--excel", required=True, help="file .xlsx chứa email + tên khóa học")
    p.add_argument("--images", required=True, help="thư mục chứa ảnh chứng chỉ")
    p.add_argument("--out", default=str(PROJECT_ROOT / "evaluation" / "eval_set.csv"),
                   help="file nhãn CSV sinh ra")
    p.add_argument("--col-email", help="tên cột email (mặc định: tự đoán)")
    p.add_argument("--col-course", help="tên cột tên khóa học (mặc định: tự đoán)")
    p.add_argument("--col-name", help="tên cột họ tên NV (mặc định: tự đoán)")
    p.add_argument("--dry-run", action="store_true",
                   help="chỉ in báo cáo, KHÔNG ghi file nhãn")
    p.add_argument("--report",
                   default=str(PROJECT_ROOT / "evaluation" / "match_report.csv"),
                   help="CSV liệt kê ĐẦY ĐỦ ca chưa ghép (mặc định luôn ghi)")
    args = p.parse_args(argv)

    rows, cot = read_excel(args.excel, args.col_email, args.col_course, args.col_name)
    images = list_images(args.images)
    kq = match(rows, images)
    _in_bao_cao(rows, images, kq, cot)

    # Báo cáo đầy đủ ghi CẢ khi --dry-run: nó là thứ để đọc, không phải bộ dữ
    # liệu. Bảng trên màn hình cắt ở 20 dòng, nên đây mới là bản dùng được để
    # gửi đi đối chiếu.
    n = ghi_bao_cao_day_du(rows, images, kq, args.report)
    print(f"\nĐã ghi danh sách ĐẦY ĐỦ {n} ca chưa ghép: {args.report}")
    print("  (lọc cột 'loai': anh_thua / chua_nop_anh / ghep_hut / mo_ho)")

    if args.dry_run:
        print("\n(--dry-run: chưa ghi file nhãn)")
        return 0

    if not kq["matched"]:
        print("\nKhông ghép được cặp nào — không ghi file nhãn.")
        return 1

    cases = to_cases(kq["matched"])
    write_dataset(cases, args.out)
    print(f"\nĐã ghi {len(cases)} ca vào: {args.out}")
    print("Bước tiếp theo: mở file đó bằng Excel, điền các cột gt_* "
          "(nhãn chuẩn do BẠN nhìn ảnh mà gán), rồi chạy:")
    print(f"    python -m evaluation.run_eval run --file {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
