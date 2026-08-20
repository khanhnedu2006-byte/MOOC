"""Điều phối toàn bộ luồng ELIS (run).

Nối 3 API + pipeline theo tài liệu:
  ① getCert          -> lấy danh sách chờ duyệt
  ② download         -> tải file chứng chỉ (JSON + base64)
     -> chạy pipeline (Gemma/Azure) đối chiếu ảnh với thông tin từ ①
  ③ ProcessStatus    -> gửi APPROVED/REJECTED

Chạy liên tục (mặc định):
    python run.py           # còn chứng chỉ thì làm ngay, hết thì nghỉ
                            # poll_interval rồi hỏi lại. Ctrl+C để dừng.
Chạy một lần rồi thoát:
    python run.py once      # xử lý hết batch hiện có rồi dừng

Cần .env đầy đủ: FPT_API_KEY, AZURE_*, ELIS_*.
"""

import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import logging
import sys
import time
from pathlib import Path
from typing import NamedTuple

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


class KetQuaVong(NamedTuple):
    """Kết quả một vòng xử lý.

    Tách làm hai con số vì chúng trả lời hai câu hỏi khác nhau:
      - so_xu_ly: đã tốn bao nhiêu lượt quét (tiền gọi LLM).
      - so_nop_ok: eLIS THẬT SỰ nhận bao nhiêu cái.
    Hai số này lệch nhau khi API ③ hỏng: quét xong nhưng không nộp được, bản
    ghi vẫn ở WAITING nên vòng sau sẽ gặp lại đúng những cái đó. Vòng lặp
    dựa vào so_nop_ok để biết có tiến triển thật hay không — xem main().
    """
    so_xu_ly: int
    so_nop_ok: int


