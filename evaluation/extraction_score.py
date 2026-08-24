"""PHẦN 1 — Đo bước TRÍCH XUẤT (evaluation.extraction_score).

Câu hỏi cần trả lời: model đọc ảnh ra đúng bao nhiêu phần trăm, theo TỪNG
TRƯỜNG.

Vì sao không gộp thành một con số duy nhất: "đúng 80%" không cho biết phải
sửa gì. Tách theo trường thì thấy ngay "tên đọc tốt, ngày đọc tệ" và biết
chỗ cần sửa prompt.

Và trong mỗi trường lại tách tiếp bốn kiểu sai, vì cách chữa khác hẳn nhau:

  CORRECT    : khớp nhãn chuẩn.
  WRONG     : ảnh có, model đọc ra, nhưng đọc sai      -> lỗi đọc (ảnh mờ / OCR).
  MISSED  : ảnh có, model trả None                    -> model quá dè dặt.
  HALLUCINATED     : ảnh KHÔNG có, model vẫn trả về giá trị    -> model bịa (nguy hiểm
            nhất: dữ liệu sai trông như dữ liệu thật).

Gộp ba loại sai làm một thì "sửa prompt cho model bớt dè dặt" và "sửa prompt
cho model bớt bịa" — hai việc NGƯỢC NHAU — trông giống hệt nhau trên báo cáo.

HAI MỨC SO KHỚP, báo cáo cả hai:
  - normalize: dùng đúng luật so của production (compare.py). Đây là con số
    có ý nghĩa nghiệp vụ, vì nó đo cái thật sự ảnh hưởng tới quyết định duyệt.
  - tuyet_doi: so chuỗi y hệt từng ký tự. Chặt hơn thực tế cần, nhưng cho thấy
    model đọc sạch tới đâu.
Chỉ báo cáo mức chuẩn hóa dễ gây ảo tưởng; chỉ báo cáo mức tuyệt đối thì con
số xấu hơn thực tế mà không ai hành động được.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import compare
import process_data

from .dataset import EvalCase

# Các trường được chấm điểm, kèm cách so tương ứng.
#   "tu"   : so theo TẬP HỢP TỪ — giống luật production, chịu được đảo thứ tự
#            tên ("A Nguyen Van" = "Nguyen Van A").
#   "ngay" : so theo GIÁ TRỊ NGÀY sau khi parse — "10 July 2026" = "10/07/2026".
#            So chuỗi thô ở đây sẽ báo sai oan cho mọi chứng chỉ nước ngoài.
SCORED_FIELDS = {
    "recipient_name":     "tu",
    "certificate_name":      "tu",
    "certificate_name_alt":  "tu",
    "issue_date":          "day",
    "expiry_date":       "day",
}

CORRECT, WRONG, MISSED, HALLUCINATED = "CORRECT", "WRONG", "MISSED", "HALLUCINATED"


def _same_date(a: str | None, b: str | None) -> bool:
    """Hai chuỗi ngày có chỉ cùng một ngày không.

    Ngày dạng số MƠ HỒ: "06-12-2026" có thể là 6/12 (kiểu VN) hoặc 12/6 (kiểu
    Mỹ). Theo đúng luật đã chốt ở process_data.date_in_range, ta thử CẢ HAI cách
    hiểu cho mỗi bên; chỉ cần tồn tại một cách hiểu chung là tính khớp.

    Không parse được cả hai bên -> lùi về so chuỗi đã chuẩn hóa, để những định
    dạng lạ (vd "2026 年 02月 24日") vẫn chấm được thay vì bị bỏ trắng.
    """
    days_a = {process_data._parse_with_order(a, t) for t in ("DMY", "MDY")} if a else set()
    days_b = {process_data._parse_with_order(b, t) for t in ("DMY", "MDY")} if b else set()
    days_a.discard(None)
    days_b.discard(None)
    if days_a and days_b:
        return bool(days_a & days_b)
    return process_data.normalize(a) == process_data.normalize(b) != ""


def _matches(kind: str, predicted: str | None, expected: str | None, chat: bool) -> bool:
    """So một giá trị model đọc được với nhãn chuẩn."""
    if chat:
        # Mức tuyệt đối: y hệt từng ký tự, chỉ bỏ khoảng trắng thừa hai đầu.
        return (predicted or "").strip() == (expected or "").strip()
    if kind == "day":
        return _same_date(predicted, expected)
    return compare.same_word_set(predicted, expected)


def _classify(kind: str, predicted: str | None, expected: str | None,
               chat: bool) -> str:
    """Xếp một trường vào CORRECT / WRONG / MISSED / HALLUCINATED."""
    has_prediction = bool(predicted and str(predicted).strip())
    has_expected = expected is not None

    if not has_expected:
        # Nhãn chuẩn nói ảnh KHÔNG có trường này.
        return HALLUCINATED if has_prediction else CORRECT
    if not has_prediction:
        return MISSED
    return CORRECT if _matches(kind, predicted, expected, chat) else WRONG


@dataclass
class FieldScore:
    """Điểm của MỘT trường trên toàn bộ bộ dữ liệu."""

    field_name: str
    total: int = 0            # số ca ĐÃ gán nhãn cho trường này
    correct: int = 0
    wrong: int = 0
    missed: int = 0
    hallucinated: int = 0
    exact_correct: int = 0
    # (case_id, expected, predicted, loai) — để soi lại từng ca hỏng.
    failed_cases: list[tuple] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def exact_accuracy(self) -> float:
        return self.exact_correct / self.total if self.total else 0.0


def score(cases: list[EvalCase], predicted: dict) -> dict[str, FieldScore]:
    """Chấm điểm trích xuất.

    cases  : danh sách ca kèm nhãn chuẩn.
    predicted : {case_id: ExtractedInfo | None} — model đọc được gì.
              None nghĩa là ca đó không chạy được (lỗi tải file, LLM chết...).
              Ca như vậy bị LOẠI khỏi phép đo trích xuất, không tính là sai —
              chúng đo độ ổn định hạ tầng, không đo chất lượng model. Nhưng số
              lượng vẫn được báo riêng để không ai quên chúng tồn tại.
    """
    scores = {t: FieldScore(t) for t in SCORED_FIELDS}

    for case in cases:
        extracted = predicted.get(case.case_id)
        if extracted is None:
            continue

        for field_name, kind in SCORED_FIELDS.items():
            has_label, expected = case.field_label(field_name)
            if not has_label:
                continue                     # chưa gán nhãn -> bỏ qua

            value = getattr(extracted, field_name, None)
            d = scores[field_name]
            d.total += 1

            kind = _classify(kind, value, expected, chat=False)
            if kind == CORRECT:
                d.correct += 1
            elif kind == WRONG:
                d.wrong += 1
            elif kind == MISSED:
                d.missed += 1
            else:
                d.hallucinated += 1
            if kind != CORRECT:
                d.failed_cases.append((case.case_id, expected, value, kind))

            if _classify(kind, value, expected, chat=True) == CORRECT:
                d.exact_correct += 1

    return scores


def failed_case_ids(cases: list[EvalCase], predicted: dict) -> list[str]:
    """Danh sách case_id không trích xuất được (để báo cáo riêng)."""
    return [case.case_id for case in cases if predicted.get(case.case_id) is None]
