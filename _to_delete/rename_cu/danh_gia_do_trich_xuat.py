"""PHẦN 1 — Đo bước TRÍCH XUẤT (danh_gia.do_trich_xuat).

Câu hỏi cần trả lời: model đọc ảnh ra đúng bao nhiêu phần trăm, theo TỪNG
TRƯỜNG.

Vì sao không gộp thành một con số duy nhất: "đúng 80%" không cho biết phải
sửa gì. Tách theo trường thì thấy ngay "tên đọc tốt, ngày đọc tệ" và biết
chỗ cần sửa prompt.

Và trong mỗi trường lại tách tiếp bốn kiểu sai, vì cách chữa khác hẳn nhau:

  DUNG    : khớp nhãn chuẩn.
  SAI     : ảnh có, model đọc ra, nhưng đọc sai      -> lỗi đọc (ảnh mờ / OCR).
  BO_SOT  : ảnh có, model trả None                    -> model quá dè dặt.
  BIA     : ảnh KHÔNG có, model vẫn trả về giá trị    -> model bịa (nguy hiểm
            nhất: dữ liệu sai trông như dữ liệu thật).

Gộp ba loại sai làm một thì "sửa prompt cho model bớt dè dặt" và "sửa prompt
cho model bớt bịa" — hai việc NGƯỢC NHAU — trông giống hệt nhau trên báo cáo.

HAI MỨC SO KHỚP, báo cáo cả hai:
  - chuan_hoa: dùng đúng luật so của production (compare.py). Đây là con số
    có ý nghĩa nghiệp vụ, vì nó đo cái thật sự ảnh hưởng tới quyết định duyệt.
  - tuyet_doi: so chuỗi y hệt từng ký tự. Chặt hơn thực tế cần, nhưng cho thấy
    model đọc sạch tới đâu.
Chỉ báo cáo mức chuẩn hóa dễ gây ảo tưởng; chỉ báo cáo mức tuyệt đối thì con
số xấu hơn thực tế mà không ai hành động được.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import compare
import process_data

from .bo_du_lieu import CaDanhGia

# Các trường được chấm điểm, kèm cách so tương ứng.
#   "tu"   : so theo TẬP HỢP TỪ — giống luật production, chịu được đảo thứ tự
#            tên ("A Nguyen Van" = "Nguyen Van A").
#   "ngay" : so theo GIÁ TRỊ NGÀY sau khi parse — "10 July 2026" = "10/07/2026".
#            So chuỗi thô ở đây sẽ báo sai oan cho mọi chứng chỉ nước ngoài.
TRUONG_CHAM_DIEM = {
    "ten_nguoi_nhan":     "tu",
    "ten_chung_chi":      "tu",
    "ten_chung_chi_phu":  "tu",
    "ngay_nhan":          "ngay",
    "ngay_het_han":       "ngay",
}

DUNG, SAI, BO_SOT, BIA = "DUNG", "SAI", "BO_SOT", "BIA"


def _giong_ngay(a: str | None, b: str | None) -> bool:
    """Hai chuỗi ngày có chỉ cùng một ngày không.

    Ngày dạng số MƠ HỒ: "06-12-2026" có thể là 6/12 (kiểu VN) hoặc 12/6 (kiểu
    Mỹ). Theo đúng luật đã chốt ở process_data.ngay_hop_le, ta thử CẢ HAI cách
    hiểu cho mỗi bên; chỉ cần tồn tại một cách hiểu chung là tính khớp.

    Không parse được cả hai bên -> lùi về so chuỗi đã chuẩn hóa, để những định
    dạng lạ (vd "2026 年 02月 24日") vẫn chấm được thay vì bị bỏ trắng.
    """
    ngay_a = {process_data._parse_theo_thu_tu(a, t) for t in ("DMY", "MDY")} if a else set()
    ngay_b = {process_data._parse_theo_thu_tu(b, t) for t in ("DMY", "MDY")} if b else set()
    ngay_a.discard(None)
    ngay_b.discard(None)
    if ngay_a and ngay_b:
        return bool(ngay_a & ngay_b)
    return process_data.chuan_hoa(a) == process_data.chuan_hoa(b) != ""


def _khop(kieu: str, du_doan: str | None, ky_vong: str | None, chat: bool) -> bool:
    """So một giá trị model đọc được với nhãn chuẩn."""
    if chat:
        # Mức tuyệt đối: y hệt từng ký tự, chỉ bỏ khoảng trắng thừa hai đầu.
        return (du_doan or "").strip() == (ky_vong or "").strip()
    if kieu == "ngay":
        return _giong_ngay(du_doan, ky_vong)
    return compare.giong_tap_hop_tu(du_doan, ky_vong)


def _phan_loai(kieu: str, du_doan: str | None, ky_vong: str | None,
               chat: bool) -> str:
    """Xếp một trường vào DUNG / SAI / BO_SOT / BIA."""
    co_du_doan = bool(du_doan and str(du_doan).strip())
    co_ky_vong = ky_vong is not None

    if not co_ky_vong:
        # Nhãn chuẩn nói ảnh KHÔNG có trường này.
        return BIA if co_du_doan else DUNG
    if not co_du_doan:
        return BO_SOT
    return DUNG if _khop(kieu, du_doan, ky_vong, chat) else SAI


@dataclass
class DiemMotTruong:
    """Điểm của MỘT trường trên toàn bộ bộ dữ liệu."""

    ten_truong: str
    tong: int = 0            # số ca ĐÃ gán nhãn cho trường này
    dung: int = 0
    sai: int = 0
    bo_sot: int = 0
    bia: int = 0
    dung_tuyet_doi: int = 0
    # (ma_ca, ky_vong, du_doan, loai) — để soi lại từng ca hỏng.
    ca_hong: list[tuple] = field(default_factory=list)

    @property
    def do_chinh_xac(self) -> float:
        return self.dung / self.tong if self.tong else 0.0

    @property
    def do_chinh_xac_tuyet_doi(self) -> float:
        return self.dung_tuyet_doi / self.tong if self.tong else 0.0


def cham_diem(cac_ca: list[CaDanhGia], du_doan: dict) -> dict[str, DiemMotTruong]:
    """Chấm điểm trích xuất.

    cac_ca  : danh sách ca kèm nhãn chuẩn.
    du_doan : {ma_ca: ThongTinTrichXuat | None} — model đọc được gì.
              None nghĩa là ca đó không chạy được (lỗi tải file, LLM chết...).
              Ca như vậy bị LOẠI khỏi phép đo trích xuất, không tính là sai —
              chúng đo độ ổn định hạ tầng, không đo chất lượng model. Nhưng số
              lượng vẫn được báo riêng để không ai quên chúng tồn tại.
    """
    diem = {t: DiemMotTruong(t) for t in TRUONG_CHAM_DIEM}

    for ca in cac_ca:
        trich = du_doan.get(ca.ma_ca)
        if trich is None:
            continue

        for ten_truong, kieu in TRUONG_CHAM_DIEM.items():
            co_nhan, ky_vong = ca.nhan_truong(ten_truong)
            if not co_nhan:
                continue                     # chưa gán nhãn -> bỏ qua

            gia_tri = getattr(trich, ten_truong, None)
            d = diem[ten_truong]
            d.tong += 1

            loai = _phan_loai(kieu, gia_tri, ky_vong, chat=False)
            if loai == DUNG:
                d.dung += 1
            elif loai == SAI:
                d.sai += 1
            elif loai == BO_SOT:
                d.bo_sot += 1
            else:
                d.bia += 1
            if loai != DUNG:
                d.ca_hong.append((ca.ma_ca, ky_vong, gia_tri, loai))

            if _phan_loai(kieu, gia_tri, ky_vong, chat=True) == DUNG:
                d.dung_tuyet_doi += 1

    return diem


def so_ca_khong_chay_duoc(cac_ca: list[CaDanhGia], du_doan: dict) -> list[str]:
    """Danh sách ma_ca không trích xuất được (để báo cáo riêng)."""
    return [ca.ma_ca for ca in cac_ca if du_doan.get(ca.ma_ca) is None]
