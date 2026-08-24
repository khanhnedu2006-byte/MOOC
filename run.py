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

_PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

import client
import file_utils
import archive
from database import database
import llm_text
import llm_vision
import ocr_azure
import pipeline
import scheduler
from config import settings
from schemas import Verdict, InputInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("run")


def call_with_retry(func, *args, **kwargs):
    """Gọi một hàm API, tự thử lại khi lỗi tạm thời (vd 502, timeout).

    Thử tối đa retry_count lần, nghỉ retry_delay_seconds giây giữa các lần.
    Lỗi ở lần cuối thì ném ra ngoài.
    """
    last_error = None
    for attempt in range(1, settings.retry_count + 1):
        try:
            return func(*args, **kwargs)
        except client.ElisError as e:
            last_error = e
            logger.warning("Lần %d/%d lỗi: %s", attempt, settings.retry_count, e)
            if attempt < settings.retry_count:
                time.sleep(settings.retry_delay_seconds)
    raise last_error


class RoundResult(NamedTuple):
    """Kết quả một vòng xử lý.

    Tách làm hai con số vì chúng trả lời hai câu hỏi khác nhau:
      - scanned_count: đã tốn bao nhiêu lượt quét (tiền gọi LLM).
      - accepted_count: eLIS THẬT SỰ nhận bao nhiêu cái.
    Hai số này lệch nhau khi API ③ hỏng: quét xong nhưng không nộp được, bản
    ghi vẫn ở WAITING nên vòng sau sẽ gặp lại đúng những cái đó. Vòng lặp
    dựa vào accepted_count để biết có tiến triển thật hay không — xem main().
    """
    scanned_count: int
    accepted_count: int


def process_one_batch(azure_client) -> RoundResult:
    """Xử lý một batch chứng chỉ chờ duyệt."""
    # ===== ① Lấy danh sách chờ duyệt =====
    items = call_with_retry(client.get_pending_list, page=1, size=100)
    if not items:
        # debug chứ không info: ở chế độ chạy liên tục dòng này sẽ lặp mỗi
        # poll_interval giây và nhấn chìm log thật. main() đã báo trạng thái
        # "hết việc" đúng MỘT lần khi job chuyển sang rảnh.
        logger.debug("Không có chứng chỉ chờ duyệt.")
        return RoundResult(0, 0)

    logger.info("Có %d chứng chỉ chờ duyệt.", len(items))

    # Cache map id -> thông tin, để dùng ở bước ③ (theo tài liệu mục 6).
    info_by_id = {item["id"]: item for item in items}

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
    # Kích thước lô lấy từ cấu hình (BATCH_SIZE). Ép về khoảng [1, 20]:
    # eLIS giới hạn 20 cặp mỗi request tải file, còn API ③ cho tới 500 bản
    # ghi mỗi request nên 20 vẫn nằm xa dưới ngưỡng đó.
    processed_count = 0
    accepted_count = 0
    batch_size = max(1, min(settings.batch_size, 20))
    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        batch_index = i // batch_size + 1
        pairs = [
            {"UserCourseId": it["id"], "certificate_id": it["certificate_id"]}
            for it in batch
        ]
        try:
            files = call_with_retry(client.download_certificates, pairs)
        except client.ElisError as e:
            # Cả lô không tải được. PHẢI ghi log từng cái, nếu không chúng
            # biến mất khỏi mọi báo cáo — người đọc thấy "hôm nay xử lý 30"
            # mà không biết thật ra có 50 cái chờ, 20 cái thất bại lặng lẽ.
            # Không có ảnh nên KHÔNG tốn lượt gọi LLM nào.
            logger.error("Tải file lô %d lỗi: %s", batch_index, e)
            _log_failed_batch(batch, f"Không tải được file từ eLIS: {e}")
            continue

        # Item nào eLIS trả về được file.
        with_file = {f["userCourseId"] for f in files}
        # Item gửi lên nhưng eLIS không trả file -> soft-fail file_103/104.
        missing = [it for it in batch if it["id"] not in with_file]
        if missing:
            logger.warning("%d chứng chỉ không nhận được file.", len(missing))
            _log_failed_batch(
                missing, "eLIS không trả về file cho chứng chỉ này",
                stage="no_file")

        # ===== Chạy pipeline cho từng file trong lô =====
        batch_results = []
        for f in files:
            uc_id = f["userCourseId"]
            info = info_by_id.get(uc_id)
            if not info:
                logger.warning("Không tìm thấy thông tin cho %s, bỏ qua.", uc_id)
                continue

            # Lưu vào kho TRƯỚC khi scan (nếu bật SAVE_CERTIFICATES). Đặt trước
            # vì nếu pipeline chết giữa chừng thì ảnh vẫn còn — mà ca làm
            # pipeline chết mới là ca đáng nghiên cứu nhất. Hàm này không bao
            # giờ ném lỗi ra ngoài.
            archive_path = None
            if settings.save_certificates:
                archive_path = archive.save(
                    f["anh_bytes"], info, _PROJECT_ROOT / settings.archive_dir)

            kq = _scan_certificate(f["anh_bytes"], info, azure_client)
            archive.write_verdict(archive_path, kq)

            # In kết luận NGAY khi có, trước cả khi ghi DB hay nộp eLIS.
            # Không có dòng này thì màn hình chỉ hiện "Có 5 chứng chỉ chờ
            # duyệt" rồi "Nộp lô 1: 5 thành công" — người vận hành không
            # biết cái nào đậu, cái nào trượt, trượt vì lý do gì, mà phải
            # mở mooc_log.db lên xem. Đặt trước ghi DB để nếu DB có hỏng
            # thì kết luận vẫn còn trên màn hình.
            logger.info("[%s] %s (%s) | %s | tầng: %s",
                        kq.verdict.value,
                        info.get("employeeName") or "?",
                        info.get("employeeId") or uc_id,
                        kq.reason or "-",
                        kq.stage)
            # Ghi log mỗi chứng chỉ đã xử lý (để xem lại / kiểm toán).
            # employee_id và user_course_id truyền riêng: ProcessResult chỉ mang
            # employee_code (username từ email), không có hai trường này.
            try:
                database.write_log(kq,
                                 employee_id=info.get("employeeId"),
                                 user_course_id=uc_id,
                                 provider=info.get("providerName"))
            except Exception as e:
                logger.warning("Ghi log lỗi (không chặn xử lý): %s", e)
            batch_results.append(_build_result_dto(kq, info))

        # ===== ③ Nộp kết quả của RIÊNG lô này =====
        if batch_results:
            accepted_count += _submit_results(batch_results, batch_index)
            processed_count += len(batch_results)

    return RoundResult(processed_count, accepted_count)


