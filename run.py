"""Vòng lặp chính nối ELIS với core AI scan (run).

Đây là vòng lặp ELIS THẬT. Khác với:
  - run_local.py : chạy tay MỘT ảnh từ đĩa, không gọi ELIS.
  - web_demo.py  : giao diện web demo, không gọi ELIS.

Luồng mỗi vòng poll, đúng thứ tự trong tài liệu Partner Integration Guide:

    ① getCert(status=WAITING)     -> danh sách chứng chỉ chờ duyệt
       (bỏ những cái đã xử lý xong ở vòng trước, tra trong SQLite)
    ② download-certificates-zip   -> tải ảnh, mỗi lần tối đa 20 cặp
       giải nén -> đọc manifest.json -> chạy pipeline.xu_ly() cho từng ảnh
    ③ ProcessUserCourseStatus     -> nộp APPROVED/REJECTED, mỗi lần tối đa 500

Cách dùng:
    python run.py --once --verbose   # chạy 1 vòng rồi thoát (nên dùng khi test)
    python run.py                    # chạy liên tục cho tới khi Ctrl+C
    python run.py --thong-ke         # chỉ xem thống kê đã xử lý, không gọi API

Nhịp của vòng lặp liên tục (chỉnh trong .env):
    Có việc  -> làm xong nghỉ POLL_INTERVAL_GIAY giây rồi kiểm tra lại ngay,
                vì thường còn cái khác đang xếp hàng.
    Rảnh     -> nghỉ POLL_INTERVAL_RONG_GIAY giây rồi kiểm tra lại.

Cần .env đầy đủ (xem .env.example).
"""

import os
# Phải đặt TRƯỚC mọi import khác — xem giải thích trong run_local.py.
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import argparse
import io
import json
import logging
import shutil
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
sys.path.insert(0, str(Path(__file__).parent / "database"))

import client
import database
import file_utils
import llm_text
import llm_vision
import ocr_azure
import pipeline
from config import settings
from schemas import ThongTinNhap

logger = logging.getLogger(__name__)

# comment: câu ngắn gọn HIỂN THỊ CHO HỌC VIÊN trên giao diện ELIS.
# comment_cer: lý do kỹ thuật chi tiết, dùng để rà soát/đối chiếu.
# Tách hai cái vì lý do kỹ thuật từ pipeline khá dài và khó hiểu với học viên.
COMMENT_APPROVED = "Chứng chỉ hợp lệ, đã được xác nhận tự động."
COMMENT_REJECTED = (
    "Chứng chỉ chưa hợp lệ. Vui lòng kiểm tra lại thông tin đã khai và ảnh/PDF đã tải lên."
)
COMMENT_LOI_FILE = "Không đọc được file chứng chỉ đã tải lên. Vui lòng tải lại."

GIOI_HAN_COMMENT_CER = 1000  # cắt bớt phòng khi lý do quá dài


def _dto_ket_qua(item: dict, ket_qua: str, comment: str, ly_do: str) -> dict:
    """Dựng một phần tử cho request API ③, lấy id từ item gốc của getCert.

    employeeId phải giữ nguyên dạng CHUỖI — mã NV có thể có số 0 ở đầu
    ("00332383"), ép sang số sẽ mất số 0 và ELIS trả về failList.
    """
    return {
        "id": item["id"],
        "certificate_id": item["certificate_id"],
        "status": ket_qua,
        "courseId": item.get("courseId"),
        "employeeId": str(item["employeeId"]),
        "comment": comment,
        "comment_cer": (ly_do or "")[:GIOI_HAN_COMMENT_CER],
    }


def _scan_mot_chung_chi(duong_dan_anh: Path, item: dict, azure_client) -> tuple[str, str, str]:
    """Chạy AI cho một file chứng chỉ. Trả (ket_qua, ly_do, tang_xu_ly).

    Đây chính là đoạn gọi core — giống hệt run_local.py, chỉ khác là thông
    tin đối chiếu lấy từ ELIS thay vì gõ tay trên dòng lệnh.
    """
    anh_list = file_utils.doc_thanh_anh(duong_dan_anh)
    nhap = ThongTinNhap(
        ten_nhan_vien=item.get("employeeName") or "",
        ten_khoa_hoc=item.get("courseName") or "",
        ma_nhan_vien=str(item["employeeId"]),
    )
    kq = pipeline.xu_ly(
        anh_list=anh_list,
        nhap=nhap,
        trich_tu_anh=llm_vision.trich_tu_anh,
        ocr_nhieu_anh=ocr_azure.ocr_nhieu_anh,
        trich_tu_text=llm_text.trich_tu_text,
        azure_client=azure_client,
    )
    return kq.ket_qua.value, kq.ly_do or "", kq.tang_xu_ly or ""


