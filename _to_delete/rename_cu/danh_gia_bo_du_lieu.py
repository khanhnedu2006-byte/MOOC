"""Bộ dữ liệu đánh giá (danh_gia.bo_du_lieu).

Định nghĩa một "ca đánh giá" và cách đọc/ghi file nhãn CSV.

Một ca gồm ba phần:
  1. Đầu vào ảnh          : đường dẫn file chứng chỉ.
  2. Đầu vào eLIS         : tên NV / tên khóa học / mã NV mà hệ thống dùng để
                            đối chiếu (tương ứng ThongTinNhap).
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
KHONG_CO = "-"

# Thứ tự cột trong file CSV. Giữ nguyên thứ tự này để file dễ đọc bằng mắt:
# đầu vào trước, nhãn chuẩn sau.
COT = [
    "ma_ca",                 # id tự đặt, vd "coursera_01". Dùng để truy vết.
    "duong_dan_anh",         # đường dẫn tới file chứng chỉ (tương đối gốc dự án)
    # ----- đầu vào phía eLIS (ThongTinNhap) -----
    "nhap_ten_nhan_vien",
    "nhap_ten_khoa_hoc",
    "nhap_ma_nhan_vien",
    # ----- nhãn chuẩn cho BƯỚC TRÍCH XUẤT -----
    "gt_ten_nguoi_nhan",
    "gt_ten_chung_chi",
    "gt_ten_chung_chi_phu",
    "gt_ngay_nhan",
    "gt_ngay_het_han",
    # ----- nhãn chuẩn cho BƯỚC PHÊ DUYỆT -----
    "gt_ket_qua",            # APPROVED hoặc REJECTED
    "ghi_chu",               # tùy ý, không dùng để tính điểm
]


@dataclass
class CaDanhGia:
    """Một ca trong bộ đánh giá."""

    ma_ca: str
    duong_dan_anh: str
    nhap_ten_nhan_vien: str
    nhap_ten_khoa_hoc: str
    nhap_ma_nhan_vien: str
    gt_ten_nguoi_nhan: str | None = None
    gt_ten_chung_chi: str | None = None
    gt_ten_chung_chi_phu: str | None = None
    gt_ngay_nhan: str | None = None
    gt_ngay_het_han: str | None = None
    gt_ket_qua: str | None = None
    ghi_chu: str = ""

    def nhan_truong(self, ten_truong: str) -> tuple[bool, str | None]:
        """Trả về (co_gan_nhan, gia_tri_ky_vong) cho một trường trích xuất.

        gia_tri_ky_vong = None nghĩa là kỳ vọng model KHÔNG đọc ra gì
        (ảnh không in trường đó).
        """
        raw = getattr(self, f"gt_{ten_truong}", None)
        if raw is None or str(raw).strip() == "":
            return False, None                  # chưa gán nhãn -> bỏ qua
        raw = str(raw).strip()
        if raw == KHONG_CO:
            return True, None                   # đã gán: ảnh không có trường này
        return True, raw


def _lam_sach(gia_tri: str | None) -> str | None:
    """Ô trống -> None. Giữ nguyên dấu '-' vì nó MANG NGHĨA (xem docstring đầu file)."""
    if gia_tri is None:
        return None
    gia_tri = gia_tri.strip()
    return gia_tri if gia_tri else None


def doc_bo_du_lieu(duong_dan: str | Path) -> list[CaDanhGia]:
    """Đọc file nhãn CSV thành danh sách CaDanhGia.

    Bỏ qua dòng trống và dòng bắt đầu bằng '#' (cho phép ghi chú trong file).
    """
    duong_dan = Path(duong_dan)
    if not duong_dan.is_file():
        raise FileNotFoundError(
            f"Không tìm thấy file nhãn: {duong_dan}\n"
            f"Tạo file mẫu bằng: python -m danh_gia.chay mau"
        )

    cac_ca: list[CaDanhGia] = []
    # utf-8-sig: tự bỏ BOM nếu có, đọc được cả file Excel lưu ra lẫn file thường.
    with duong_dan.open(encoding="utf-8-sig", newline="") as f:
        for so_dong, dong in enumerate(csv.DictReader(f), start=2):
            ma_ca = (dong.get("ma_ca") or "").strip()
            if not ma_ca or ma_ca.startswith("#"):
                continue
            thieu = [c for c in ("duong_dan_anh", "nhap_ten_nhan_vien",
                                 "nhap_ten_khoa_hoc", "nhap_ma_nhan_vien")
                     if not (dong.get(c) or "").strip()]
            if thieu:
                raise ValueError(
                    f"Dòng {so_dong} (ma_ca={ma_ca}) thiếu cột bắt buộc: "
                    f"{', '.join(thieu)}"
                )
            cac_ca.append(CaDanhGia(**{
                c: _lam_sach(dong.get(c)) if c not in ("ma_ca", "ghi_chu")
                else (dong.get(c) or "").strip()
                for c in COT
            }))

    if not cac_ca:
        raise ValueError(f"File nhãn không có ca nào dùng được: {duong_dan}")
    return cac_ca


def ghi_bo_du_lieu(cac_ca: list[CaDanhGia], duong_dan: str | Path) -> None:
    """Ghi danh sách ca ra CSV (utf-8-sig để Excel đọc đúng tiếng Việt)."""
    duong_dan = Path(duong_dan)
    duong_dan.parent.mkdir(parents=True, exist_ok=True)
    with duong_dan.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COT)
        w.writeheader()
        for ca in cac_ca:
            w.writerow({k: ("" if v is None else v) for k, v in asdict(ca).items()})


def tao_file_mau(thu_muc_anh: str | Path, duong_dan_ra: str | Path) -> int:
    """Quét thư mục ảnh, sinh file nhãn RỖNG để người gán nhãn điền tay.

    Chỉ điền sẵn ma_ca và duong_dan_anh — phần còn lại người gán nhãn tự viết.
    KHÔNG tự sinh nhãn bằng cách chạy model: nhãn chuẩn mà lấy từ chính model
    cần đo thì mọi con số sau đó đều vô nghĩa (model luôn "đúng 100%").
    """
    thu_muc_anh = Path(thu_muc_anh)
    duoi = {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
    files = sorted(p for p in thu_muc_anh.rglob("*") if p.suffix.lower() in duoi)

    cac_ca = [
        CaDanhGia(
            ma_ca=f"{i:02d}_{p.stem[:30]}",
            duong_dan_anh=str(p).replace("\\", "/"),
            nhap_ten_nhan_vien="", nhap_ten_khoa_hoc="", nhap_ma_nhan_vien="",
        )
        for i, p in enumerate(files, start=1)
    ]
    # ghi_bo_du_lieu yêu cầu đủ cột; ở đây cột đầu vào để trống có chủ đích.
    ghi_bo_du_lieu(cac_ca, duong_dan_ra)
    return len(cac_ca)