def _submit_results(batch_results: list[dict], batch_index: int) -> int:
    """Gọi API ③ cho một lô kết quả và ghi lại trạng thái từng item.

    Lỗi ở đây KHÔNG ném ra ngoài: lô này hỏng thì các lô sau vẫn phải được
    xử lý tiếp. Item hỏng đã được đánh dấu elis_sent_ok=0 nên báo cáo không
    tính nhầm là đã duyệt xong, và vòng poll sau sẽ gặp lại chúng.

    Trả về số item eLIS thật sự nhận (successList).
    """
    try:
        data = call_with_retry(client.update_status, batch_results)
    except client.ElisError as e:
        logger.error("Nộp kết quả lô %d lỗi (%d item): %s",
                     batch_index, len(batch_results), e)
        for dto in batch_results:
            _record_send_status(dto["id"], False, f"Không gửi được: {e}")
        return 0

    succeeded = data.get("successList", []) or []
    failed = data.get("failList", []) or []
    logger.info("Nộp lô %d: %d thành công, %d thất bại.",
                batch_index, len(succeeded), len(failed))

    for it in succeeded:
        _record_send_status(it.get("id"), True)
    for it in failed:
        # failList bọc dạng {"data": {...}, "message": "..."} — mục 5.4.
        data_bytes = it.get("data") or it
        message = it.get("message", "")
        logger.warning("ELIS từ chối id=%s: %s", data_bytes.get("id"), message)
        _record_send_status(data_bytes.get("id"), False, message)

    return len(succeeded)


def _log_failed_batch(batch, reason, stage="download_error"):
    """Ghi log REJECTED cho từng item trong lô không xử lý được."""
    for it in batch:
        # Ca hỏng kỹ thuật cũng phải hiện kết luận trên màn hình như ca chạy
        # được, nếu không chúng chỉ nằm im trong DB và người vận hành tưởng
        # là chưa xử lý tới.
        logger.info("[%s] %s (%s) | %s | tầng: %s",
                    Verdict.REJECTED.value,
                    it.get("employeeName") or "?",
                    it.get("employeeId") or it.get("id"),
                    reason, stage)
        try:
            database.write_failure_log(
                user_course_id=it["id"],
                employee_id=it.get("employeeId"),
                verdict=Verdict.REJECTED.value,
                reason=reason,
                stage=stage,
                provider=it.get("providerName"),
            )
        except Exception as e:
            logger.warning("Ghi log thất bại lỗi: %s", e)


