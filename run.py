"""Điều phối toàn bộ luồng ELIS (run).

Nối 3 API + pipeline theo tài liệu:
  ① getCert          -> lấy danh sách chờ duyệt
  ② download ZIP     -> tải ảnh chứng chỉ
     -> chạy pipeline (Gemma/Azure) đối chiếu ảnh với thông tin từ ①
  ③ ProcessStatus    -> gửi APPROVED/REJECTED

Chạy một lần:
    python run.py once      # xử lý hết batch hiện có rồi dừng
Chạy vòng lặp:
    python run.py loop      # lặp mãi: xử lý -> nghỉ poll_interval -> lặp lại

Cần .env đầy đủ: FPT_API_KEY, AZURE_*, ELIS_*.
"""

import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import client
import file_utils
from database import database
import llm_text
import llm_vision
import ocr_azure
import pipeline
from config import settings
from schemas import KetQua, ThongTinNhap

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("run")


def goi_co_retry(ham, *args, **kwargs):
    """Gọi một hàm API, tự thử lại khi lỗi tạm thời (vd 502, timeout).

    Thử tối đa so_lan_retry lần, nghỉ retry_delay_giay giây giữa các lần.
    Lỗi ở lần cuối thì ném ra ngoài.
    """
    lan_cuoi = None
    for lan in range(1, settings.so_lan_retry + 1):
        try:
            return ham(*args, **kwargs)
        except client.ElisError as e:
            lan_cuoi = e
            logger.warning("Lần %d/%d lỗi: %s", lan, settings.so_lan_retry, e)
            if lan < settings.so_lan_retry:
                time.sleep(settings.retry_delay_giay)
    raise lan_cuoi


def xu_ly_mot_batch(azure_client) -> int:
    """Xử lý một batch chứng chỉ chờ duyệt. Trả về số chứng chỉ đã xử lý."""
    # ===== ① Lấy danh sách chờ duyệt =====
    danh_sach = goi_co_retry(client.lay_danh_sach_cho_duyet, page=1, size=100)
    if not danh_sach:
        logger.info("Không có chứng chỉ chờ duyệt.")
        return 0

    logger.info("Có %d chứng chỉ chờ duyệt.", len(danh_sach))

    # Cache map id -> thông tin, để dùng ở bước ③ (theo tài liệu mục 6).
    map_thong_tin = {item["id"]: item for item in danh_sach}

    # ===== ② Tải ZIP + scan + ③ nộp — TỪNG LÔ MỘT =====
    #
    # Vì sao nộp NGAY sau mỗi lô thay vì gom hết rồi nộp một lần ở cuối:
    # kết quả chưa nộp thì trên eLIS bản ghi vẫn ở trạng thái WAITING, nên
    # vòng poll sau getCert vẫn trả về đúng những item đó và job sẽ tải lại,
    # scan lại — tốn thêm một lượt gọi LLM cho mỗi cái. Gom cả mẻ rồi mới
    # nộp khiến toàn bộ công đã làm phụ thuộc vào một request duy nhất ở
    # cuối; chỉ cần nó hỏng (rớt mạng, container restart, eLIS lỗi) là mất
    # sạch. Nộp theo lô thì hỏng ở lô sau không xóa công của lô trước.
    #
    # Lô 20 (giới hạn của API ② khi tải) vẫn nằm xa dưới giới hạn 500 bản
    # ghi mỗi request của API ③, nên không cần chia nhỏ thêm.
    so_da_xu_ly = 0
    for i in range(0, len(danh_sach), 20):
        lo = danh_sach[i:i + 20]
        thu_tu_lo = i // 20 + 1
        cac_cap = [
            {"UserCourseId": it["id"], "certificate_id": it["certificate_id"]}
            for it in lo
        ]
        try:
            files = goi_co_retry(client.tai_zip_chung_chi, cac_cap)
        except client.ElisError as e:
            # Cả lô không tải được. PHẢI ghi log từng cái, nếu không chúng
            # biến mất khỏi mọi báo cáo — người đọc thấy "hôm nay xử lý 30"
            # mà không biết thật ra có 50 cái chờ, 20 cái thất bại lặng lẽ.
            # Không có ảnh nên KHÔNG tốn lượt gọi LLM nào.
            logger.error("Tải ZIP lô %d lỗi: %s", thu_tu_lo, e)
            _ghi_log_ca_lo_that_bai(lo, f"Không tải được file từ eLIS: {e}")
            continue

        # Item nào eLIS trả về được (manifest success=true).
        co_file = {f["userCourseId"] for f in files}
        # Item gửi lên nhưng không thấy trong ZIP -> soft-fail file_103/104.
        thieu = [it for it in lo if it["id"] not in co_file]
        if thieu:
            logger.warning("%d chứng chỉ không có file trong ZIP.", len(thieu))
            _ghi_log_ca_lo_that_bai(
                thieu, "eLIS không có file trên đĩa (soft-fail trong manifest)",
                tang="soft_fail_zip")

        # ===== Chạy pipeline cho từng file trong lô =====
        ket_qua_lo = []
        for f in files:
            uc_id = f["userCourseId"]
            thong_tin = map_thong_tin.get(uc_id)
            if not thong_tin:
                logger.warning("Không tìm thấy thông tin cho %s, bỏ qua.", uc_id)
                continue

            kq = _scan_mot_chung_chi(f["anh_bytes"], thong_tin, azure_client)
            # Ghi log mỗi chứng chỉ đã xử lý (để xem lại / kiểm toán).
            # employee_id và user_course_id truyền riêng: KetQuaXuLy chỉ mang
            # ma_nhan_vien (username từ email), không có hai trường này.
            try:
                database.ghi_log(kq,
                                 employee_id=thong_tin.get("employeeId"),
                                 user_course_id=uc_id)
            except Exception as e:
                logger.warning("Ghi log lỗi (không chặn xử lý): %s", e)
            ket_qua_lo.append(_tao_dto_ket_qua(kq, thong_tin))

        # ===== ③ Nộp kết quả của RIÊNG lô này =====
        if ket_qua_lo:
            _nop_ket_qua(ket_qua_lo, thu_tu_lo)
            so_da_xu_ly += len(ket_qua_lo)

    return so_da_xu_ly


