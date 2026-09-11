import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import alert
import archive
import client

import file_utils
import llm_text
import llm_vision
import ocr_azure
import pipeline
import scheduler
from config import settings
from database import database
from process_data import code_from_email, normalize
from schemas import InputInfo, ProcessResult, Verdict

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s: %(message)s")

for _noisy in ("azure", "azure.core.pipeline.policies.http_logging_policy",
               "httpx", "httpcore", "urllib3", "openai", "PIL"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger("run")

#1. Kiểu dữ liệu và tiện ích chung

TECHNICAL_STAGES = frozenset(database.TECHNICAL_STAGES)


# Kết quả một vòng xử lý.
#   scanned_count  - số chứng chỉ đã quét
#   accepted_count - số cái eLIS xác nhận đã nhận
#   deferred_count - số cái chưa xử lý (giãn cách hoặc kẹt sau ca hỏng)
#   api_error      - có giá trị khi không gọi được eLIS
class RoundResult(NamedTuple):
    scanned_count: int
    accepted_count: int
    deferred_count: int = 0
    api_error: str | None = None

# Kết quả xử lý MỘT chứng chỉ.
# technical_failure=True -> cả vòng dừng tại đây (chặn đầu hàng).
class CertOutcome(NamedTuple):
    technical_failure: bool
    accepted_by_elis: bool


# Kỹ thuật = hệ thống chưa xử lý được (Azure lỗi, eLIS không trả file) -> WAITING.
# Nghiệp vụ = đọc được ảnh nhưng sai tên/khóa/ngày -> REJECTED.
def is_technical_failure(result: ProcessResult) -> bool:
    return result.stage in TECHNICAL_STAGES


# Ca BỎ QUA: đọc được ảnh nhưng không xác minh được danh tính (email ngoài công ty).
def is_skip(result: ProcessResult) -> bool:
    return result.stage == database.SKIP_STAGE

# Gọi eLIS, thử lại khi lỗi tạm thời (502, timeout).
# Tối đa RETRY_COUNT lần, cách nhau RETRY_DELAY_SECONDS giây.
def call_with_retry(func, *args, **kwargs):
    last_error = None
    for attempt in range(1, settings.retry_count + 1):
        try:
            return func(*args, **kwargs)
        except client.ElisError as e:
            last_error = e
            logger.warning("Lần %d/%d lỗi: %s",
                           attempt, settings.retry_count, e)
            if attempt < settings.retry_count:
                time.sleep(settings.retry_delay_seconds)
    raise last_error

#2. Lọc hàng đợi, báo hoãn, cảnh báo

# Những khóa nhân viên này ĐÃ được duyệt.
#
# Hỏi eLIS theo email: API ① nhận `employeeEmail` nên mỗi chứng chỉ chỉ tốn
# một request và dữ liệu luôn tươi. API trả về mọi bản ghi của người đó ở mọi
# trạng thái, nên lọc lại tại đây: đúng email và `submitStatus == "APPROVED"`.
#
# Phải tự kiểm email vì API bỏ qua tham số lạ trong im lặng (trả 200 kèm toàn
# bộ bản ghi). Phát hiện API không lọc thì trả set rỗng.
def completed_courses(email: str) -> set[str]:
    wanted = str(email or "").strip().lower()
    if not wanted:
        return set()

    rows = call_with_retry(client.get_by_email, wanted)

    courses, foreign = set(), 0
    for row in rows:
        if str(row.get("employeeEmail") or "").strip().lower() != wanted:
            foreign += 1
            continue
        # CHỈ lấy bản ghi ĐÃ DUYỆT, nếu không thì chứng chỉ WAITING đang xử lý
        # cũng "trùng" với chính nó và cả hàng đợi bị từ chối tự động.
        # Đọc `submitStatus`, không phải `status` (trạng thái đăng ký học).
        if str(row.get("submitStatus") or "").strip().upper() != "APPROVED":
            continue
        course = normalize(row.get("courseName"))
        if course:
            courses.add(course)

    if foreign:
        logger.error("API ① KHÔNG lọc theo employeeEmail: hỏi %s nhưng %d/%d "
                     "bản ghi trả về là của người khác. Bỏ qua luật chống nộp "
                     "trùng cho lượt này — sửa lại tên tham số trước khi tin "
                     "kết quả.", wanted, foreign, len(rows))
        return set()

    return courses


# LUẬT NGHIỆP VỤ (HR chốt): MỘT KHÓA HỌC CHỈ ĐƯỢC HỌC MỘT LẦN.
# Không có cửa sổ thời gian, không thêm luật kiểu "chỉ tính nếu duyệt trong
# vòng N tháng".


# Nhớ những khóa vừa được DUYỆT TRONG VÒNG NÀY, bịt khe hở khi hai bản ghi
# trùng nhau cùng nằm một vòng: eLIS chưa chắc kịp phản ánh cái đầu khi cái
# thứ hai hỏi. Xóa ở đầu mỗi vòng.
_approved_this_round: set[tuple[str, str]] = set()


# Chứng chỉ này có phải nộp trùng không?
def is_duplicate(info: dict) -> bool:
    if not settings.duplicate_check:
        return False

    email = str(info.get("employeeEmail") or "").strip().lower()
    course = normalize(info.get("courseName"))
    if not email or not course:
        return False        # thiếu dữ liệu thì KHÔNG đoán, để pipeline xử

    if (email, course) in _approved_this_round:
        return True

    try:
        return course in completed_courses(email)
    except client.ElisError as e:
        # Hỏng thì MỞ, không đóng: không tra được lịch sử thì chứng chỉ vẫn đi
        # tiếp theo luồng thường, tránh biến sự cố hạ tầng thành từ chối oan.
        logger.warning("Không tra được lịch sử của %s (%s) — bỏ qua luật "
                       "chống nộp trùng cho chứng chỉ này.", email, e)
        return False


# Nộp REJECTED cho ca nộp trùng: KHÔNG tải file, KHÔNG gọi LLM. Là kết luận
# nghiệp vụ nên vẫn gọi API ③, khác ca bỏ qua vốn để nguyên WAITING.
def reject_duplicate(info: dict) -> bool:
    result = ProcessResult(verdict=Verdict.REJECTED,
                           reason="Cán bộ nộp trùng khóa học",
                           stage="duplicate")
    logger.info("[TRÙNG] %s | %s — đã được duyệt khóa này, từ chối không quét.",
                info.get("employeeName") or "?",
                (info.get("courseName") or "?")[:45])
    try:
        database.write_failure_log(
            user_course_id=info.get("id"), employee_id=info.get("employeeId"),
            verdict=Verdict.REJECTED.value, reason=result.reason,
            stage="duplicate", provider=info.get("providerName"),
            course_name=info.get("courseName"))
    except Exception as e:
        logger.exception("Không ghi được log ca nộp trùng: %s", e)

    return submit_result(build_result_dto(result, info))


# Cắt hàng đợi tại chứng chỉ ĐẦU TIÊN chưa tới lượt thử lại.
# Trả về (ready, deferred, needs_alert):
#   ready       - xử lý vòng này, giữ đúng thứ tự eLIS trả về
#   deferred    - đang chờ hết giãn cách, kèm thời gian còn lại
#   needs_alert - đã hỏng tới ngưỡng, cần gửi email (vẫn ở lại hàng đợi)
def filter_queue(items: list[dict],
                 ignore_cooldown: bool = False) -> tuple[list, list, list]:
    # Loại ca BỎ QUA trước cả API ② tải file: chúng đã chạy hết pipeline một
    # lần và kết quả không đổi, quét lại chỉ tốn tiền LLM.
    skipped = database.skipped_ids([i["id"] for i in items])
    if skipped:
        report_skipped(items, skipped)
        items = [i for i in items if str(i["id"]) not in skipped]

    state = database.technical_retry_state([i["id"] for i in items])
    if not state:
        return items, [], []

    threshold = max(1, settings.technical_alert_after)
    cooldown = timedelta(
        minutes=max(0, settings.technical_retry_cooldown_minutes))
    now = datetime.now()

    ready, deferred, needs_alert = [], [], []
    for item in items:
        record = state.get(str(item["id"]))
        if not record:
            ready.append(item)          # never failed, keep its position
            continue

        failure_count, last_failed_at = record
        if failure_count >= threshold:
            needs_alert.append((item, failure_count))

        try:
            elapsed = now - datetime.fromisoformat(last_failed_at)
        except (TypeError, ValueError):
            elapsed = cooldown          # unreadable timestamp -> allow a try
        if elapsed < cooldown and not ignore_cooldown:
            deferred.append((item, failure_count, threshold, cooldown - elapsed))
            break                       # head-of-line: everyone behind waits
        ready.append(item)

    return ready, deferred, needs_alert

_last_skipped_ids: frozenset = frozenset()


# In danh sách chứng chỉ đang bị BỎ QUA — CHỈ khi tập id thay đổi.
# Trên eLIS chúng trông y hệt ca chưa tới lượt (cùng WAITING, comment rỗng),
# nên log này là chỗ duy nhất người vận hành thấy chúng.
def report_skipped(items: list[dict], skipped: set) -> None:
    global _last_skipped_ids
    if frozenset(skipped) == _last_skipped_ids:
        return
    _last_skipped_ids = frozenset(skipped)

    logger.info("Bỏ qua %d chứng chỉ KHÔNG XÁC MINH ĐƯỢC DANH TÍNH "
                "(chờ người duyệt xử lý trên eLIS):", len(skipped))
    for item in items:
        if str(item["id"]) in skipped:
            logger.info("    %s | %s | %s",
                        str(item["id"])[:8], item.get("employeeName") or "?",
                        (item.get("courseName") or "?")[:45])


_last_deferred_ids: frozenset = frozenset()


# In danh sách chứng chỉ đang chờ tới lượt — CHỈ khi tập id thay đổi.
def report_deferred(deferred: list[tuple]) -> None:
    global _last_deferred_ids
    current_ids = frozenset(str(item["id"]) for item, *_ in deferred)
    if current_ids == _last_deferred_ids:
        return
    _last_deferred_ids = current_ids

    if not deferred:
        logger.info("Không còn chứng chỉ nào bị hoãn.")
        return

    logger.info("Hoãn %d chứng chỉ HỎNG KỸ THUẬT, chưa tới lượt thử lại "
                "(ca từ chối vì sai tên/khóa học KHÔNG bị hoãn):", len(deferred))
    for item, failure_count, threshold, time_left in deferred:
        logger.info("    %s | %s | %s — hỏng %d lần%s, thử lại sau ~%.0f phút",
                    str(item["id"])[:8], item.get("employeeName") or "?",
                    (item.get("courseName") or "?")[:45], failure_count,
                    " (ĐÃ CẢNH BÁO)" if failure_count >= threshold else "",
                    time_left.total_seconds() / 60)


# Gửi email cho người vận hành khi có ca hỏng tới ngưỡng.
def alert_operator(needs_alert: list[tuple]) -> None:
    try:
        details = database.technical_failure_detail(
            [item["id"] for item, _ in needs_alert]) if needs_alert else {}
    except Exception as e:
        logger.warning("Không đọc được chi tiết lỗi để cảnh báo: %s", e)
        details = {}

    payload = []
    for item, failure_count in needs_alert:
        stage, reason = details.get(str(item["id"]), (None, None))
        payload.append({
            "id": item["id"],
            "employee_name": item.get("employeeName"),
            "course_name": item.get("courseName"),
            "failure_count": failure_count,
            "stage": stage,
            "reason": reason,
        })

    try:
        alert.send_alert(payload)
    except Exception as e:
        logger.exception("Lỗi khi gửi cảnh báo (không chặn xử lý): %s", e)

#3. Vòng xử lý với từng chứng chỉ

# Xử lý hàng đợi MỘT vòng: API ① lấy hàng đợi -> lọc giãn cách -> chạy tuần tự.
# Gặp ca hỏng kỹ thuật là DỪNG cả vòng (chặn đầu hàng).
# ignore_cooldown=True dành cho lệnh tay `python run.py retry`.
def process_one_round(azure_client, items: list[dict] | None = None,
                      ignore_cooldown: bool = False) -> RoundResult:
    # ---- API 1: fetch the queue ----
    if items is None:
        try:
            items = call_with_retry(client.get_pending_list, page=1, size=100)
        except client.ElisError as e:
            alert.api_failed(1, str(e))
            logger.error("API ① getCert THẤT BẠI: %s — không lấy được hàng đợi "
                         "nên vòng này không xử lý gì.", e)
            alert_operator([])
            return RoundResult(0, 0, 0, api_error=str(e))
        alert.api_succeeded(1)

    if not items:
        # debug, not info: in loop mode this would repeat every poll interval.
        logger.debug("Không có chứng chỉ chờ duyệt.")
        return RoundResult(0, 0)

    total_pending = len(items)

    _approved_this_round.clear()
    items, deferred, needs_alert = filter_queue(items, ignore_cooldown)

    if items:
        logger.info("Có %d chứng chỉ chờ duyệt%s.", len(items),
                    f" (bỏ qua {total_pending - len(items)} ca chưa tới lượt)"
                    if total_pending > len(items) else "")
    report_deferred(deferred)
    alert_operator(needs_alert)

    if not items:
        return RoundResult(0, 0, len(deferred))

    # ---- Process sequentially ----
    scanned = accepted = 0
    blocked_by = None
    for position, info in enumerate(items, start=1):
        outcome = handle_one_certificate(info, azure_client, position, len(items))
        if outcome.technical_failure:
            blocked_by = info
            break
        scanned += 1
        accepted += 1 if outcome.accepted_by_elis else 0

    if blocked_by:
        waiting = len(items) - scanned - 1
        logger.info("DỪNG VÒNG tại %s (%s) — hỏng kỹ thuật. %d chứng chỉ phía "
                    "sau chờ theo; thử lại chính ca này sau ~%d phút.",
                    blocked_by.get("employeeName") or "?",
                    (blocked_by.get("courseName") or "?")[:45],
                    max(0, waiting),
                    max(0, settings.technical_retry_cooldown_minutes))

    deferred_total = len(deferred) + max(0, len(items) - scanned -
                                         (1 if blocked_by else 0))
    return RoundResult(scanned, accepted, deferred_total)

# Tải -> quét -> ghi log -> nộp cho ĐÚNG MỘT chứng chỉ.
# Trả technical_failure=True khi cả vòng phải dừng tại đây.
def handle_one_certificate(info: dict, azure_client,
                           position: int, total: int) -> CertOutcome:
    uc_id = info["id"]

    # Kiểm nộp trùng TRƯỚC khi tải file để ca trùng không tốn lượt LLM.
    if is_duplicate(info):
        return CertOutcome(False, reject_duplicate(info))

    # ---- API 2: download this one file ----
    pair = [{"UserCourseId": uc_id, "certificate_id": info["certificate_id"]}]
    try:
        files = call_with_retry(client.download_certificates, pair)
    except client.ElisError as e:
        alert.api_failed(2, str(e))
        logger.error("[%d/%d] Tải file cho %s lỗi: %s",
                     position, total, uc_id, e)
        log_technical_failure(info, f"Không tải được file từ eLIS: {e}",
                              stage="download_error")
        return CertOutcome(True, False)
    alert.api_succeeded(2)

    downloaded = next((f for f in files if f["userCourseId"] == uc_id), None)
    if downloaded is None:
        logger.warning("[%d/%d] eLIS không trả về file cho %s.",
                       position, total, uc_id)
        log_technical_failure(info, "eLIS không trả về file cho chứng chỉ này",
                              stage="no_file")
        return CertOutcome(True, False)

    archive_path = None
    if settings.save_certificates:
        archive_path = archive.save(downloaded["anh_bytes"], info,
                                    PROJECT_ROOT / settings.archive_dir)

    result = scan_certificate(downloaded["anh_bytes"], info, azure_client)
    archive.write_verdict(archive_path, result)
    technical = is_technical_failure(result)
    displayed = Verdict.WAITING if technical else result.verdict

    logger.info("[%d/%d] [%s] %s (%s) | %s | stage: %s",
                position, total, displayed.value,
                info.get("employeeName") or "?",
                info.get("employeeId") or uc_id,
                result.reason or "-", result.stage)

    try:
        database.write_log(result,
                           employee_id=info.get("employeeId"),
                           user_course_id=uc_id,
                           provider=info.get("providerName"),
                           course_name=info.get("courseName"),
                           verdict_override=displayed.value if technical else None)
    except Exception as e:
        logger.warning("Ghi log lỗi (không chặn xử lý): %s", e)

    if technical:
        return CertOutcome(True, False)

    # Ca BỎ QUA: không gọi API ③, bản ghi ở lại WAITING.
    if is_skip(result):
        return CertOutcome(False, False)

    # ---- API 3: submit this one result ----
    accepted = submit_result(build_result_dto(result, info))
    if accepted and result.verdict == Verdict.APPROVED:
        _approved_this_round.add((
            str(info.get("employeeEmail") or "").strip().lower(),
            normalize(info.get("courseName"))))
    return CertOutcome(False, accepted)


#4. Nộp kết quả, ghi log, gọi pipeline

# Gọi API ③ nộp kết quả MỘT chứng chỉ. True = eLIS đã nhận.
# API ③ hỏng là mất kết quả đã trả phí Gemma + Azure, vòng sau quét lại từ
# đầu, nên phải đếm để cảnh báo.
def submit_result(dto: dict) -> bool:
    try:
        data = call_with_retry(client.update_status, [dto])
    except client.ElisError as e:
        alert.api_failed(3, str(e))
        logger.error("API ③ nộp kết quả cho %s THẤT BẠI: %s — chứng chỉ này sẽ "
                     "bị quét lại ở vòng sau.", dto["id"], e)
        record_send_status(dto["id"], False, f"Không gửi được: {e}")
        return False

    alert.api_succeeded(3)
    succeeded = data.get("successList", []) or []
    failed = data.get("failList", []) or []

    for entry in succeeded:
        record_send_status(entry.get("id"), True)
    for entry in failed:
        # failList wraps entries as {"data": {...}, "message": "..."}.
        payload = entry.get("data") or entry
        message = entry.get("message", "")
        logger.warning("eLIS từ chối id=%s: %s", payload.get("id"), message)
        record_send_status(payload.get("id"), False, message)

    return bool(succeeded)


# Ghi log ca hỏng kỹ thuật với verdict WAITING, KHÔNG phải REJECTED.
def log_technical_failure(info: dict, reason: str,
                          stage: str = "download_error") -> None:
    logger.info("[%s] %s (%s) | %s | stage: %s",
                Verdict.WAITING.value,
                info.get("employeeName") or "?",
                info.get("employeeId") or info.get("id"),
                reason, stage)
    try:
        database.write_failure_log(
            user_course_id=info["id"],
            employee_id=info.get("employeeId"),
            verdict=Verdict.WAITING.value,
            reason=reason,
            stage=stage,
            provider=info.get("providerName"),
            course_name=info.get("courseName"),
        )
    except Exception as e:
        logger.warning("Ghi log thất bại lỗi: %s", e)


# Đánh dấu eLIS có nhận kết quả không (elis_sent_ok).
# Lỗi ghi DB chỉ log cảnh báo, không chặn luồng.
def record_send_status(user_course_id, succeeded: bool, message=None) -> None:
    if not user_course_id:
        return
    try:
        database.update_send_result(user_course_id, succeeded, message)
    except Exception as e:
        logger.warning("Cập nhật trạng thái gửi lỗi: %s", e)


# Chạy pipeline ba tầng (Gemma -> Azure OCR + LLM2 -> so đồng thuận)
def scan_certificate(image_bytes: bytes, info: dict, azure_client) -> ProcessResult:
    """Run the pipeline for one certificate."""
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as tmp:
        tmp.write(image_bytes)
        tmp_path = tmp.name

    try:
        images = file_utils.read_as_images(tmp_path)
    except file_utils.InvalidFileError as e:
        return ProcessResult(
            employee_code=info.get("employeeId"),
            verdict=Verdict.REJECTED,
            reason=f"File không hợp lệ: {e}",
            stage="file_error",
        )
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    match_code = code_from_email(info.get("employeeEmail"))
    if not match_code:
        logger.warning("Không lấy được mã đối chiếu từ employeeEmail=%r "
                       "— chỉ còn đối chiếu bằng tên.", info.get("employeeEmail"))

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

#5. Chuyển kết quả thành câu chữ cho elis
def learner_comment(result: ProcessResult) -> str:
    if result.verdict == Verdict.APPROVED:
        return "Hợp lệ"
    return result.reason or "Chứng chỉ không hợp lệ"

# Dựng payload cho API ③.
# employeeId là mã eLIS cấp, KHÁC employee_code (username dùng đối chiếu ảnh).
def build_result_dto(result: ProcessResult, info: dict) -> dict:
    return {
        "id": info["id"],
        "certificate_id": info["certificate_id"],
        "status": result.verdict.value,          # APPROVED / REJECTED
        "courseId": info["courseId"],
        "employeeId": info["employeeId"],
        "comment": learner_comment(result),
    }


#6. chạy chương trình
def run_forever(azure_client) -> None:
    sleep_seconds = max(1, settings.poll_interval_seconds)
    logger.info("Chạy liên tục. Hết việc thì hỏi lại mỗi %d giây. Ctrl+C để dừng.",
                sleep_seconds)

    idle = False        # so the "idle" line prints once per idle spell
    while True:
        try:
            result = process_one_round(azure_client)
        except Exception as e:
            logger.exception("Lỗi trong vòng xử lý: %s", e)
            result = RoundResult(0, 0)
        try:
            scheduler.check_and_send()
        except Exception as e:
            logger.exception("Lỗi lịch báo cáo (không chặn xử lý): %s", e)

        if result.accepted_count > 0:
            idle = False
            continue                    # real progress -> keep going now

        if not idle:
            if result.api_error:
                logger.error("KHÔNG gọi được eLIS (API ①): %s. Hệ thống đang đứng im "
                             "— không xử lý được chứng chỉ nào. Thử lại mỗi %d giây.",
                             result.api_error, sleep_seconds)
            elif result.scanned_count > 0:
                logger.warning("Đã quét %d chứng chỉ nhưng eLIS không nhận cái nào. "
                               "Tạm nghỉ %d giây rồi thử lại.",
                               result.scanned_count, sleep_seconds)
            elif result.deferred_count > 0:
                logger.info("%d chứng chỉ đang chờ tới lượt thử lại, chưa có việc nào "
                            "làm được ngay. Kiểm tra lại mỗi %d giây...",
                            result.deferred_count, sleep_seconds)
            else:
                logger.info("Không còn chứng chỉ chờ duyệt. "
                            "Kiểm tra lại mỗi %d giây...", sleep_seconds)
            idle = True

        time.sleep(sleep_seconds)


# Chỉ đọc: in hàng đợi eLIS kèm trạng thái thử lại.
def print_status() -> int:
    items = call_with_retry(client.get_pending_list, page=1, size=100)
    if not items:
        print("Không có chứng chỉ chờ duyệt.")
        return 0

    state = database.technical_retry_state([i["id"] for i in items])
    threshold = max(1, settings.technical_alert_after)
    cooldown = timedelta(
        minutes=max(0, settings.technical_retry_cooldown_minutes))
    now = datetime.now()

    print(f"\n{len(items)} certificate(s) pending "
          f"(retry cooldown: {cooldown.total_seconds() / 60:.0f} min, "
          f"alert email after {threshold} failures - never gives up)\n")
    header = (f"{'user_course_id':<38} {'Employee':<22} "
              f"{'Course':<42} {'State'}")
    print(header)
    print("-" * len(header))

    for item in items:
        record = state.get(str(item["id"]))
        if not record:
            description = "new, will run next round"
        else:
            failure_count, last_failed_at = record
            try:
                elapsed = now - datetime.fromisoformat(last_failed_at)
            except (TypeError, ValueError):
                elapsed = cooldown
            alerted = " (ĐÃ CẢNH BÁO)" if failure_count >= threshold else ""
            if elapsed < cooldown:
                left = (cooldown - elapsed).total_seconds() / 60
                description = (f"failed {failure_count}x{alerted} - "
                               f"~{left:.0f} min to go")
            else:
                description = f"failed {failure_count}x{alerted} - due now"
        print(f"{str(item['id']):<38} "
              f"{(item.get('employeeName') or '?')[:21]:<22} "
              f"{(item.get('courseName') or '?')[:41]:<42} {description}")

    print("\nTo retry immediately (skip the cooldown): python run.py retry")
    return 0


# Điểm vào: chọn chế độ loop / once / retry / status rồi chạy.
# status KHÔNG tạo client Azure, để lệnh chỉ-đọc không phụ thuộc key Azure.
def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "loop"
    if mode not in ("once", "loop", "retry", "status"):
        print("Usage: python run.py [loop|once|retry|status]\n"
              "  loop   : run continuously (default)\n"
              "  once   : process one round, then exit\n"
              "  retry  : one round, IGNORING the retry cooldown\n"
              "           (use when you know Azure/eLIS has recovered)\n"
              "  status : READ-ONLY queue dump with retry state.\n"
              "           No processing, no LLM cost.")
        return 1

    database.init_db()

    if mode == "status":
        return print_status()

    azure_client = ocr_azure.create_client()

    if mode in ("once", "retry"):
        if mode == "retry":
            logger.info("Chế độ retry: bỏ qua giãn cách, thử lại NGAY mọi ca "
                        "hỏng kỹ thuật.")
        result = process_one_round(azure_client,
                                   ignore_cooldown=(mode == "retry"))
        logger.info("Xong. Đã xử lý %d chứng chỉ, eLIS nhận %d.",
                    result.scanned_count, result.accepted_count)
        return 0

    try:
        run_forever(azure_client)
    except KeyboardInterrupt:
        # Ctrl+C is the normal way to stop, not a crash — no traceback.
        logger.info("Đã dừng theo yêu cầu (Ctrl+C).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