def xu_ly_mot_batch(azure_client) -> KetQuaVong:
    """Xử lý một batch chứng chỉ chờ duyệt."""
    # ===== ① Lấy danh sách chờ duyệt =====
    danh_sach = goi_co_retry(client.lay_danh_sach_cho_duyet, page=1, size=100)
    if not danh_sach:
        # debug chứ không info: ở chế độ chạy liên tục dòng này sẽ lặp mỗi
        # poll_interval giây và nhấn chìm log thật. main() đã báo trạng thái
        # "hết việc" đúng MỘT lần khi job chuyển sang rảnh.
        logger.debug("Không có chứng chỉ chờ duyệt.")
        return KetQuaVong(0, 0)

    logger.info("Có %d chứng chỉ chờ duyệt.", len(danh_sach))

    # Cache map id -> thông tin, để dùng ở bước ③ (theo tài liệu mục 6).
    map_thong_tin = {item["id"]: item for item in danh_sach}

    # ===== ② Tải file + scan + ③ nộp — TỪNG LÔ MỘT =====
    #
    # Vì sao nộp NGAY sau mỗi lô thay vì gom hết rồi nộp một lần ở cuối:
    # kết quả chưa nộp thì trên eLIS bản ghi vẫn ở trạng thái WAITING, nên
    # vòng poll sau getCert vẫn trả về đúng những item đó và job sẽ tải lại,
    # scan lại — tốn thêm một lượt gọi LLM cho mỗi cái. Gom cả mẻ rồi mới
    # nộp khiến toàn bộ công đã làm phụ thuộc vào một request duy nhất ở
    # cuối; chỉ cần nó hỏng (rớt mạng, container restart, eLIS lỗi) là mất
    # sạch. Nộp theo lô thì hỏng ở lô sau không xóa công của lô trước.
    #
    # Kích thước lô lấy từ cấu hình (KICH_THUOC_LO). Ép về khoảng [1, 20]:
    # eLIS giới hạn 20 cặp mỗi request tải file, còn API ③ cho tới 500 bản
    # ghi mỗi request nên 20 vẫn nằm xa dưới ngưỡng đó.
    so_da_xu_ly = 0
    so_nop_ok = 0
    kich_thuoc = max(1, min(settings.kich_thuoc_lo, 20))
    for i in range(0, len(danh_sach), kich_thuoc):
        lo = danh_sach[i:i + kich_thuoc]
        thu_tu_lo = i // kich_thuoc + 1
        cac_cap = [
            {"UserCourseId": it["id"], "certificate_id": it["certificate_id"]}
            for it in lo
        ]
        try:
            files = goi_co_retry(client.tai_chung_chi, cac_cap)
        except client.ElisError as e:
            # Cả lô không tải được. PHẢI ghi log từng cái, nếu không chúng
            # biến mất khỏi mọi báo cáo — người đọc thấy "hôm nay xử lý 30"
            # mà không biết thật ra có 50 cái chờ, 20 cái thất bại lặng lẽ.
            # Không có ảnh nên KHÔNG tốn lượt gọi LLM nào.
            logger.error("Tải file lô %d lỗi: %s", thu_tu_lo, e)
            _ghi_log_ca_lo_that_bai(lo, f"Không tải được file từ eLIS: {e}")
            continue

        # Item nào eLIS trả về được file.
        co_file = {f["userCourseId"] for f in files}
        # Item gửi lên nhưng eLIS không trả file -> soft-fail file_103/104.
        thieu = [it for it in lo if it["id"] not in co_file]
        if thieu:
            logger.warning("%d chứng chỉ không nhận được file.", len(thieu))
            _ghi_log_ca_lo_that_bai(
                thieu, "eLIS không trả về file cho chứng chỉ này",
                tang="khong_co_file")

        # ===== Chạy pipeline cho từng file trong lô =====
        ket_qua_lo = []
        for f in files:
            uc_id = f["userCourseId"]
            thong_tin = map_thong_tin.get(uc_id)
            if not thong_tin:
                logger.warning("Không tìm thấy thông tin cho %s, bỏ qua.", uc_id)
                continue

            kq = _scan_mot_chung_chi(f["anh_bytes"], thong_tin, azure_client)

            # In kết luận NGAY khi có, trước cả khi ghi DB hay nộp eLIS.
            # Không có dòng này thì màn hình chỉ hiện "Có 5 chứng chỉ chờ
            # duyệt" rồi "Nộp lô 1: 5 thành công" — người vận hành không
            # biết cái nào đậu, cái nào trượt, trượt vì lý do gì, mà phải
            # mở mooc_log.db lên xem. Đặt trước ghi DB để nếu DB có hỏng
            # thì kết luận vẫn còn trên màn hình.
            logger.info("[%s] %s (%s) | %s | tầng: %s",
                        kq.ket_qua.value,
                        thong_tin.get("employeeName") or "?",
                        thong_tin.get("employeeId") or uc_id,
                        kq.ly_do or "-",
                        kq.tang_xu_ly)
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
            so_nop_ok += _nop_ket_qua(ket_qua_lo, thu_tu_lo)
            so_da_xu_ly += len(ket_qua_lo)

    return KetQuaVong(so_da_xu_ly, so_nop_ok)


def _nop_ket_qua(ket_qua_lo: list[dict], thu_tu_lo: int) -> int:
    """Gọi API ③ cho một lô kết quả và ghi lại trạng thái từng item.

    Lỗi ở đây KHÔNG ném ra ngoài: lô này hỏng thì các lô sau vẫn phải được
    xử lý tiếp. Item hỏng đã được đánh dấu elis_gui_ok=0 nên báo cáo không
    tính nhầm là đã duyệt xong, và vòng poll sau sẽ gặp lại chúng.

    Trả về số item eLIS thật sự nhận (successList).
    """
    try:
        data = goi_co_retry(client.cap_nhat_trang_thai, ket_qua_lo)
    except client.ElisError as e:
        logger.error("Nộp kết quả lô %d lỗi (%d item): %s",
                     thu_tu_lo, len(ket_qua_lo), e)
        for dto in ket_qua_lo:
            _cap_nhat_gui(dto["id"], False, f"Không gửi được: {e}")
        return 0

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

    return len(thanh_cong)


