"""PHẦN 2 — Đo bước PHÊ DUYỆT (danh_gia.do_phe_duyet).

Bài toán hai nhãn: APPROVED / REJECTED. Tính precision, recall, F1.

BÁO CÁO CHO CẢ HAI NHÃN, không chọn sẵn một nhãn làm "positive". Lý do: hai
nhãn trả lời hai câu hỏi nghiệp vụ khác nhau và cả hai đều quan trọng.

  Coi REJECTED là positive:
    - recall    = trong số chứng chỉ THẬT SỰ không hợp lệ, ta bắt được bao nhiêu?
                  Bỏ sót = chứng chỉ sai lọt qua, được duyệt oan.
    - precision = trong số ta từ chối, bao nhiêu cái đáng bị từ chối?
                  Thấp = từ chối oan người làm thật.

  Coi APPROVED là positive: hai câu hỏi trên đảo vai.

Trong hệ thống này lỗi ĐẮT hơn là DUYỆT OAN (chứng chỉ sai được công nhận),
nên khi phải chọn một con số để tối ưu, thường là recall của REJECTED. Nhưng
đó là quyết định nghiệp vụ, không phải quyết định kỹ thuật — nên module này
đưa ra đủ số liệu, không tự chọn thay.

LOẠI CA LỖI KỸ THUẬT RA KHỎI PHÉP ĐO — quan trọng:
pipeline trả REJECTED cho cả những ca không đọc được file, LLM chết, Azure
timeout (tang_xu_ly = llm1_loi / tang2_loi / file_loi / ...). Những cái đó
KHÔNG phải phán đoán của model. Để lẫn vào thì một hôm mạng công ty chập là
recall của REJECTED tăng vọt, báo cáo trông đẹp lên trong khi model không hề
tốt hơn. Chúng được đếm và báo cáo RIÊNG.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Các tầng nghĩa là "hỏng kỹ thuật", không phải kết luận nghiệp vụ.
TANG_LOI_KY_THUAT = {
    "llm1_loi", "tang2_loi", "loi_he_thong",
    "file_loi", "loi_tai_file", "khong_co_file",
}

NHAN = ("APPROVED", "REJECTED")


@dataclass
class DiemMotNhan:
    """Precision / recall / F1 khi coi MỘT nhãn là positive."""

    nhan: str
    tp: int = 0      # đoán đúng nhãn này
    fp: int = 0      # đoán nhãn này nhưng thực tế là nhãn kia
    fn: int = 0      # thực tế là nhãn này nhưng đoán ra nhãn kia

    @property
    def precision(self) -> float:
        mau = self.tp + self.fp
        return self.tp / mau if mau else 0.0

    @property
    def recall(self) -> float:
        mau = self.tp + self.fn
        return self.tp / mau if mau else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def so_ca_thuc_te(self) -> int:
        """Số ca THỰC TẾ mang nhãn này (support)."""
        return self.tp + self.fn


@dataclass
class KetQuaPheDuyet:
    """Toàn bộ số đo của bước phê duyệt."""

    theo_nhan: dict[str, DiemMotNhan]
    ma_tran: dict[tuple[str, str], int]      # (thuc_te, du_doan) -> số ca
    so_dung: int = 0
    tong: int = 0
    ca_sai: list[tuple] = field(default_factory=list)   # (ma_ca, thuc_te, du_doan, ly_do, tang)
    ca_loi_ky_thuat: list[tuple] = field(default_factory=list)  # (ma_ca, tang, ly_do)
    ca_thieu_nhan: list[str] = field(default_factory=list)
    theo_tang: dict[str, list[int]] = field(default_factory=dict)  # tang -> [dung, tong]

    @property
    def accuracy(self) -> float:
        return self.so_dung / self.tong if self.tong else 0.0

    @property
    def macro_f1(self) -> float:
        """Trung bình F1 của hai nhãn, KHÔNG trọng số theo số ca.

        Dùng macro chứ không phải micro vì bộ dữ liệu thường lệch (nhiều
        REJECTED hơn APPROVED, hoặc ngược lại). Micro sẽ để nhãn đông ca át
        nhãn ít ca, và một model chỉ đoán bừa nhãn đông vẫn ra điểm cao.
        """
        return sum(d.f1 for d in self.theo_nhan.values()) / len(self.theo_nhan)


def cham_diem(cac_ca, du_doan: dict) -> KetQuaPheDuyet:
    """Chấm điểm phê duyệt.

    cac_ca  : danh sách CaDanhGia (đã có gt_ket_qua).
    du_doan : {ma_ca: KetQuaXuLy | None}. None = không chạy được.
    """
    kq = KetQuaPheDuyet(
        theo_nhan={n: DiemMotNhan(n) for n in NHAN},
        ma_tran={(tt, dd): 0 for tt in NHAN for dd in NHAN},
    )

    for ca in cac_ca:
        thuc_te = (ca.gt_ket_qua or "").strip().upper()
        if thuc_te not in NHAN:
            kq.ca_thieu_nhan.append(ca.ma_ca)
            continue

        ket = du_doan.get(ca.ma_ca)
        tang = getattr(ket, "tang_xu_ly", None) if ket is not None else None
        ly_do = getattr(ket, "ly_do", "") if ket is not None else "không chạy được"

        # Ca hỏng kỹ thuật: đếm riêng, KHÔNG đưa vào ma trận nhầm lẫn.
        if ket is None or tang in TANG_LOI_KY_THUAT:
            kq.ca_loi_ky_thuat.append((ca.ma_ca, tang or "-", ly_do))
            continue

        du = ket.ket_qua.value if hasattr(ket.ket_qua, "value") else str(ket.ket_qua)
        if du not in NHAN:
            kq.ca_loi_ky_thuat.append((ca.ma_ca, tang or "-", f"nhãn lạ: {du}"))
            continue

        kq.tong += 1
        kq.ma_tran[(thuc_te, du)] += 1

        thong_ke_tang = kq.theo_tang.setdefault(tang or "-", [0, 0])
        thong_ke_tang[1] += 1

        if du == thuc_te:
            kq.so_dung += 1
            thong_ke_tang[0] += 1
            kq.theo_nhan[thuc_te].tp += 1
        else:
            kq.ca_sai.append((ca.ma_ca, thuc_te, du, ly_do, tang or "-"))
            kq.theo_nhan[du].fp += 1        # đoán nhãn này mà sai
            kq.theo_nhan[thuc_te].fn += 1   # nhãn thật bị bỏ sót

    return kq
