
import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import logging
import sys
import time
from datetime import datetime, timedelta
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
from process_data import code_from_email
from schemas import Verdict, InputInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

for _ten in ("azure", "azure.core.pipeline.policies.http_logging_policy",
             "httpx", "httpcore", "urllib3", "openai", "PIL"):
    logging.getLogger(_ten).setLevel(logging.WARNING)

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


# Stage nghĩa là "hỏng kỹ thuật" — hệ thống chưa xử lý được, KHÔNG phải
# nhân viên khai sai. Lấy thẳng từ database để hai nơi không lệch nhau.
TECHNICAL_STAGES = frozenset(database.TECHNICAL_STAGES)


def _la_hong_ky_thuat(kq) -> bool:
    return kq.stage in TECHNICAL_STAGES


def _sap_xep_va_loc(items: list[dict],
                    bo_qua_cooldown: bool = False) -> tuple[list, list, list]:
    """Sắp xếp hàng đợi và loại những ca chưa tới lượt thử lại.

    Trả về (danh_sach_xu_ly, danh_sach_hoan, danh_sach_bo_cuoc).
    Mỗi phần tử danh_sach_hoan là (item, so_lan_hong, gioi_han, thoi_gian_con_lai).

    BA NHÓM, ba cách đối xử khác nhau:
      - Chưa hỏng lần nào  -> làm TRƯỚC. Việc mới không được xếp sau việc
        đang mắc kẹt, nếu không một chứng chỉ hỏng vĩnh viễn sẽ chặn cả
        hàng đợi.
      - Đã hỏng, chưa hết cooldown -> BỎ QUA vòng này. Không tải, không gọi
        LLM. Đây là chỗ tiết kiệm thật: không có nó thì mỗi 5 giây lại tốn
        một lượt LLM cho cùng một chứng chỉ.
      - Đã hỏng quá số lần cho phép -> BỎ CUỘC, nộp REJECTED thật. Không có
        nhánh này thì bản ghi nằm WAITING vĩnh viễn và không ai biết.
    """
    trang_thai = database.technical_retry_state([i["id"] for i in items])
    if not trang_thai:
        return items, [], []

    gioi_han = max(1, settings.technical_retry_max)
    nghi = timedelta(minutes=max(0, settings.technical_retry_cooldown_minutes))
    bay_gio = datetime.now()

    moi, thu_lai, bo_cuoc, hoan = [], [], [], []
    for it in items:
        tt = trang_thai.get(str(it["id"]))
        if not tt:
            moi.append(it)
            continue
        so_lan, lan_cuoi = tt
        if so_lan >= gioi_han:
            bo_cuoc.append((it, so_lan))
            continue
        try:
            da_qua = bay_gio - datetime.fromisoformat(lan_cuoi)
        except (TypeError, ValueError):
            da_qua = nghi                      # không đọc được thì cho thử
        if da_qua < nghi and not bo_qua_cooldown:
            hoan.append((it, so_lan, gioi_han, nghi - da_qua))
            continue
        thu_lai.append(it)

    # Ca thử lại xếp SAU toàn bộ ca mới — đúng yêu cầu "đẩy về dưới cùng".
    return moi + thu_lai, hoan, bo_cuoc


# Danh sách id đã báo hoãn ở lần gần nhất. Chỉ để tránh lặp log, không mang
# nghĩa nghiệp vụ nào — mất khi restart cũng không sao.
_da_bao_hoan: frozenset = frozenset()


def _bao_ca_hoan(hoan: list[tuple]) -> None:
    """In các ca đang chờ tới lượt thử lại — CHỈ khi danh sách thay đổi.

    IN ĐỦ BỐN THÔNG TIN cho mỗi ca: là ai, hỏng mấy lần rồi, và còn bao lâu
    nữa mới thử lại. Bản trước chỉ in "Hoãn 1 chứng chỉ" — người đọc không
    biết ca nào bị hoãn, nên khi thấy nó nằm cạnh một ca vừa bị từ chối vì
    sai tên thì tưởng hệ thống đang hoãn nhầm cả ca nghiệp vụ.

    Chỉ in khi TẬP id thay đổi. Vòng lặp chạy mỗi vài giây còn cooldown là
    hàng tiếng: in mỗi vòng thì 6 tiếng chờ sinh ra hàng nghìn dòng giống hệt.
    """
    global _da_bao_hoan
    tap_id = frozenset(str(it["id"]) for it, *_ in hoan)
    if tap_id == _da_bao_hoan:
        return
    _da_bao_hoan = tap_id

    if not hoan:
        logger.info("Không còn chứng chỉ nào bị hoãn.")
        return

    logger.info("Hoãn %d chứng chỉ HỎNG KỸ THUẬT, chưa tới lượt thử lại "
                "(ca từ chối vì sai tên/khóa học KHÔNG bị hoãn):", len(hoan))
    for it, so_lan, gioi_han, con_lai in hoan:
        # In cả TÊN KHÓA HỌC: người vận hành nhìn màn hình eLIS thấy tên khóa
        # chứ không thấy user_course_id. Chỉ in id thì không đối chiếu được
        # dòng log với dòng trên eLIS, và dễ tưởng hệ thống đang bỏ sót.
        logger.info("    %s | %s | %s — hỏng %d/%d lần, thử lại sau ~%.1f tiếng",
                    str(it["id"])[:8], it.get("employeeName") or "?",
                    (it.get("courseName") or "?")[:45],
                    so_lan, gioi_han, con_lai.total_seconds() / 3600)