def _xu_ly_entry(entry: dict, item: dict, thu_muc_giai_nen: Path, azure_client) -> dict:
    """Xử lý MỘT entry trong manifest.json -> trả dto sẵn sàng nộp ELIS."""
    if not entry.get("success"):
        # Soft-fail file_103/104: ELIS không có file trên đĩa/DB.
        # Không có gì để đọc -> REJECTED luôn, KHÔNG gọi AI (đỡ tốn tiền).
        ket_qua = "REJECTED"
        ly_do = f"Không tải được file từ ELIS (errorCode={entry.get('errorCode')})"
        tang = "soft_fail_zip"
        comment = COMMENT_LOI_FILE
    else:
        duong_dan = thu_muc_giai_nen / entry["entryName"]
        try:
            ket_qua, ly_do, tang = _scan_mot_chung_chi(duong_dan, item, azure_client)
            comment = COMMENT_APPROVED if ket_qua == "APPROVED" else COMMENT_REJECTED
        except Exception as e:
            # Lỗi bất ngờ khi scan (file hỏng, API LLM chết...) -> REJECTED,
            # nhưng ghi rõ lý do để người vận hành soát lại thủ công.
            logger.exception("Lỗi scan UserCourseId=%s", item["id"])
            ket_qua, ly_do, tang = "REJECTED", f"Lỗi hệ thống khi scan: {e}", "loi_he_thong"
            comment = COMMENT_REJECTED

    with database.ket_noi() as conn:
        database.ghi_ket_qua_xu_ly(
            conn,
            user_course_id=item["id"],
            certificate_id=item["certificate_id"],
            course_id=item.get("courseId"),
            employee_id=str(item["employeeId"]),
            employee_name=item.get("employeeName") or "",
            course_name=item.get("courseName") or "",
            ket_qua=ket_qua,
            ly_do=ly_do,
            tang_xu_ly=tang,
        )

    logger.info("  %s | %s | %s", ket_qua, item.get("employeeName"), ly_do[:80])
    return _dto_ket_qua(item, ket_qua, comment, ly_do)


def _xu_ly_batch(batch: list[dict], azure_client) -> list[dict]:
    """Tải ZIP cho một batch (≤20 item), giải nén, scan từng cái."""
    payload = [
        {"UserCourseId": it["id"], "certificate_id": it["certificate_id"]}
        for it in batch
    ]
    tra_cuu = {it["id"]: it for it in batch}

    try:
        zip_bytes = client.tai_zip_chung_chi(payload)
    except client.ElisApiError as e:
        # Fail-fast: cả batch không có ZIP (file_101/102/105). Không nộp gì
        # cho batch này — để nguyên WAITING, vòng poll sau thử lại.
        logger.error("Bỏ qua batch %d item, lỗi tải ZIP: %s", len(batch), e)
        return []
    except Exception as e:
        logger.error("Bỏ qua batch %d item, lỗi bất ngờ khi tải ZIP: %s", len(batch), e)
        return []

    thu_muc = Path(tempfile.mkdtemp(prefix="elis_zip_"))
    ket_qua: list[dict] = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            zf.extractall(thu_muc)

        manifest = json.loads((thu_muc / "manifest.json").read_text(encoding="utf-8"))

        for entry in manifest:
            item = tra_cuu.get(entry.get("userCourseId"))
            if item is None:
                logger.warning("manifest có userCourseId lạ: %s", entry.get("userCourseId"))
                continue
            ket_qua.append(_xu_ly_entry(entry, item, thu_muc, azure_client))
    finally:
        # Tài liệu mục 4.6 bước 5: xóa file tạm, không giữ lại PII thừa.
        shutil.rmtree(thu_muc, ignore_errors=True)

    return ket_qua


def _nop_ket_qua(danh_sach_dto: list[dict]) -> None:
    """Chia batch ≤500 và gọi API ③, ghi successList/failList vào SQLite."""
    for batch in client.chia_batch(danh_sach_dto, client.MAX_DTO_PROCESS_STATUS):
        try:
            kq = client.cap_nhat_trang_thai(batch)
        except Exception as e:
            logger.error("Nộp kết quả lỗi cho cả batch %d item: %s", len(batch), e)
            continue

        with database.ket_noi() as conn:
            for ok in kq["successList"]:
                database.ghi_ket_qua_nop_elis(conn, ok["id"], thanh_cong=True)

            for fail in kq["failList"]:
                # failList bọc dạng {"data": {...}, "message": "..."} — mục 5.4.
                du_lieu = fail.get("data") or fail
                fail_id = du_lieu.get("id")
                message = fail.get("message", "")
                logger.warning("ELIS từ chối id=%s: %s", fail_id, message)
                if fail_id:
                    database.ghi_ket_qua_nop_elis(conn, fail_id, thanh_cong=False, message=message)

        logger.info(
            "Nộp batch: %d thành công, %d bị từ chối.",
            len(kq["successList"]), len(kq["failList"]),
        )


