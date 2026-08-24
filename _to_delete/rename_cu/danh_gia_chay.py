"""Chạy đánh giá (danh_gia.chay).

    python -m danh_gia.chay mau                  # tạo file nhãn rỗng từ data/
    python -m danh_gia.chay tu-kho               # tạo file nhãn từ kho chứng chỉ THẬT
    python -m danh_gia.chay chay                 # chạy pipeline thật + chấm điểm
    python -m danh_gia.chay chay --file X.csv    # dùng file nhãn khác

Chạy end-to-end: mỗi lần gọi "chay" là một lần gọi LLM thật cho toàn bộ ca
trong file nhãn. Cần mạng công ty và tốn phí — con số in ra ở đầu để bạn biết
trước quy mô.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

GOC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GOC / "src"))
sys.path.insert(0, str(GOC))

import file_utils                                   # noqa: E402
import kho_luu                                      # noqa: E402
import llm_text                                     # noqa: E402
import llm_vision                                   # noqa: E402
import ocr_azure                                    # noqa: E402
import pipeline                                     # noqa: E402
from config import settings                         # noqa: E402
from schemas import ThongTinNhap                    # noqa: E402

from danh_gia import bo_du_lieu, do_phe_duyet, do_trich_xuat   # noqa: E402

logging.basicConfig(level=logging.WARNING,
                    format="%(levelname)s: %(message)s")
logger = logging.getLogger("danh_gia")

FILE_NHAN_MAC_DINH = GOC / "danh_gia" / "bo_nhan.csv"


# ================= sinh file nhãn từ kho chứng chỉ thật =================

def _ma_tu_email(email: str | None) -> str:
    """Lấy phần trước @ làm mã đối chiếu — giống hệt run.py._ma_tu_email()."""
    if not email or "@" not in email:
        return (email or "").strip()
    return email.split("@", 1)[0].strip()


def tao_tu_kho(goc_kho, duong_dan_ra) -> int:
    """Sinh file nhãn từ kho chứng chỉ thật, điền sẵn phần ĐẦU VÀO.

    Điền sẵn: duong_dan_anh + ba cột nhap_* (lấy từ getCert đã lưu kèm).
    Để TRỐNG: toàn bộ cột gt_* — kể cả gt_ket_qua, dù trong kho có sẵn kết
    luận của hệ thống.

    Vì sao cố ý không điền gt_ket_qua: đó là câu trả lời của chính hệ thống
    đang cần đo. Lấy nó làm đáp án chuẩn thì hệ thống luôn đúng 100% và bộ
    đánh giá không còn đo được gì. Nhãn chuẩn phải do người nhìn ảnh mà gán.
    """
    cac_meta = kho_luu.doc_kho(goc_kho)
    if not cac_meta:
        raise FileNotFoundError(
            f"Kho rỗng hoặc chưa có: {goc_kho}\n"
            f"Bật LUU_CHUNG_CHI=1 trong .env rồi chạy run.py để thu thập."
        )

    cac_ca = [
        bo_du_lieu.CaDanhGia(
            ma_ca=m.get("ma_ca") or m["_duong_dan_anh"].stem,
            duong_dan_anh=str(
                m["_duong_dan_anh"].relative_to(GOC)
                if m["_duong_dan_anh"].is_relative_to(GOC)
                else m["_duong_dan_anh"]
            ).replace("\\", "/"),
            nhap_ten_nhan_vien=m.get("employeeName") or "",
            nhap_ten_khoa_hoc=m.get("courseName") or "",
            nhap_ma_nhan_vien=_ma_tu_email(m.get("employeeEmail")),
        )
        for m in cac_meta
    ]
    bo_du_lieu.ghi_bo_du_lieu(cac_ca, duong_dan_ra)
    return len(cac_ca)


# ===================== chạy pipeline trên bộ dữ liệu =====================

def chay_pipeline(cac_ca, azure_client) -> dict:
    """Chạy pipeline thật cho từng ca. Trả về {ma_ca: KetQuaXuLy | None}."""
    ket = {}
    for i, ca in enumerate(cac_ca, start=1):
        duong_dan = GOC / ca.duong_dan_anh
        print(f"  [{i}/{len(cac_ca)}] {ca.ma_ca} ... ", end="", flush=True)

        if not duong_dan.is_file():
            print("KHÔNG THẤY FILE")
            ket[ca.ma_ca] = None
            continue

        try:
            anh_list = file_utils.doc_thanh_anh(duong_dan)
            kq = pipeline.xu_ly(
                anh_list=anh_list,
                nhap=ThongTinNhap(
                    ten_nhan_vien=ca.nhap_ten_nhan_vien,
                    ten_khoa_hoc=ca.nhap_ten_khoa_hoc,
                    ma_nhan_vien=ca.nhap_ma_nhan_vien,
                ),
                trich_tu_anh=llm_vision.trich_tu_anh,
                ocr_nhieu_anh=ocr_azure.ocr_nhieu_anh,
                trich_tu_text=llm_text.trich_tu_text,
                azure_client=azure_client,
            )
            ket[ca.ma_ca] = kq
            print(f"{kq.ket_qua.value} ({kq.tang_xu_ly})")
        except Exception as e:
            # Một ca hỏng KHÔNG được làm chết cả lượt đánh giá — chạy lại từ
            # đầu nghĩa là trả tiền LLM lại cho những ca đã xong.
            logger.warning("Ca %s lỗi: %s", ca.ma_ca, e)
            ket[ca.ma_ca] = None
            print(f"LỖI: {e}")
    return ket


# ============================== in báo cáo ==============================

def _pt(x: float) -> str:
    return f"{x * 100:5.1f}%"


def in_bao_cao_trich_xuat(diem, ca_hong_max=10) -> None:
    print("\n" + "=" * 78)
    print("PHẦN 1 — TRÍCH XUẤT (độ chính xác theo trường)")
    print("=" * 78)
    print(f"{'Trường':<20}{'n':>4}{'Đúng':>8}{'Tuyệt đối':>11}"
          f"{'Sai':>6}{'Bỏ sót':>8}{'Bịa':>6}")
    print("-" * 78)
    for ten, d in diem.items():
        if d.tong == 0:
            print(f"{ten:<20}{0:>4}{'  (chưa gán nhãn)':>33}")
            continue
        print(f"{ten:<20}{d.tong:>4}{_pt(d.do_chinh_xac):>8}"
              f"{_pt(d.do_chinh_xac_tuyet_doi):>11}"
              f"{d.sai:>6}{d.bo_sot:>8}{d.bia:>6}")
    print("-" * 78)
    print("Đúng      = so theo luật của production (bỏ dấu, không phân biệt thứ tự từ,")
    print("            ngày so theo giá trị nên '10 July 2026' = '10/07/2026').")
    print("Tuyệt đối = so chuỗi y hệt từng ký tự.")
    print("Bịa       = ảnh KHÔNG có trường đó mà model vẫn trả về giá trị.")

    co_hong = [(t, d) for t, d in diem.items() if d.ca_hong]
    if co_hong:
        print("\nCa sai cụ thể:")
        for ten, d in co_hong:
            print(f"\n  [{ten}]")
            for ma_ca, ky_vong, du_doan, loai in d.ca_hong[:ca_hong_max]:
                print(f"    {loai:<7} {ma_ca}")
                print(f"            đúng : {ky_vong!r}")
                print(f"            model: {du_doan!r}")
            if len(d.ca_hong) > ca_hong_max:
                print(f"    ... còn {len(d.ca_hong) - ca_hong_max} ca nữa")


def in_bao_cao_phe_duyet(kq) -> None:
    print("\n" + "=" * 78)
    print("PHẦN 2 — PHÊ DUYỆT (precision / recall / F1)")
    print("=" * 78)

    if kq.tong == 0:
        print("Không có ca nào chấm được. Kiểm tra cột gt_ket_qua trong file nhãn.")
        return

    print(f"Số ca chấm được: {kq.tong}     Accuracy: {_pt(kq.accuracy)}"
          f"     Macro-F1: {_pt(kq.macro_f1)}")

    print("\nMa trận nhầm lẫn (hàng = thực tế, cột = model đoán):")
    print(f"{'':>22}{'APPROVED':>12}{'REJECTED':>12}")
    for tt in do_phe_duyet.NHAN:
        hang = "".join(f"{kq.ma_tran[(tt, dd)]:>12}" for dd in do_phe_duyet.NHAN)
        print(f"  thực tế {tt:<12}{hang}")

    print("\nTheo từng nhãn (coi nhãn đó là positive):")
    print(f"{'Nhãn':<12}{'Số ca':>7}{'Precision':>11}{'Recall':>9}{'F1':>8}")
    print("-" * 47)
    for nhan in do_phe_duyet.NHAN:
        d = kq.theo_nhan[nhan]
        print(f"{nhan:<12}{d.so_ca_thuc_te:>7}{_pt(d.precision):>11}"
              f"{_pt(d.recall):>9}{_pt(d.f1):>8}")
    print("-" * 47)
    print("REJECTED recall thấp    = chứng chỉ sai LỌT QUA, bị duyệt oan.")
    print("REJECTED precision thấp = từ chối OAN người làm thật.")

    if kq.theo_tang:
        print("\nTheo tầng xử lý (tầng nào quyết định, và quyết định có đúng không):")
        print(f"{'Tầng':<16}{'Số ca':>7}{'Đúng':>7}{'Tỷ lệ':>9}")
        print("-" * 39)
        for tang, (dung, tong) in sorted(kq.theo_tang.items()):
            print(f"{tang:<16}{tong:>7}{dung:>7}{_pt(dung / tong):>9}")
        print("-" * 39)
        print("Tầng llm2 / llm1_vs_llm2 là những ca phải gọi Azure OCR (tốn tiền).")
        print("Nếu tỷ lệ đúng ở đó không cao hơn llm1 thì tầng 2 chưa đáng giá tiền.")

    if kq.ca_sai:
        print(f"\nCa đoán sai ({len(kq.ca_sai)}):")
        for ma_ca, tt, dd, ly_do, tang in kq.ca_sai:
            print(f"  {ma_ca:<24} đúng={tt:<9} model={dd:<9} [{tang}]")
            print(f"  {'':<24} lý do model: {ly_do}")

    if kq.ca_loi_ky_thuat:
        print(f"\nLoại khỏi phép đo — hỏng kỹ thuật ({len(kq.ca_loi_ky_thuat)} ca):")
        for ma_ca, tang, ly_do in kq.ca_loi_ky_thuat:
            print(f"  {ma_ca:<24} [{tang}] {ly_do}")
        print("  (Đây là lỗi hạ tầng, không phải model đoán sai — nên không")
        print("   tính vào precision/recall. Nhưng nhiều quá thì số đo mất ý nghĩa")
        print("   vì phần lớn bộ dữ liệu đã bị loại.)")

    if kq.ca_thieu_nhan:
        print(f"\nChưa gán gt_ket_qua ({len(kq.ca_thieu_nhan)} ca): "
              f"{', '.join(kq.ca_thieu_nhan[:10])}")


# ================================= CLI =================================

def main() -> int:
    p = argparse.ArgumentParser(description="Đánh giá trích xuất + phê duyệt")
    p.add_argument("lenh", choices=["mau", "tu-kho", "chay"])
    p.add_argument("--file", default=str(FILE_NHAN_MAC_DINH),
                   help="file nhãn CSV")
    p.add_argument("--anh", default=str(GOC / "data"),
                   help="thư mục ảnh (chỉ dùng cho lệnh 'mau')")
    p.add_argument("--kho", default=None,
                   help="thư mục kho (chỉ dùng cho lệnh 'tu-kho')")
    args = p.parse_args()

    if args.lenh == "tu-kho":
        kho = args.kho or str(GOC / settings.thu_muc_kho)
        n = tao_tu_kho(kho, args.file)
        print(f"Đã tạo {args.file} với {n} ca từ kho {kho}.")
        print("\nĐã điền sẵn: duong_dan_anh, nhap_ten_nhan_vien,")
        print("             nhap_ten_khoa_hoc, nhap_ma_nhan_vien")
        print("Cần điền tay: các cột gt_* (thông tin THẬT trên ảnh)")
        print("              và gt_ket_qua (kết luận ĐÚNG phải ra)")
        print("\nLưu ý: gt_ket_qua CỐ Ý để trống dù trong kho có sẵn kết luận")
        print("của hệ thống. Lấy đáp án của model làm đáp án chuẩn thì model")
        print("luôn đúng 100% và bộ đánh giá không đo được gì.")
        return 0

    if args.lenh == "mau":
        n = bo_du_lieu.tao_file_mau(args.anh, args.file)
        print(f"Đã tạo {args.file} với {n} ca.")
        print("\nCần điền tay các cột:")
        print("  nhap_*  : dữ liệu eLIS gửi sang (tên NV, tên khóa học, mã NV)")
        print("  gt_*    : thông tin THẬT trên ảnh")
        print("  gt_ket_qua : APPROVED hoặc REJECTED — kết luận ĐÚNG phải ra")
        print(f"\nĐể trống  = chưa gán nhãn, bỏ qua khi tính điểm.")
        print(f"Ghi '{bo_du_lieu.KHONG_CO}'      = ảnh không in trường đó "
              f"(kỳ vọng model trả về rỗng).")
        return 0

    cac_ca = bo_du_lieu.doc_bo_du_lieu(args.file)
    print(f"Bộ dữ liệu: {len(cac_ca)} ca từ {args.file}")
    print(f"Sắp gọi LLM thật cho {len(cac_ca)} ca. Ctrl+C để hủy.\n")

    azure_client = ocr_azure.tao_client()
    ket = chay_pipeline(cac_ca, azure_client)

    du_doan_trich = {
        ma: (kq.trich_xuat if kq is not None else None)
        for ma, kq in ket.items()
    }
    in_bao_cao_trich_xuat(do_trich_xuat.cham_diem(cac_ca, du_doan_trich))

    khong_chay = do_trich_xuat.so_ca_khong_chay_duoc(cac_ca, ket)
    if khong_chay:
        print(f"\nKhông trích xuất được ({len(khong_chay)} ca): "
              f"{', '.join(khong_chay)}")

    in_bao_cao_phe_duyet(do_phe_duyet.cham_diem(cac_ca, ket))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
