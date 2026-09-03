"""PHẦN 2 — Đo bước PHÊ DUYỆT (evaluation.decision_score).

Bài toán hai nhãn: APPROVED / REJECTED. Tính precision, recall, F1.

BÁO CÁO CHO CẢ HAI NHÃN, không chọn sẵn một nhãn làm "positive". Lý do: hai
nhãn trả lời hai câu hỏi nghiệp vụ khác nhau và cả hai đều quan trọng.

  Coi REJECTED là positive:
    - recall    = trong số chứng chỉ THẬT SỰ không hợp lệ, ta bắt được bao nhiêu?
                  Bỏ sót = chứng chỉ sai lọt qua, được duyệt oan.
    - precision = trong số ta từ chối, bao nhiêu cái đáng bị từ chối?
                  Thấp = từ chối oan người làm thật.

  Coi APPROVED là positive: hai câu hỏi trên đảo vai.

Trong hệ thống này lỗi ĐẮT hơn là DUYỆT OAN (chứng chỉ sai được công nhận),
nên khi phải chọn một con số để tối ưu, thường là recall của REJECTED. Nhưng
đó là quyết định nghiệp vụ, không phải quyết định kỹ thuật — nên module này
đưa ra đủ số liệu, không tự chọn thay.

LOẠI CA LỖI KỸ THUẬT RA KHỎI PHÉP ĐO — quan trọng:
pipeline trả REJECTED cho cả những ca không đọc được file, LLM chết, Azure
timeout (stage = llm1_error / stage2_error / file_error / ...). Những cái đó
KHÔNG phải phán đoán của model. Để lẫn vào thì một hôm mạng công ty chập là
recall của REJECTED tăng vọt, báo cáo trông đẹp lên trong khi model không hề
tốt hơn. Chúng được đếm và báo cáo RIÊNG.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Các tầng nghĩa là "hỏng kỹ thuật", không phải kết luận nghiệp vụ.
TECHNICAL_ERROR_STAGES = {
    "llm1_error", "stage2_error", "system_error",
    "file_error", "download_error", "no_file",
}

LABELS = ("APPROVED", "REJECTED")


@dataclass
class LabelScore:
    """Precision / recall / F1 khi coi MỘT nhãn là positive."""

    label: str
    tp: int = 0      # đoán đúng nhãn này
    fp: int = 0      # đoán nhãn này nhưng thực tế là nhãn kia
    fn: int = 0      # thực tế là nhãn này nhưng đoán ra nhãn kia

    @property
    def precision(self) -> float:
        color = self.tp + self.fp
        return self.tp / color if color else 0.0

    @property
    def recall(self) -> float:
        color = self.tp + self.fn
        return self.tp / color if color else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def support(self) -> int:
        """Số ca THỰC TẾ mang nhãn này (support)."""
        return self.tp + self.fn


@dataclass
class DecisionScore:
    """Toàn bộ số đo của bước phê duyệt."""

    by_label: dict[str, LabelScore]
    matrix: dict[tuple[str, str], int]      # (actual, predicted) -> số ca
    correct_count: int = 0
    total: int = 0
    # (case_id, actual, predicted, reason, stage)
    wrong_cases: list[tuple] = field(default_factory=list)
    # (case_id, stage, reason)
    technical_error_cases: list[tuple] = field(default_factory=list)
    unlabeled_cases: list[str] = field(default_factory=list)
    by_stage: dict[str, list[int]] = field(default_factory=dict)  # tang -> [dung, tong]

    @property
    def accuracy(self) -> float:
        return self.correct_count / self.total if self.total else 0.0

    @property
    def macro_f1(self) -> float:
        """Trung bình F1 của hai nhãn, KHÔNG trọng số theo số ca.

        Dùng macro chứ không phải micro vì bộ dữ liệu thường lệch (nhiều
        REJECTED hơn APPROVED, hoặc ngược lại). Micro sẽ để nhãn đông ca át
        nhãn ít ca, và một model chỉ đoán bừa nhãn đông vẫn ra điểm cao.
        """
        return sum(d.f1 for d in self.by_label.values()) / len(self.by_label)


def score(cases, predicted: dict) -> DecisionScore:
    """Chấm điểm phê duyệt.

    cases  : danh sách EvalCase (đã có gt_verdict).
    predicted : {case_id: ProcessResult | None}. None = không chạy được.
    """
    kq = DecisionScore(
        by_label={n: LabelScore(n) for n in LABELS},
        matrix={(info, dd): 0 for info in LABELS for dd in LABELS},
    )

    for case in cases:
        actual = (case.gt_verdict or "").strip().upper()
        if actual not in LABELS:
            kq.unlabeled_cases.append(case.case_id)
            continue

        results = predicted.get(case.case_id)
        stage = getattr(results, "stage", None) if results is not None else None
        reason = getattr(results, "reason", "") if results is not None else "không chạy được"

        # Ca hỏng kỹ thuật: đếm riêng, KHÔNG đưa vào ma trận nhầm lẫn.
        if results is None or stage in TECHNICAL_ERROR_STAGES:
            kq.technical_error_cases.append((case.case_id, stage or "-", reason))
            continue

        du = results.verdict.value if hasattr(results.verdict, "value") else str(results.verdict)
        if du not in LABELS:
            kq.technical_error_cases.append((case.case_id, stage or "-", f"nhãn lạ: {du}"))
            continue

        kq.total += 1
        kq.matrix[(actual, du)] += 1

        stage_stats = kq.by_stage.setdefault(stage or "-", [0, 0])
        stage_stats[1] += 1

        if du == actual:
            kq.correct_count += 1
            stage_stats[0] += 1
            kq.by_label[actual].tp += 1
        else:
            kq.wrong_cases.append((case.case_id, actual, du, reason, stage or "-"))
            kq.by_label[du].fp += 1        # đoán nhãn này mà sai
            kq.by_label[actual].fn += 1   # nhãn thật bị bỏ sót

    return kq
