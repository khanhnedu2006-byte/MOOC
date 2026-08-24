"""Bộ dữ liệu đánh giá (evaluation.dataset).

Định nghĩa một "ca đánh giá" và cách đọc/ghi file nhãn CSV.

Một ca gồm ba phần:
  1. Đầu vào ảnh          : đường dẫn file chứng chỉ.
  2. Đầu vào eLIS         : tên NV / tên khóa học / mã NV mà hệ thống dùng để
                            đối chiếu (tương ứng InputInfo).
  3. Nhãn chuẩn (người gán): thông tin THẬT trên ảnh + kết luận ĐÚNG phải ra.

Vì sao dùng CSV chứ không JSON: người gán nhãn mở bằng Excel được. File ghi
bằng utf-8-sig (có BOM) để Excel trên Windows hiển thị đúng tiếng Việt — thiếu
BOM là Excel đọc ra "Bùi Ä?á»©c HÃ²a".

Ô TRỐNG VÀ DẤU GẠCH KHÁC NHAU — đây là chỗ dễ làm hỏng số đo:
  - Ô TRỐNG  = CHƯA GÁN NHÃN. Trường đó bị BỎ QUA khi tính điểm.
  - Dấu "-"  = ảnh THẬT SỰ không in trường này. Kỳ vọng model trả về None.
Không phân biệt hai thứ này thì "tỷ lệ bịa" trở thành số vô nghĩa: mọi ô chưa
kịp gán nhãn sẽ bị tính là "model bịa ra dữ liệu".
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, asdict
from pathlib import Path

# Giá trị đánh dấu "ảnh không có trường này".
NOT_PRESENT = "-"

# Thứ tự cột trong file CSV. Giữ nguyên thứ tự này để file dễ đọc bằng mắt:
# đầu vào trước, nhãn chuẩn sau.
COLUMNS = [
    "case_id",                 # id tự đặt, vd "coursera_01". Dùng để truy vết.
    "image_path",         # đường dẫn tới file chứng chỉ (tương đối gốc dự án)
    # ----- đầu vào phía eLIS (InputInfo) -----
    "input_employee_name",
    "input_course_name",
    "input_employee_code",
    # ----- nhãn chuẩn cho BƯỚC TRÍCH XUẤT -----
    "gt_recipient_name",
    "gt_certificate_name",
    "gt_certificate_name_alt",
    "gt_issue_date",
    "gt_expiry_date",
    # ----- nhãn chuẩn cho BƯỚC PHÊ DUYỆT -----
    "gt_verdict",            # APPROVED hoặc REJECTED
    "note",               # tùy ý, không dùng để tính điểm
]


@dataclass
class EvalCase:
    """Một ca trong bộ đánh giá."""

    case_id: str
    image_path: str
    input_employee_name: str
    input_course_name: str
    input_employee_code: str
    gt_recipient_name: str | None = None
    gt_certificate_name: str | None = None
    gt_certificate_name_alt: str | None = None
    gt_issue_date: str | None = None
    gt_expiry_date: str | None = None
    gt_verdict: str | None = None
    note: str = ""

    def field_label(self, field_name: str) -> tuple[bool, str | None]:
        """Trả về (co_gan_nhan, gia_tri_ky_vong) cho một trường trích xuất.

        gia_tri_ky_vong = None nghĩa là kỳ vọng model KHÔNG đọc ra gì
        (ảnh không in trường đó).
        """
        raw = getattr(self, f"gt_{field_name}", None)
        if raw is None or str(raw).strip() == "":
            return False, None                  # chưa gán nhãn -> bỏ qua
        raw = str(raw).strip()
        if raw == NOT_PRESENT:
            return True, None                   # đã gán: ảnh không có trường này
        return True, raw


def _clean(value: str | None) -> str | None:
    """Ô trống -> None. Giữ nguyên dấu '-' vì nó MANG NGHĨA (xem docstring đầu file)."""
    if value is None:
        return None
    value = value.strip()
    return value if value else None


def read_dataset(path: str | Path) -> list[EvalCase]:
    """Đọc file nhãn CSV thành danh sách EvalCase.

    Bỏ qua dòng trống và dòng bắt đầu bằng '#' (cho phép ghi chú trong file).
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(
            f"Không tìm thấy file nhãn: {path}\n"
            f"Tạo file mẫu bằng: python -m evaluation.run_eval mau"
        )

    cases: list[EvalCase] = []
    # utf-8-sig: tự bỏ BOM nếu có, đọc được cả file Excel lưu ra lẫn file thường.
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row_count, lines in enumerate(csv.DictReader(f), start=2):
            case_id = (lines.get("case_id") or "").strip()
            if not case_id or case_id.startswith("#"):
                continue
            missing = [c for c in ("image_path", "input_employee_name",
                                 "input_course_name", "input_employee_code")
                     if not (lines.get(c) or "").strip()]
            if missing:
                raise ValueError(
                    f"Dòng {row_count} (case_id={case_id}) thiếu cột bắt buộc: "
                    f"{', '.join(missing)}"
                )
            cases.append(EvalCase(**{
                c: _clean(lines.get(c)) if c not in ("case_id", "note")
                else (lines.get(c) or "").strip()
                for c in COLUMNS
            }))

    if not cases:
        raise ValueError(f"File nhãn không có ca nào dùng được: {path}")
    return cases


def write_dataset(cases: list[EvalCase], path: str | Path) -> None:
    """Ghi danh sách ca ra CSV (utf-8-sig để Excel đọc đúng tiếng Việt)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for case in cases:
            w.writerow({k: ("" if v is None else v) for k, v in asdict(case).items()})


def create_template(image_folder: str | Path, output_path: str | Path) -> int:
    """Quét thư mục ảnh, sinh file nhãn RỖNG để người gán nhãn điền tay.

    Chỉ điền sẵn case_id và image_path — phần còn lại người gán nhãn tự viết.
    KHÔNG tự sinh nhãn bằng cách chạy model: nhãn chuẩn mà lấy từ chính model
    cần đo thì mọi con số sau đó đều vô nghĩa (model luôn "đúng 100%").
    """
    image_folder = Path(image_folder)
    extension = {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
    files = sorted(p for p in image_folder.rglob("*") if p.suffix.lower() in extension)

    cases = [
        EvalCase(
            case_id=f"{i:02d}_{p.stem[:30]}",
            image_path=str(p).replace("\\", "/"),
            input_employee_name="", input_course_name="", input_employee_code="",
        )
        for i, p in enumerate(files, start=1)
    ]
    # write_dataset yêu cầu đủ cột; ở đây cột đầu vào để trống có chủ đích.
    write_dataset(cases, output_path)
    return len(cases)