def _bo_cuoc(bo_cuoc: list[tuple]) -> int:
    """Nộp REJECTED cho những ca đã thử quá số lần cho phép.

    Phải có nhánh này, nếu không bản ghi nằm WAITING vĩnh viễn: eLIS thấy nó
    "đang chờ duyệt" mãi mãi, còn job thì bỏ qua vì hết lượt. Học viên không
    nhận được kết luận nào và cũng không ai biết để xử lý tay.

    Lý do gửi kèm nói rõ đây là lỗi hệ thống, không đổ cho học viên.
    """
    if not bo_cuoc:
        return 0

    dto = []
    for it, so_lan in bo_cuoc:
        logger.error("BỎ CUỘC sau %d lần: %s (%s) — nộp REJECTED.",
                     so_lan, it.get("employeeName") or "?", it["id"])
        ly_do = (f"Hệ thống đã thử {so_lan} lần nhưng không xử lý được "
                 f"chứng chỉ này. Vui lòng liên hệ bộ phận đào tạo.")
        try:
            database.write_failure_log(
                user_course_id=it["id"], employee_id=it.get("employeeId"),
                verdict=Verdict.REJECTED.value, reason=ly_do,
                stage="system_error", provider=it.get("providerName"),
                course_name=it.get("courseName"))
        except Exception as e:
            logger.warning("Ghi log bỏ cuộc lỗi: %s", e)
        dto.append({
            "id": it["id"],
            "certificate_id": it["certificate_id"],
            "status": Verdict.REJECTED.value,
            "courseId": it["courseId"],
            "employeeId": it["employeeId"],
            "comment": "Chưa xử lý được chứng chỉ, vui lòng liên hệ bộ phận đào tạo",
            "comment_cer": ly_do[:1000],
        })
    return _submit_results(dto, 0)