def chay_mot_vong() -> tuple[int, int]:
    """Chạy đúng một vòng: poll -> tải -> scan -> nộp.

    Trả (so_tim_thay, so_xu_ly):
      - so_tim_thay: số chứng chỉ MỚI lấy được từ ELIS (đã loại cái làm rồi)
      - so_xu_ly   : số thực sự scan xong và nộp lại

    Trả hai số vì chúng khác nhau khi tải ZIP lỗi: tìm thấy 5 cái nhưng tải
    hỏng nên xử lý được 0. Lúc đó vòng lặp vẫn phải coi là "có việc" để nghỉ
    ngắn rồi thử lại ngay, chứ không ngủ dài như khi thật sự rảnh.
    """
    items = client.lay_toan_bo_cho_duyet()

    with database.ket_noi() as conn:
        items = [it for it in items if not database.da_xu_ly(conn, it["id"])]

    if not items:
        return 0, 0

    logger.info("Bắt đầu xử lý %d chứng chỉ.", len(items))
    azure_client = ocr_azure.tao_client()

    tat_ca_dto: list[dict] = []
    for batch in client.chia_batch(items, client.MAX_ITEMS_DOWNLOAD_ZIP):
        tat_ca_dto.extend(_xu_ly_batch(batch, azure_client))

    if tat_ca_dto:
        _nop_ket_qua(tat_ca_dto)

    return len(items), len(tat_ca_dto)


def vong_lap_lien_tuc() -> int:
    """Chạy mãi: có việc thì làm, không có thì nghỉ rồi kiểm tra lại.

    Nhịp nghỉ lấy từ .env:
      - Vừa có việc      -> nghỉ POLL_INTERVAL_GIAY (ngắn)
      - Không có gì làm  -> nghỉ POLL_INTERVAL_RONG_GIAY (dài hơn)
    """
    ngan = settings.poll_interval_giay
    dai = settings.poll_interval_rong_giay

    print(f"Môi trường     : {settings.env}")
    print(f"Nghỉ khi có việc : {ngan}s")
    print(f"Nghỉ khi rảnh    : {dai}s")
    print("Ctrl+C để dừng.\n")

    so_vong = 0
    so_vong_rong_lien_tiep = 0

    while True:
        so_vong += 1
        gio = datetime.now().strftime("%H:%M:%S")

        try:
            tim_thay, xu_ly = chay_mot_vong()
        except KeyboardInterrupt:
            raise
        except Exception as e:
            # Một vòng lỗi KHÔNG được làm chết job. Log rồi nghỉ dài, thử lại.
            logger.exception("Vòng #%d lỗi", so_vong)
            print(f"[{gio}] vòng #{so_vong}: LỖI ({e}) — thử lại sau {dai}s")
            _ngu(dai)
            continue

        if tim_thay:
            so_vong_rong_lien_tiep = 0
            print(f"[{gio}] vòng #{so_vong}: tìm thấy {tim_thay}, xử lý xong {xu_ly}"
                  f" — nghỉ {ngan}s")
            nghi = ngan
        else:
            so_vong_rong_lien_tiep += 1
            # Chỉ in dòng "không có gì" ở vài vòng đầu và thưa dần về sau,
            # để chạy qua đêm không đầy màn hình mà vẫn biết job còn sống.
            if so_vong_rong_lien_tiep <= 3 or so_vong_rong_lien_tiep % 10 == 0:
                print(f"[{gio}] vòng #{so_vong}: không có gì"
                      f" (đã rảnh {so_vong_rong_lien_tiep} vòng liên tiếp)"
                      f" — nghỉ {dai}s")
            nghi = dai

        _ngu(nghi)


def _ngu(giay: int) -> None:
    """Ngủ nhưng vẫn thoát ngay khi bấm Ctrl+C (không phải chờ hết giờ)."""
    try:
        time.sleep(giay)
    except KeyboardInterrupt:
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Vòng lặp ELIS: lấy chứng chỉ chờ duyệt -> AI scan -> nộp kết quả"
    )
    parser.add_argument("--once", action="store_true", help="Chạy đúng 1 vòng rồi thoát")
    parser.add_argument("--thong-ke", action="store_true", help="Chỉ xem thống kê, không gọi API")
    parser.add_argument("--verbose", action="store_true", help="In log chi tiết")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s: %(message)s",
    )

    if args.thong_ke:
        tk = database.thong_ke()
        print("Đã xử lý:", tk["theo_ket_qua"])
        print("Chưa nộp được ELIS:", tk["chua_nop_duoc"])
        return 0

    if args.once:
        tim_thay, xu_ly = chay_mot_vong()
        print(f"Tìm thấy {tim_thay} chứng chỉ chờ duyệt, xử lý xong {xu_ly}.")
        return 0

    try:
        return vong_lap_lien_tuc()
    except KeyboardInterrupt:
        print("\nĐã dừng.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
