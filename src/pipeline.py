"""Điều phối xử lý MỘT chứng chỉ (pipeline).

Nối các module theo đúng sơ đồ ba lần so:

  1. Gemma (LLM1) đọc ảnh -> so tên + khóa học với input người nhập.
     Cả hai khớp -> APPROVED (dừng, không tốn Azure).

  2. Không khớp -> Azure OCR + LLM2 đọc lại từ ảnh.

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


def _llms_agree(t1: ExtractedInfo, t2: ExtractedInfo) -> bool:
    """True khi LLM1 và LLM2 trích ra cùng tên VÀ cùng khóa học (so chặt)."""
    return (
        compare.identical_after_normalize(t1.recipient_name, t2.recipient_name)
        and compare.identical_after_normalize(t1.certificate_name, t2.certificate_name)
    )


def _mismatch_reason(extracted: ExtractedInfo, given: InputInfo, stage: str) -> str:
    """Tạo lý do gọn: chỉ nêu trường nào không khớp (tên / khóa học / thời gian).

    Liệt kê MỌI trường sai, nhưng không kèm giá trị hay tầng xử lý.
    """
    name_matches = compare.match_name_or_code(
        extracted.recipient_name, given.employee_name, given.employee_code
    )
    course_matches = compare.match_course_bilingual(
        extracted.certificate_name, extracted.certificate_name_alt, given.course_name,
        settings.course_match_mode,
    )
    date_valid = _date_in_range(extracted)

    errors = []
    if not name_matches:
        errors.append("Tên không khớp")
    if not course_matches:
        errors.append("Tên khóa học không khớp")
    if not date_valid:
        errors.append("Ngày không hợp lệ")
    return "; ".join(errors) if errors else "Không khớp"


def process(
    images: list[bytes],
    given: InputInfo,
    extract_from_image,   # hàm llm_vision.extract_from_image
    ocr_images,  # hàm ocr_azure.ocr_images
    extract_from_text,  # hàm llm_text.extract_from_text
    azure_client,
) -> ProcessResult:
    """Xử lý một chứng chỉ, trả về ProcessResult (APPROVED / REJECTED).

    Các hàm gọi API được truyền vào (dependency injection) để test được mà
    không cần gọi API thật, và để pipeline không phụ thuộc cứng vào module nào.
    """
    def verdict(kq, reason, stage, extracted=None):
        return ProcessResult(
            employee_code=given.employee_code,
            verdict=kq,
            extracted=extracted,
            reason=reason,
            stage=stage,
        )

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
        if not _date_in_range(llm1):
            return verdict(Verdict.REJECTED,
                           _mismatch_reason(llm1, given, "LLM1"), "llm1", llm1)
        return verdict(Verdict.APPROVED, "Tên, khóa học và thời gian đều khớp (LLM1)", "llm1", llm1)

    # ===== Tầng 2: Azure OCR + LLM2 =====
    try:
        ocr_text = ocr_images(azure_client, images)
        llm2 = extract_from_text(ocr_text)
    except Exception as e:
        logger.warning("Tầng 2 lỗi: %s", e)
        # Tầng 2 hỏng (ảnh mờ Azure không đọc được...): không khẳng định được,
        # theo luồng 2 nhãn thì về REJECTED để người kiểm tra khi cần.
        return verdict(Verdict.REJECTED, f"Tầng 2 lỗi: {e}", "stage2_error", llm1)

    # ===== So LLM1 với LLM2 (chặt) =====
    if _llms_agree(llm1, llm2):
        # Hai máy đọc ra GIỐNG nhau -> tin tưởng kết quả đó, và nó khác input.
        return verdict(Verdict.REJECTED, _mismatch_reason(llm2, given, "đồng thuận"),
                       "llm1_vs_llm2", llm2)

    # ===== So LLM2 với input =====
    if _both_fields_match(llm2, given):
        if not _date_in_range(llm2):
            return verdict(Verdict.REJECTED,
                           _mismatch_reason(llm2, given, "LLM2"), "llm2", llm2)
        return verdict(Verdict.APPROVED,
                       "Khớp ở LLM2 (Azure đọc lại, LLM1 đọc sai)", "llm2", llm2)

    return verdict(Verdict.REJECTED, _mismatch_reason(llm2, given, "LLM2"), "llm2", llm2)