def _nop_ket_qua(ket_qua_lo: list[dict], thu_tu_lo: int) -> None:
    """Gọi API ③ cho một lô kết quả và ghi lại trạng thái từng item.

    Lỗi ở đây KHÔNG ném ra ngoài: lô này hỏng thì các lô sau vẫn phải được
    xử lý tiếp. Item hỏng đã được đánh dấu elis_gui_ok=0 nên báo cáo không
    tính nhầm là đã duyệt xong, và vòng poll sau sẽ gặp lại chúng.
    """
    try:
        data = goi_co_retry(client.cap_nhat_trang_thai, ket_qua_lo)
    except client.ElisError as e:
        logger.error("Nộp kết quả lô %d lỗi (%d item): %s",
                     thu_tu_lo, len(ket_qua_lo), e)
        for dto in ket_qua_lo:
            _cap_nhat_gui(dto["id"], False, f"Không gửi được: {e}")
        return

    thanh_cong = data.get("successList", []) or []
    that_bai = data.get("failList", []) or []
    logger.info("Nộp lô %d: %d thành công, %d thất bại.",
                thu_tu_lo, len(thanh_cong), len(that_bai))

    for it in thanh_cong:
        _cap_nhat_gui(it.get("id"), True)
    for it in that_bai:
        # failList bọc dạng {"data": {...}, "message": "..."} — mục 5.4.
        du_lieu = it.get("data") or it
        message = it.get("message", "")
        logger.warning("ELIS từ chối id=%s: %s", du_lieu.get("id"), message)
        _cap_nhat_gui(du_lieu.get("id"), False, message)


def _ghi_log_ca_lo_that_bai(lo, ly_do, tang="loi_tai_file"):
    """Ghi log REJECTED cho từng item trong lô không xử lý được."""
    for it in lo:
        try:
            database.ghi_log_that_bai(
                user_course_id=it["id"],
                employee_id=it.get("employeeId"),
                ket_qua=KetQua.REJECTED.value,
                ly_do=ly_do,
                tang_xu_ly=tang,
            )
        except Exception as e:
            logger.warning("Ghi log thất bại lỗi: %s", e)


def _cap_nhat_gui(user_course_id, thanh_cong, message=None):
    """Ghi lại ELIS có nhận kết quả không. Lỗi ghi log không chặn luồng."""
    if not user_course_id:
        return
    try:
        database.cap_nhat_ket_qua_gui(user_course_id, thanh_cong, message)
    except Exception as e:
        logger.warning("Cập nhật trạng thái gửi lỗi: %s", e)