def _ghi_log_ca_lo_that_bai(lo, ly_do, tang="loi_tai_file"):
    """Ghi log REJECTED cho từng item trong lô không xử lý được."""
    for it in lo:
        # Ca hỏng kỹ thuật cũng phải hiện kết luận trên màn hình như ca chạy
        # được, nếu không chúng chỉ nằm im trong DB và người vận hành tưởng
        # là chưa xử lý tới.
        logger.info("[%s] %s (%s) | %s | tầng: %s",
                    KetQua.REJECTED.value,
                    it.get("employeeName") or "?",
                    it.get("employeeId") or it.get("id"),
                    ly_do, tang)
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


# Lý do kỹ thuật -> câu hiển thị cho học viên. Học viên không cần biết
# LLM1/LLM2/Azure là gì; những tên đó chỉ có nghĩa với người bảo trì hệ
# thống và đã được ghi vào bảng log để tra khi cần.
_CAU_CHO_TANG = {
    "llm1_loi":      "Chưa xử lý được chứng chỉ, vui lòng thử lại sau",
    "tang2_loi":     "Chưa xử lý được chứng chỉ, vui lòng thử lại sau",
    "loi_he_thong":  "Chưa xử lý được chứng chỉ, vui lòng thử lại sau",
    "file_loi":      "Không đọc được file chứng chỉ, vui lòng tải lại",
    "loi_tai_file":  "Không đọc được file chứng chỉ, vui lòng tải lại",
    "khong_co_file": "Không đọc được file chứng chỉ, vui lòng tải lại",
}


def _comment_hoc_vien(kq) -> str:
    """Câu hiển thị cho học viên trên giao diện eLIS.

    APPROVED -> "Hợp lệ".
    REJECTED vì nghiệp vụ -> nêu đúng trường nào sai (tên / khóa học / ngày).
    REJECTED vì trục trặc kỹ thuật -> câu trung tính, KHÔNG đổ lỗi cho học
        viên và không lộ chi tiết nội bộ. Lý do kỹ thuật đầy đủ vẫn nằm
        trong bảng log để người vận hành tra.
    """
    if kq.ket_qua == KetQua.APPROVED:
        return "Hợp lệ"

    cau = _CAU_CHO_TANG.get(kq.tang_xu_ly)
    if cau:
        return cau

    # Lý do nghiệp vụ do pipeline sinh ra đã sạch, không chứa tên tầng:
    # "Tên không khớp; Tên khóa học không khớp; Ngày không hợp lệ".
    return kq.ly_do or "Chứng chỉ không hợp lệ"


def _comment_cer(kq) -> str:
    """Thông tin AI đọc được từ ảnh, để người duyệt đối chiếu bằng mắt.

    Theo mẫu mentor gửi, trường này chứa "AI [Extracted Information]" —
    tức thông tin trích xuất, không phải kết luận. Không ghi tên tầng xử lý
    ở đây vì nó vô nghĩa với người đọc.
    """
    t = getattr(kq, "trich_xuat", None)
    if not t:
        return "AI không đọc được nội dung chứng chỉ"

    phan = []
    if t.ten_nguoi_nhan:
        phan.append(f"Tên: {t.ten_nguoi_nhan}")
    if t.ten_chung_chi:
        ten = t.ten_chung_chi
        if getattr(t, "ten_chung_chi_phu", None):
            ten = f"{ten} / {t.ten_chung_chi_phu}"
        phan.append(f"Khóa học: {ten}")
    if t.ngay_nhan:
        phan.append(f"Ngày: {t.ngay_nhan}")

    return "AI đọc được — " + " | ".join(phan) if phan else \
        "AI không đọc được nội dung chứng chỉ"