def process_one_batch(azure_client, items: list[dict] | None = None,
                      dang_thu_lai: bool = False,
                      bo_qua_cooldown: bool = False) -> RoundResult:
    """Xử lý một batch chứng chỉ chờ duyệt.

    items=None  -> tự lấy danh sách từ eLIS (lượt chạy bình thường).
    items=[...] -> xử lý đúng danh sách đó; dùng cho lượt thử lại cuối vòng.

    dang_thu_lai=True chặn đệ quy VÀ bỏ qua cooldown: lượt thử lại là lượt
    được phép chạy ngay. Hỏng lần nữa thì để nguyên WAITING cho vòng poll
    sau, không gọi thử lại lần ba trong cùng một vòng.

    bo_qua_cooldown=True là lệnh tay `python run.py retry`: người vận hành
    biết sự cố đã khỏi và muốn thử ngay, không đợi hết giãn cách.
    """
    # ===== ① Lấy danh sách chờ duyệt =====
    if items is None:
        items = call_with_retry(client.get_pending_list, page=1, size=100)
    if not items:
        # debug chứ không info: ở chế độ chạy liên tục dòng này sẽ lặp mỗi
        # poll_interval giây và nhấn chìm log thật. main() đã báo trạng thái
        # "hết việc" đúng MỘT lần khi job chuyển sang rảnh.
        logger.debug("Không có chứng chỉ chờ duyệt.")
        return RoundResult(0, 0)

    # Lượt thử lại KHÔNG lọc lại: các ca này vừa hỏng xong nên chắc chắn
    # đang trong cooldown, lọc lại là loại sạch chính thứ vừa gom để thử.
    if dang_thu_lai:
        hoan, bo_cuoc, accepted_bo_cuoc = [], [], 0
    else:
        tong_cho = len(items)
        items, hoan, bo_cuoc = _sap_xep_va_loc(items, bo_qua_cooldown)
        # Báo SAU KHI LỌC, và chỉ khi thật sự có việc.
        #
        # Báo trước khi lọc thì khi mọi ca đều đang trong cooldown, dòng "Có N
        # chứng chỉ chờ duyệt" vẫn in mỗi vòng — với chu kỳ 5 giây và cooldown
        # 6 tiếng, đó là hơn 4.000 dòng nói về việc mà job KHÔNG làm.
        if items:
            logger.info("Có %d chứng chỉ chờ duyệt%s.", len(items),
                        f" (bỏ qua {tong_cho - len(items)} ca chưa tới lượt)"
                        if tong_cho > len(items) else "")
        _bao_ca_hoan(hoan)
        accepted_bo_cuoc = _bo_cuoc(bo_cuoc)

    if not items:
        return RoundResult(len(bo_cuoc), accepted_bo_cuoc)

    # Cache map id -> thông tin, để dùng ở bước ③ (theo tài liệu mục 6).
    info_by_id = {item["id"]: item for item in items}

    processed_count = 0
    accepted_count = 0
    hong_ky_thuat: list[dict] = []
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

            # Ca hỏng kỹ thuật KHÔNG bị nộp eLIS nên nó vẫn đang WAITING.
            # Hiện REJECTED cho nó là nói sai với chính người vận hành: họ
            # đọc log rồi đi báo học viên "chứng chỉ bị từ chối", trong khi
            # hệ thống chỉ đang hẹn thử lại sau vài tiếng.
            hong = _la_hong_ky_thuat(kq)
            hien_thi = Verdict.WAITING if hong else kq.verdict

            # In kết luận NGAY khi có, trước cả khi ghi DB hay nộp eLIS.
            # Không có dòng này thì màn hình chỉ hiện "Có 5 chứng chỉ chờ
            # duyệt" rồi "Nộp lô 1: 5 thành công" — người vận hành không
            # biết cái nào đậu, cái nào trượt, trượt vì lý do gì, mà phải
            # mở mooc_log.db lên xem. Đặt trước ghi DB để nếu DB có hỏng
            # thì kết luận vẫn còn trên màn hình.
            logger.info("[%s] %s (%s) | %s | tầng: %s",
                        hien_thi.value,
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
                                 provider=info.get("providerName"),
                                 course_name=info.get("courseName"),
                                 verdict_override=hien_thi.value if hong else None)
            except Exception as e:
                logger.warning("Ghi log lỗi (không chặn xử lý): %s", e)
            if hong:
                # KHÔNG nộp. Bản ghi ở lại WAITING trên eLIS để còn được xử
                # lý lại — nộp REJECTED là đóng vĩnh viễn một chứng chỉ mà hệ
                # thống chưa hề đánh giá được nội dung.
                hong_ky_thuat.append(info)
                continue
            batch_results.append(_build_result_dto(kq, info))

        # ===== ③ Nộp kết quả của RIÊNG lô này =====
        if batch_results:
            accepted_count += _submit_results(batch_results, batch_index)
            processed_count += len(batch_results)

    # ===== Thử lại các ca hỏng kỹ thuật, SAU KHI đã xong hết việc khác =====
    #
    # Đặt ở cuối chứ không thử ngay tại chỗ: sự cố thường theo cụm (Azure quá
    # tải, eLIS chập). Thử ngay lại chỉ gặp đúng sự cố đó. Chạy xong hết việc
    # khác rồi quay lại thì đã trôi qua vài chục giây tới vài phút — đủ để
    # nhiều sự cố tạm thời tự khỏi.
    if hong_ky_thuat and not dang_thu_lai:
        logger.info("Thử lại %d chứng chỉ hỏng kỹ thuật (cuối hàng đợi).",
                    len(hong_ky_thuat))
        kq_thu_lai = process_one_batch(azure_client, items=hong_ky_thuat,
                                       dang_thu_lai=True)
        processed_count += kq_thu_lai.scanned_count
        accepted_count += kq_thu_lai.accepted_count

    return RoundResult(processed_count + len(bo_cuoc),
                       accepted_count + accepted_bo_cuoc)


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
    """Ghi log cho từng item trong lô không xử lý được.

    Ghi WAITING chứ KHÔNG phải REJECTED: những ca này không hề được nộp về
    eLIS (hàm gọi chỉ `continue`), nên bên eLIS chúng vẫn đang chờ duyệt và
    sẽ được thử lại. REJECTED ở đây là một lời nói dối trong log.
    """
    for it in batch:
        # Ca hỏng kỹ thuật cũng phải hiện kết luận trên màn hình như ca chạy
        # được, nếu không chúng chỉ nằm im trong DB và người vận hành tưởng
        # là chưa xử lý tới.
        logger.info("[%s] %s (%s) | %s | tầng: %s",
                    Verdict.WAITING.value,
                    it.get("employeeName") or "?",
                    it.get("employeeId") or it.get("id"),
                    reason, stage)
        try:
            database.write_failure_log(
                user_course_id=it["id"],
                employee_id=it.get("employeeId"),
                verdict=Verdict.WAITING.value,
                reason=reason,
                stage=stage,
                provider=it.get("providerName"),
                course_name=it.get("courseName"),
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
    match_code = code_from_email(info.get("employeeEmail"))
    if not match_code:
        # Cảnh báo để không hỏng âm thầm: thiếu mã đối chiếu thì chứng chỉ in
        # username sẽ bị REJECTED với lý do "tên không khớp", trông y hệt
        # trường hợp nhân viên nộp nhầm — rất khó lần ra nguyên nhân thật.
        logger.warning("Không lấy được mã đối chiếu từ employeeEmail=%r "
                       "(thiếu @ hoặc bỏ trống) — chỉ còn đối chiếu bằng tên.",
                       info.get("employeeEmail"))
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


def print_status() -> int:
    """In hàng đợi hiện tại của eLIS kèm trạng thái thử lại của từng ca.

    Vì sao cần lệnh này: khi một chứng chỉ nằm im trên eLIS, câu hỏi đầu tiên
    luôn là "cái nào?" — mà màn hình eLIS chỉ hiện TÊN KHÓA còn log chỉ hiện
    user_course_id. Không có bảng nối hai thứ đó thì cách duy nhất là so mốc
    thời gian giữa log console và DB, tức là ĐOÁN. Lệnh này hỏi thẳng API ①
    nên nó là dữ liệu, không phải suy luận.

    Chỉ ĐỌC: không tải file, không gọi LLM, không nộp gì về eLIS. Chạy lúc job
    đang chạy nền cũng được.
    """
    items = call_with_retry(client.get_pending_list, page=1, size=100)
    if not items:
        print("Không có chứng chỉ nào đang chờ duyệt.")
        return 0

    trang_thai = database.technical_retry_state([i["id"] for i in items])
    gioi_han = max(1, settings.technical_retry_max)
    nghi = timedelta(minutes=max(0, settings.technical_retry_cooldown_minutes))
    bay_gio = datetime.now()

    print(f"\n{len(items)} chứng chỉ đang chờ duyệt "
          f"(giãn cách thử lại: {nghi.total_seconds() / 3600:.1f} tiếng, "
          f"tối đa {gioi_han} lần)\n")
    header = f"{'user_course_id':<38} {'Nhân viên':<22} {'Khóa học':<42} {'Trạng thái'}"
    print(header)
    print("-" * len(header))

    for it in items:
        tt = trang_thai.get(str(it["id"]))
        if not tt:
            mo_ta = "mới, sẽ xử lý ở vòng tới"
        else:
            so_lan, lan_cuoi = tt
            try:
                da_qua = bay_gio - datetime.fromisoformat(lan_cuoi)
            except (TypeError, ValueError):
                da_qua = nghi
            if so_lan >= gioi_han:
                mo_ta = f"hết lượt ({so_lan}/{gioi_han}) — sẽ nộp REJECTED"
            elif da_qua < nghi:
                con = (nghi - da_qua).total_seconds() / 3600
                mo_ta = f"hỏng {so_lan}/{gioi_han} — chờ thêm ~{con:.1f} tiếng"
            else:
                mo_ta = f"hỏng {so_lan}/{gioi_han} — đã tới lượt thử lại"
        print(f"{str(it['id']):<38} {(it.get('employeeName') or '?')[:21]:<22} "
              f"{(it.get('courseName') or '?')[:41]:<42} {mo_ta}")

    print("\nMuốn thử lại NGAY (bỏ qua giãn cách): python run.py retry")
    return 0


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "loop"
    if mode not in ("once", "loop", "retry", "status"):
        print("Dùng: python run.py [loop|once|retry|status]\n"
              "  loop  : chạy liên tục (mặc định)\n"
              "  once  : xử lý một lượt rồi thoát\n"
              "  retry : xử lý một lượt, BỎ QUA giãn cách của ca hỏng kỹ thuật\n"
              "          (dùng khi biết sự cố Azure/eLIS đã khỏi, muốn thử ngay)\n"
              "  status: CHỈ XEM — in hàng đợi eLIS kèm tên khóa học và\n"
              "          trạng thái thử lại. Không xử lý, không tốn lượt LLM.")
        return 1

    database.init_db()  # tạo bảng log nếu chưa có

    # status không đụng tới ảnh nên không cần client Azure. Tạo client trước
    # sẽ bắt lệnh chỉ-xem phụ thuộc vào key Azure còn hạn hay không.
    if mode == "status":
        return print_status()

    azure_client = ocr_azure.create_client()

    if mode == "retry":
        logger.info("Chế độ retry: bỏ qua giãn cách, thử lại NGAY mọi ca "
                    "hỏng kỹ thuật còn trong hạn.")
        kq = process_one_batch(azure_client, bo_qua_cooldown=True)
        logger.info("Xong. Đã xử lý %d chứng chỉ, eLIS nhận %d.",
                    kq.scanned_count, kq.accepted_count)
        return 0

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