def _record_send_status(user_course_id, succeeded, message=None):
    """Ghi lại ELIS có nhận kết quả không. Lỗi ghi log không chặn luồng."""
    if not user_course_id:
        return
    try:
        database.update_send_result(user_course_id, succeeded, message)
    except Exception as e:
        logger.warning("Cập nhật trạng thái gửi lỗi: %s", e)


def _code_from_email(email: str | None) -> str:
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


def _scan_certificate(image_bytes, info, azure_client):
    """Chạy pipeline cho một chứng chỉ. Trả về ProcessResult."""
    # Ảnh từ ELIS có thể là PDF; ghi tạm rồi dùng file_utils để chuẩn hóa.
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as tmp:
        tmp.write(image_bytes)
        tmp_path = tmp.name

    try:
        images = file_utils.read_as_images(tmp_path)
    except file_utils.InvalidFileError as e:
        # Không đọc được file -> coi như REJECTED, lý do rõ.
        os.unlink(tmp_path)
        from schemas import ProcessResult
        return ProcessResult(
            employee_code=info.get("employeeId"),
            verdict=Verdict.REJECTED,
            reason=f"File không hợp lệ: {e}",
            stage="file_error",
        )
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    # Thông tin đối chiếu lấy từ API ① (không phải người nhập tay).
    # Mã để ĐỐI CHIẾU với ảnh lấy từ EMAIL (phần trước @fpt.com), không phải
    # employeeId. Lý do: một số chứng chỉ in username (= phần email) làm tên.
    # Lưu ý: employeeId gốc vẫn được dùng khi GỬI kết quả về ELIS (xem
    # _build_result_dto), không đụng ở đây.
    match_code = _code_from_email(info.get("employeeEmail"))
    given = InputInfo(
        employee_name=info.get("employeeName") or "",
        course_name=info.get("courseName") or "",
        employee_code=match_code,
    )

    return pipeline.process(
        images=images,
        given=given,
        extract_from_image=llm_vision.extract_from_image,
        ocr_images=ocr_azure.ocr_images,
        extract_from_text=llm_text.extract_from_text,
        azure_client=azure_client,
    )


# Lý do kỹ thuật -> câu hiển thị cho học viên. Học viên không cần biết
# LLM1/LLM2/Azure là gì; những tên đó chỉ có nghĩa với người bảo trì hệ
# thống và đã được ghi vào bảng log để tra khi cần.
_MESSAGE_BY_STAGE = {
    "llm1_error":      "Chưa xử lý được chứng chỉ, vui lòng thử lại sau",
    "stage2_error":     "Chưa xử lý được chứng chỉ, vui lòng thử lại sau",
    "system_error":  "Chưa xử lý được chứng chỉ, vui lòng thử lại sau",
    "file_error":      "Không đọc được file chứng chỉ, vui lòng tải lại",
    "download_error":  "Không đọc được file chứng chỉ, vui lòng tải lại",
    "no_file": "Không đọc được file chứng chỉ, vui lòng tải lại",
}


def _learner_comment(kq) -> str:
    """Câu hiển thị cho học viên trên giao diện eLIS.

    APPROVED -> "Hợp lệ".
    REJECTED vì nghiệp vụ -> nêu đúng trường nào sai (tên / khóa học / ngày).
    REJECTED vì trục trặc kỹ thuật -> câu trung tính, KHÔNG đổ lỗi cho học
        viên và không lộ chi tiết nội bộ. Lý do kỹ thuật đầy đủ vẫn nằm
        trong bảng log để người vận hành tra.
    """
    if kq.verdict == Verdict.APPROVED:
        return "Hợp lệ"

    cau = _MESSAGE_BY_STAGE.get(kq.stage)
    if cau:
        return cau

    # Lý do nghiệp vụ do pipeline sinh ra đã sạch, không chứa tên tầng:
    # "Tên không khớp; Tên khóa học không khớp; Ngày không hợp lệ".
    return kq.reason or "Chứng chỉ không hợp lệ"


def _certificate_comment(kq) -> str:
    """Thông tin AI đọc được từ ảnh, để người duyệt đối chiếu bằng mắt.

    Theo mẫu mentor gửi, trường này chứa "AI [Extracted Information]" —
    tức thông tin trích xuất, không phải kết luận. Không ghi tên tầng xử lý
    ở đây vì nó vô nghĩa với người đọc.
    """
    t = getattr(kq, "extracted", None)
    if not t:
        return "AI không đọc được nội dung chứng chỉ"

    phan = []
    if t.recipient_name:
        phan.append(f"Tên: {t.recipient_name}")
    if t.certificate_name:
        name = t.certificate_name
        if getattr(t, "certificate_name_alt", None):
            name = f"{name} / {t.certificate_name_alt}"
        phan.append(f"Khóa học: {name}")
    if t.issue_date:
        phan.append(f"Ngày: {t.issue_date}")

    return "AI đọc được — " + " | ".join(phan) if phan else \
        "AI không đọc được nội dung chứng chỉ"


