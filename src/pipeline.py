"""Điều phối xử lý MỘT chứng chỉ (pipeline).

Nối các module theo đúng sơ đồ ba lần so:

  0. Lọc trước (nếu bật): ảnh là DANH SÁCH nhiều khóa học -> BỎ QUA (WAITING),
     không gọi LLM1/Azure.

  1. Gemma (LLM1) đọc ảnh -> so tên + khóa học với input người nhập.
     Cả hai khớp -> APPROVED (dừng, không tốn lượt OCR).
     Cả hai khớp nhưng NGÀY trượt -> OCR + LLM2 đọc lại ngày; ngày LLM2
     trong khoảng -> APPROVED, ngoài khoảng -> REJECTED.

  2. Không khớp -> OCR + LLM2 đọc lại từ ảnh. Nhà cung cấp OCR do
     OCR_PROVIDER chọn (Azure hoặc OCR.space) — xem src/ocr.py.

  3. So LLM1 với LLM2 (chặt tuyệt đối):
     Giống nhau -> REJECTED (hai máy đồng thuận: ảnh khác input).

  4. Khác nhau -> so LLM2 với input:
     Khớp -> APPROVED, không khớp -> REJECTED.

pipeline chỉ xử lý MỘT chứng chỉ. Việc lặp qua nhiều cái nằm ở run.py.
"""

import logging

import compare
import process_data
from config import settings
from schemas import Verdict, ProcessResult, InputInfo, ExtractedInfo

logger = logging.getLogger(__name__)

# Ca BỎ QUA: đọc được ảnh nhưng không xác minh được danh tính người học.
# KHÔNG phải hỏng kỹ thuật (thử lại không bao giờ khỏi) và KHÔNG phải kết luận
# nghiệp vụ (không nộp gì về eLIS). Xem run.py mục 2.
SKIP_STAGE = "skipped_external_email"

# Ca BỎ QUA: ảnh là DANH SÁCH nhiều khóa học (trang hồ sơ "Registrations",
# "Enrollments"...) chứ không phải chứng chỉ của riêng khóa này. Phát hiện
# TRƯỚC LLM1. Cùng hằng số với database.SKIP_MULTI_COURSE_STAGE.
SKIP_MULTI_COURSE_STAGE = "skipped_multi_course"


def _course_list_reason(images, detect_course_list) -> str | None:
    """Lý do bỏ qua nếu ảnh là danh sách nhiều khóa học, không thì None.

    Đòi CẢ HAI tín hiệu: page_type "course_list" VÀ từ 2 tên khóa trở lên.
    Chỉ một tín hiệu thì đi tiếp như cũ — bỏ qua nhầm một chứng chỉ thật tệ
    hơn để lọt một ảnh danh sách (lọt thì pipeline vẫn đối chiếu như trước).

    Bước lọc lỗi (Gemma hỏng, JSON lạ) -> đi tiếp, KHÔNG tính hỏng kỹ thuật:
    đây chỉ là bộ lọc phụ, không đáng chặn cả hàng đợi.
    """
    if detect_course_list is None:
        return None
    try:
        kind = detect_course_list(images[0])
    except Exception as e:
        logger.warning("Bước lọc ảnh nhiều khóa học lỗi, bỏ qua bước này: %s", e)
        return None

    titles = list(dict.fromkeys(
        t.strip() for t in kind.course_titles if t and t.strip()))
    if (kind.page_type or "").strip().lower() != "course_list" or len(titles) < 2:
        return None
    shown = ", ".join(titles[:5]) + (", ..." if len(titles) > 5 else "")
    return (f"Ảnh là danh sách {len(titles)} khóa học ({shown}), không phải "
            f"chứng chỉ riêng của khóa này — chờ người duyệt")


def _both_fields_match(extracted: ExtractedInfo, given: InputInfo) -> bool:
    """True khi tên VÀ khóa học khớp với input.

    - Tên: khớp tên nhân viên hoặc mã nhân viên.
    - Khóa học: khớp (hỗ trợ song ngữ, khớp một trong hai ngôn ngữ là đủ).
    Thời gian kiểm riêng (_date_in_range) để ghi được lý do rõ ràng.
    """
    return (
        compare.match_name_or_code(
            extracted.recipient_name, given.employee_name, given.employee_code
        )
        and compare.match_course_bilingual(
            extracted.certificate_name, extracted.certificate_name_alt, given.course_name,
            settings.course_match_mode,
        )
    )