def _tao_dto_ket_qua(kq, thong_tin) -> dict:
    """Tạo DTO cho API ③ từ kết quả pipeline (theo tài liệu mục 5.2)."""
    return {
        "id": thong_tin["id"],
        "certificate_id": thong_tin["certificate_id"],
        "status": kq.ket_qua.value,  # APPROVED / REJECTED
        "courseId": thong_tin["courseId"],
        "employeeId": thong_tin["employeeId"],
        "comment": _comment_hoc_vien(kq),   # bắt buộc, hiển thị cho học viên
        "comment_cer": _comment_cer(kq)[:1000],
    }


def chay_lien_tuc(azure_client) -> None:
    """Lặp mãi tới khi Ctrl+C.

    Luật nghỉ: CÒN VIỆC THÌ LÀM TIẾP NGAY, hết việc mới nghỉ poll_interval.
    Nhưng "còn việc" ở đây đo bằng so_nop_ok (eLIS đã nhận), KHÔNG phải
    so_xu_ly (đã quét xong). Lý do: khi API ③ hỏng, bản ghi vẫn nằm ở
    WAITING nên vòng sau getCert trả về đúng những item đó. Nếu lấy so_xu_ly
    làm mốc thì job sẽ quay vòng KHÔNG NGHỈ, tải lại và gọi LLM lại cùng một
    tập chứng chỉ cho tới khi eLIS sống lại — vừa tốn tiền vừa không ai để ý
    vì nhìn log vẫn thấy "đang chạy". Đo bằng so_nop_ok thì lúc đó job tự
    hạ nhịp xuống mỗi poll_interval một lần, và quay lại chạy hết tốc độ
    ngay khi eLIS nhận được cái đầu tiên.
    """
    nghi = max(1, settings.poll_interval_giay)
    logger.info("Chạy liên tục. Hết việc thì hỏi lại mỗi %d giây. "
                "Ctrl+C để dừng.", nghi)

    dang_ranh = False   # để dòng log "đang rảnh" chỉ in MỘT lần mỗi đợt rảnh
    while True:
        try:
            kq = xu_ly_mot_batch(azure_client)
        except Exception as e:
            # Không để một lỗi bất kỳ giết job. logger.exception giữ lại
            # traceback — thiếu nó thì lỗi lạ chỉ còn một dòng vô nghĩa.
            logger.exception("Lỗi trong vòng xử lý: %s", e)
            kq = KetQuaVong(0, 0)

        if kq.so_nop_ok > 0:
            # Có tiến triển thật -> làm tiếp ngay, không nghỉ.
            dang_ranh = False
            continue

        if not dang_ranh:
            if kq.so_xu_ly > 0:
                logger.warning(
                    "Đã quét %d chứng chỉ nhưng eLIS không nhận cái nào. "
                    "Tạm nghỉ %d giây rồi thử lại.", kq.so_xu_ly, nghi)
            else:
                logger.info("Không còn chứng chỉ chờ duyệt. "
                            "Kiểm tra lại mỗi %d giây...", nghi)
            dang_ranh = True

        time.sleep(nghi)


def main():
    che_do = sys.argv[1] if len(sys.argv) > 1 else "loop"
    if che_do not in ("once", "loop"):
        print("Dùng: python run.py [loop|once]     (không ghi gì = loop)")
        return 1

    database.khoi_tao()  # tạo bảng log nếu chưa có
    azure_client = ocr_azure.tao_client()

    if che_do == "once":
        kq = xu_ly_mot_batch(azure_client)
        logger.info("Xong. Đã xử lý %d chứng chỉ, eLIS nhận %d.",
                    kq.so_xu_ly, kq.so_nop_ok)
        return 0

    try:
        chay_lien_tuc(azure_client)
    except KeyboardInterrupt:
        # Ctrl+C là cách dừng BÌNH THƯỜNG, không phải sự cố -> không đổ
        # traceback ra màn hình.
        logger.info("Đã dừng theo yêu cầu (Ctrl+C).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())