def _build_result_dto(kq, info) -> dict:
    """Tạo DTO cho API ③ từ kết quả pipeline (theo tài liệu mục 5.2)."""
    return {
        "id": info["id"],
        "certificate_id": info["certificate_id"],
        "status": kq.verdict.value,  # APPROVED / REJECTED
        "courseId": info["courseId"],
        "employeeId": info["employeeId"],
        "comment": _learner_comment(kq),   # bắt buộc, hiển thị cho học viên
        "comment_cer": _certificate_comment(kq)[:1000],
    }


def run_forever(azure_client) -> None:
    """Lặp mãi tới khi Ctrl+C.

    Luật nghỉ: CÒN VIỆC THÌ LÀM TIẾP NGAY, hết việc mới nghỉ poll_interval.
    Nhưng "còn việc" ở đây đo bằng accepted_count (eLIS đã nhận), KHÔNG phải
    scanned_count (đã quét xong). Lý do: khi API ③ hỏng, bản ghi vẫn nằm ở
    WAITING nên vòng sau getCert trả về đúng những item đó. Nếu lấy scanned_count
    làm mốc thì job sẽ quay vòng KHÔNG NGHỈ, tải lại và gọi LLM lại cùng một
    tập chứng chỉ cho tới khi eLIS sống lại — vừa tốn tiền vừa không ai để ý
    vì nhìn log vẫn thấy "đang chạy". Đo bằng accepted_count thì lúc đó job tự
    hạ nhịp xuống mỗi poll_interval một lần, và quay lại chạy hết tốc độ
    ngay khi eLIS nhận được cái đầu tiên.
    """
    sleep_seconds = max(1, settings.poll_interval_seconds)
    logger.info("Chạy liên tục. Hết việc thì hỏi lại mỗi %d giây. "
                "Ctrl+C để dừng.", sleep_seconds)

    is_idle = False   # để dòng log "đang rảnh" chỉ in MỘT lần mỗi đợt rảnh
    while True:
        try:
            kq = process_one_batch(azure_client)
        except Exception as e:
            # Không để một lỗi bất kỳ giết job. logger.exception giữ lại
            # traceback — thiếu nó thì lỗi lạ chỉ còn một dòng vô nghĩa.
            logger.exception("Lỗi trong vòng xử lý: %s", e)
            kq = RoundResult(0, 0)

        # Tới giờ báo cáo thì gửi. Đặt SAU khi xử lý xong một vòng để số
        # liệu của vòng đó đã nằm trong DB. Hàm này rẻ khi chưa tới hạn
        # (chỉ đọc cấu hình + so giờ) nên gọi mỗi vòng không sao.
        try:
            scheduler.kiem_tra_va_gui()
        except Exception as e:
            logger.exception("Lỗi lịch báo cáo (không chặn xử lý): %s", e)

        if kq.accepted_count > 0:
            # Có tiến triển thật -> làm tiếp ngay, không nghỉ.
            is_idle = False
            continue

        if not is_idle:
            if kq.scanned_count > 0:
                logger.warning(
                    "Đã quét %d chứng chỉ nhưng eLIS không nhận cái nào. "
                    "Tạm nghỉ %d giây rồi thử lại.", kq.scanned_count, sleep_seconds)
            else:
                logger.info("Không còn chứng chỉ chờ duyệt. "
                            "Kiểm tra lại mỗi %d giây...", sleep_seconds)
            is_idle = True

        time.sleep(sleep_seconds)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "loop"
    if mode not in ("once", "loop"):
        print("Dùng: python run.py [loop|once]     (không ghi gì = loop)")
        return 1

    database.init_db()  # tạo bảng log nếu chưa có
    azure_client = ocr_azure.create_client()

    if mode == "once":
        kq = process_one_batch(azure_client)
        logger.info("Xong. Đã xử lý %d chứng chỉ, eLIS nhận %d.",
                    kq.scanned_count, kq.accepted_count)
        return 0

    try:
        run_forever(azure_client)
    except KeyboardInterrupt:
        # Ctrl+C là cách dừng BÌNH THƯỜNG, không phải sự cố -> không đổ
        # traceback ra màn hình.
        logger.info("Đã dừng theo yêu cầu (Ctrl+C).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())