def _date_in_range(extracted: ExtractedInfo) -> bool:
    """Ngày hoàn thành trên chứng chỉ có nằm trong khoảng công ty quy định không."""
    return process_data.date_in_range(
        extracted.issue_date,
        settings.valid_from,
        settings.valid_to,
    )


def _date_reason(extracted: ExtractedInfo, other: ExtractedInfo | None = None) -> str:
    """Câu lý do cho trường NGÀY, tách ba trường hợp.

    Bản đọc chính không có ngày mà bản kia có -> nói theo bản kia: "không có
    ngày" là sai sự thật khi một máy vẫn đọc ra được một ngày.
    """
    source = extracted
    if (not (extracted.issue_date or "").strip() and other is not None
            and (other.issue_date or "").strip()):
        source = other

    status = process_data.date_status(source.issue_date, settings.valid_from,
                                      settings.valid_to)
    if status == process_data.DATE_MISSING:
        return "Không tìm thấy ngày hoàn thành chứng chỉ"
    return f"Ngày không hợp lệ"
   


def _vn_date(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{d}/{m}/{y}"


def _llms_agree(t1: ExtractedInfo, t2: ExtractedInfo) -> bool:
    """True khi LLM1 và LLM2 trích ra cùng tên VÀ cùng khóa học (so chặt)."""
    return (
        compare.identical_after_normalize(t1.recipient_name, t2.recipient_name)
        and compare.identical_after_normalize(t1.certificate_name, t2.certificate_name)
    )


# Tên trên ảnh KHÔNG nối được với nhân viên nào -> không kết luận được.
#
# Trả về đuôi email ngoài công ty (để ghi vào lý do), hoặc None nếu vẫn kết
# luận được như bình thường.
#
# THỨ TỰ QUAN TRỌNG: chỉ hỏi tới email SAU KHI so tên/mã đã trượt. Chứng chỉ
# in cả tên lẫn email cá nhân ("NGUYEN THUY LINH abc@gmail.com") mà tên khớp
# eLIS thì danh tính ĐÃ xác minh xong bằng tên — bỏ qua nó là bỏ phí một ca
# vốn kết luận được.
def _unverifiable_identity(extracted: ExtractedInfo, given: InputInfo) -> str | None:
    if compare.match_name_or_code(extracted.recipient_name,
                                  given.employee_name, given.employee_code):
        return None

    if not compare.match_course_bilingual(
            extracted.certificate_name, extracted.certificate_name_alt,
            given.course_name, settings.course_match_mode):
        return None
    if not _date_in_range(extracted):
        return None

    domain = compare.external_email(extracted.recipient_name)
    if domain:
        return ("Không xác minh được danh tính: chứng chỉ ghi email ngoài công "
                f"ty (@{domain})")

    if compare.name_missing_words(extracted.recipient_name, given.employee_name):
        return ("Không xác minh được danh tính: tên trên chứng chỉ thiếu họ "
                f"hoặc tên đệm (\"{extracted.recipient_name}\" so với "
                f"\"{given.employee_name}\")")

    return None


def _field_matches(extracted: ExtractedInfo, given: InputInfo) -> dict[str, bool]:
    """Từng trường của MỘT bản đọc có khớp dữ liệu eLIS không."""
    return {
        "Tên": compare.match_name_or_code(
            extracted.recipient_name, given.employee_name, given.employee_code),
        "Tên khóa học": compare.match_course_bilingual(
            extracted.certificate_name, extracted.certificate_name_alt,
            given.course_name, settings.course_match_mode),
        "Ngày": _date_in_range(extracted),
    }


# Tên trên ảnh trượt vì THIẾU họ/tên đệm hoặc là EMAIL CÁ NHÂN — cùng hai dấu
# hiệu _unverifiable_identity dùng. Đây là "chưa xác minh được", không phải
# "sai tên", nên KHÔNG nêu "Tên không khớp" trong comment gửi người học:
# ca "Hieu Tran" / "Trần Trung Hiếu" là đúng người, chỉ thiếu tên đệm.
def _name_unverifiable(extracted: ExtractedInfo, given: InputInfo) -> bool:
    name = extracted.recipient_name
    return bool(compare.external_email(name)
                or compare.name_missing_words(name, given.employee_name))


def _name_reason(extracted: ExtractedInfo, other: ExtractedInfo | None = None) -> str:
    """Câu lý do cho trường TÊN.

    CẢ HAI bản đọc đều không ra tên -> "không tìm thấy tên" (thường là ảnh chụp
    trang khóa học, chưa phải chứng chỉ). Chỉ cần một máy đọc ra tên thì tên đó
    là tên khác -> "Tên không khớp".
    """
    reads = [extracted] + ([other] if other is not None else [])
    if all(not (r.recipient_name or "").strip() for r in reads):
        return "Không tìm thấy tên người học trên chứng chỉ"
    return "Tên không khớp"


def _mismatch_reason(extracted: ExtractedInfo, given: InputInfo,
                     other: ExtractedInfo | None = None) -> str:
    """Lý do từ chối: nêu trường nào không khớp (tên / khóa học / thời gian).
    Không đổi phán quyết: APPROVED/REJECTED do _both_fields_match quyết, và
    nó không gọi tới đây.

    Tên thiếu họ/tên đệm hoặc là email cá nhân thì KHÔNG nêu lỗi tên — chỉ
    nêu các trường còn lại (khóa học, ngày) là lý do từ chối thật.
    """
    primary = _field_matches(extracted, given)
    backup = _field_matches(other, given) if other is not None else None

    errors = []
    for field, matched in primary.items():
        if matched:
            continue
        if backup is not None and backup[field]:
            continue        # bản kia đọc khớp, chưa chắc sai
        if field == "Tên" and (_name_unverifiable(extracted, given) or (
                other is not None and _name_unverifiable(other, given))):
            continue        # chưa xác minh được, không phải sai tên
        if field == "Tên":
            errors.append(_name_reason(extracted, other))
        elif field == "Ngày":
            errors.append(_date_reason(extracted, other))
        else:
            errors.append(f"{field} không khớp")
    return "; ".join(errors) if errors else "Không khớp"


# Tên + khóa học đã khớp ở LLM1, chỉ NGÀY trượt -> đọc lại ngày bằng OCR
# + LLM2 trước khi từ chối.
#
# VÌ SAO: ngày in chữ nhỏ (bảng xác thực, ảnh chụp màn hình) và Gemma từng đọc
# "Sept. 29, 2026" thành "Sep. 29, 2020" -> chứng chỉ hợp lệ bị từ chối oan.
# Tên và khóa đã có LLM1 xác nhận; ở đây CHỈ hỏi LLM2 về ngày, không so lại
# tên/khóa của LLM2 (LLM2 đọc tên kém hơn thì đã có LLM1 đứng ra).
#
# Tầng 2 hỏng -> stage2_error (hỏng kỹ thuật, WAITING, thử lại sau), không
# từ chối dựa trên một bản đọc ngày duy nhất.
def _recheck_date(llm1, given, images, ocr_images, extract_from_text,
                  ocr_client, verdict) -> ProcessResult:
    logger.info("LLM1 khớp tên + khóa nhưng ngày %r ngoài khoảng -> đọc lại "
                "ngày bằng OCR + LLM2.", llm1.issue_date)
    try:
        llm2 = extract_from_text(ocr_images(ocr_client, images))
    except Exception as e:
        logger.warning("Tầng 2 lỗi khi đọc lại ngày: %s", e)
        return verdict(Verdict.REJECTED, f"Tầng 2 lỗi (đọc lại ngày): {e}",
                       "stage2_error", llm1)

    # Log ghi tên/khóa của LLM1 (bản đã khớp) kèm ngày của LLM2 (bản quyết định).
    merged = llm1.model_copy(update={"issue_date": llm2.issue_date})
    if _date_in_range(llm2):
        return verdict(Verdict.APPROVED,
                       "Tên, khóa học khớp (LLM1); thời gian khớp khi OCR đọc "
                       f"lại (LLM1 đọc ngày sai: {llm1.issue_date!r})",
                       "llm2", merged)
    return verdict(Verdict.REJECTED, _date_reason(llm2, llm1), "llm2", merged)


def process(
    images: list[bytes],
    given: InputInfo,
    extract_from_image,   # hàm llm_vision.extract_from_image
    ocr_images,  # hàm ocr.ocr_images
    extract_from_text,  # hàm llm_text.extract_from_text
    ocr_client,
    detect_course_list=None,  # hàm llm_vision.detect_course_list; None = tắt
) -> ProcessResult:
    """Xử lý một chứng chỉ, trả về ProcessResult (APPROVED / REJECTED).

    Các hàm gọi API được truyền vào (dependency injection) để test được mà
    không cần gọi API thật, và để pipeline không phụ thuộc cứng vào module nào.
    """
    def verdict(result, reason, stage, extracted=None):
        return ProcessResult(
            employee_code=given.employee_code,
            verdict=result,
            extracted=extracted,
            reason=reason,
            stage=stage,
        )

    # ===== Bước lọc: ảnh danh sách nhiều khóa học -> BỎ QUA =====
    skip = _course_list_reason(images, detect_course_list)
    if skip:
        return verdict(Verdict.WAITING, skip, SKIP_MULTI_COURSE_STAGE)

    # ===== Tầng 1: Gemma đọc ảnh =====
    # Chứng chỉ có thể nhiều trang (PDF); dùng trang đầu để đọc, vì thông tin
    # chính (tên, khóa học) thường nằm ở trang đầu. Nếu cần đọc mọi trang thì
    # mở rộng sau.
    try:
        llm1 = extract_from_image(images[0])
    except Exception as e:
        logger.warning("LLM1 lỗi: %s", e)
        return verdict(Verdict.REJECTED, f"LLM1 lỗi: {e}", "llm1_error")

    if _both_fields_match(llm1, given):
        if _date_in_range(llm1):
            return verdict(Verdict.APPROVED, "Tên, khóa học và thời gian đều khớp (LLM1)", "llm1", llm1)
        return _recheck_date(llm1, given, images, ocr_images, extract_from_text,
                             ocr_client, verdict)

    # ===== Tầng 2: Azure OCR + LLM2 =====
    logger.info(
        "LLM1 không khớp -> kiểm tra lại bằng OCR + LLM2. "
        "LLM1 đọc: tên=%r khóa=%r ngày=%r | eLIS gửi: tên=%r khóa=%r",
        llm1.recipient_name, llm1.certificate_name, llm1.issue_date,
        given.employee_name, given.course_name)

    try:
        ocr_text = ocr_images(ocr_client, images)
        llm2 = extract_from_text(ocr_text)
    except Exception as e:
        logger.warning("Tầng 2 lỗi: %s", e)
        # Tầng 2 hỏng (ảnh mờ Azure không đọc được...): không khẳng định được,
        # theo luồng 2 nhãn thì về REJECTED để người kiểm tra khi cần.
        return verdict(Verdict.REJECTED, f"Tầng 2 lỗi: {e}", "stage2_error", llm1)

    # ===== So LLM1 với LLM2 (chặt) =====
    if _llms_agree(llm1, llm2):
        # Hai máy đọc ra GIỐNG nhau -> tin tưởng kết quả đó, và nó khác input.
        skip = _unverifiable_identity(llm2, given)
        if skip:
            return verdict(Verdict.WAITING, skip, SKIP_STAGE, llm2)
        return verdict(Verdict.REJECTED, _mismatch_reason(llm2, given, llm1),
                       "llm1_vs_llm2", llm2)

    # ===== So LLM2 với input =====
    if _both_fields_match(llm2, given):
        if not _date_in_range(llm2):
            return verdict(Verdict.REJECTED,
                           _mismatch_reason(llm2, given, llm1), "llm2", llm2)
        return verdict(Verdict.APPROVED,
                       "Khớp ở LLM2 (OCR đọc lại, LLM1 đọc sai)", "llm2", llm2)

    skip = _unverifiable_identity(llm2, given)
    if skip:
        return verdict(Verdict.WAITING, skip, SKIP_STAGE, llm2)
    return verdict(Verdict.REJECTED, _mismatch_reason(llm2, given, llm1), "llm2", llm2)