def _ma_tu_email(email: str | None) -> str:
    """Lấy mã nhân viên (username) từ email — phần đứng trước dấu @.

    KHÔNG giới hạn tên miền. Lý do: FPT có nhiều đuôi khác nhau (fpt.com,
    fpt.com.vn, fsoft.com.vn, fe.edu.vn, fptsoftware.com...). Thứ được in
    trên chứng chỉ là USERNAME, không phụ thuộc tên miền — nên chặn theo
    đuôi chỉ làm mất mã đối chiếu và từ chối oan đúng những chứng chỉ in
    username, tức là đúng ca mà việc lấy mã từ email sinh ra để xử lý.

    Trả rỗng nếu không có email hoặc chuỗi không chứa dấu @. Rỗng nghĩa là
    không đối chiếu được qua mã, chỉ còn đối chiếu bằng tên.

    Ví dụ:
        "hungnt97@fpt.com"      -> "hungnt97"
        "hoabd3@fpt.com.vn"     -> "hoabd3"
        "  HoaBD3@FSOFT.COM.VN" -> "hoabd3"
    """
    if not email or not email.strip():
        return ""
    email = email.strip().lower()
    if "@" not in email:
        # Dữ liệu bất thường: trường employeeEmail mà không có dấu @.
        # Cảnh báo để không hỏng âm thầm — nếu im lặng, chứng chỉ in
        # username sẽ bị REJECTED với lý do "tên không khớp", trông y hệt
        # trường hợp nhân viên khai sai, rất khó lần ra nguyên nhân.
        logger.warning("employeeEmail không hợp lệ (thiếu @): %r", email)
        return ""
    return email.split("@", 1)[0].strip()


def _scan_mot_chung_chi(anh_bytes, thong_tin, azure_client):
    """Chạy pipeline cho một chứng chỉ. Trả về KetQuaXuLy."""
    # Ảnh từ ELIS có thể là PDF; ghi tạm rồi dùng file_utils để chuẩn hóa.
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as tmp:
        tmp.write(anh_bytes)
        tmp_path = tmp.name

    try:
        anh_list = file_utils.doc_thanh_anh(tmp_path)
    except file_utils.FileKhongHopLe as e:
        # Không đọc được file -> coi như REJECTED, lý do rõ.
        os.unlink(tmp_path)
        from schemas import KetQuaXuLy
        return KetQuaXuLy(
            ma_nhan_vien=thong_tin.get("employeeId"),
            ket_qua=KetQua.REJECTED,
            ly_do=f"File không hợp lệ: {e}",
            tang_xu_ly="file_loi",
        )
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    # Thông tin đối chiếu lấy từ API ① (không phải người nhập tay).
    # Mã để ĐỐI CHIẾU với ảnh lấy từ EMAIL (phần trước @fpt.com), không phải
    # employeeId. Lý do: một số chứng chỉ in username (= phần email) làm tên.
    # Lưu ý: employeeId gốc vẫn được dùng khi GỬI kết quả về ELIS (xem
    # _tao_dto_ket_qua), không đụng ở đây.
    ma_doi_chieu = _ma_tu_email(thong_tin.get("employeeEmail"))
    nhap = ThongTinNhap(
        ten_nhan_vien=thong_tin.get("employeeName") or "",
        ten_khoa_hoc=thong_tin.get("courseName") or "",
        ma_nhan_vien=ma_doi_chieu,
    )

    return pipeline.xu_ly(
        anh_list=anh_list,
        nhap=nhap,
        trich_tu_anh=llm_vision.trich_tu_anh,
        ocr_nhieu_anh=ocr_azure.ocr_nhieu_anh,
        trich_tu_text=llm_text.trich_tu_text,
        azure_client=azure_client,
    )


def _tao_dto_ket_qua(kq, thong_tin) -> dict:
    """Tạo DTO cho API ③ từ kết quả pipeline (theo tài liệu mục 5.2)."""
    return {
        "id": thong_tin["id"],
        "certificate_id": thong_tin["certificate_id"],
        "status": kq.ket_qua.value,  # APPROVED / REJECTED
        "courseId": thong_tin["courseId"],
        "employeeId": thong_tin["employeeId"],
        "comment": f"AI scan: {kq.ly_do}",  # comment bắt buộc, hiển thị cho học viên
        "comment_cer": f"[{kq.tang_xu_ly}]",
    }


def main():
    che_do = sys.argv[1] if len(sys.argv) > 1 else "once"
    database.khoi_tao()  # tạo bảng log nếu chưa có
    azure_client = ocr_azure.tao_client()

    if che_do == "once":
        n = xu_ly_mot_batch(azure_client)
        logger.info("Xong. Đã xử lý %d chứng chỉ.", n)
    elif che_do == "loop":
        logger.info("Chạy vòng lặp (Ctrl+C để dừng).")
        while True:
            try:
                xu_ly_mot_batch(azure_client)
            except Exception as e:
                logger.error("Lỗi trong batch: %s", e)
            logger.info("Nghỉ %d giây...", settings.poll_interval_giay)
            time.sleep(settings.poll_interval_giay)
    else:
        print("Dùng: python run.py [once|loop]")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())