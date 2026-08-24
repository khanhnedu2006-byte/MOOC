"""Chạy đánh giá (evaluation.run_eval).

    python -m evaluation.run_eval template                  # tạo file nhãn rỗng từ data/
    python -m evaluation.run_eval from-archive               # tạo file nhãn từ kho chứng chỉ THẬT
    python -m evaluation.run_eval run                 # chạy pipeline thật + chấm điểm
    python -m evaluation.run_eval run --file X.csv    # dùng file nhãn khác

Chạy end-to-end: mỗi lần gọi "chay" là một lần gọi LLM thật cho toàn bộ ca
trong file nhãn. Cần mạng công ty và tốn phí — con số in ra ở đầu để bạn biết
trước quy mô.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

import file_utils                                   # noqa: E402
import archive                                      # noqa: E402
import llm_text                                     # noqa: E402
import llm_vision                                   # noqa: E402
import ocr_azure                                    # noqa: E402
import pipeline                                     # noqa: E402
from config import settings                         # noqa: E402
from schemas import InputInfo                    # noqa: E402

from evaluation import dataset, decision_score, extraction_score   # noqa: E402

logging.basicConfig(level=logging.WARNING,
                    format="%(levelname)s: %(message)s")
logger = logging.getLogger("danh_gia")

DEFAULT_DATASET_FILE = PROJECT_ROOT / "danh_gia" / "bo_nhan.csv"


# ================= sinh file nhãn từ kho chứng chỉ thật =================

def _code_from_email(email: str | None) -> str:
    """Lấy phần trước @ làm mã đối chiếu — giống hệt run.py._code_from_email()."""
    if not email or "@" not in email:
        return (email or "").strip()
    return email.split("@", 1)[0].strip()


def create_from_archive(archive_root, output_path) -> int:
    """Sinh file nhãn từ kho chứng chỉ thật, điền sẵn phần ĐẦU VÀO.

    Điền sẵn: image_path + ba cột input_* (lấy từ getCert đã lưu kèm).
    Để TRỐNG: toàn bộ cột gt_* — kể cả gt_verdict, dù trong kho có sẵn kết
    luận của hệ thống.

    Vì sao cố ý không điền gt_verdict: đó là câu trả lời của chính hệ thống
    đang cần đo. Lấy nó làm đáp án chuẩn thì hệ thống luôn đúng 100% và bộ
    đánh giá không còn đo được gì. Nhãn chuẩn phải do người nhìn ảnh mà gán.
    """
    metas = archive.read_archive(archive_root)
    if not metas:
        raise FileNotFoundError(
            f"Kho rỗng hoặc chưa có: {archive_root}\n"
            f"Bật SAVE_CERTIFICATES=1 trong .env rồi chạy run.py để thu thập."
        )

    cases = [
        dataset.EvalCase(
            case_id=m.get("case_id") or m["_duong_dan_anh"].stem,
            image_path=str(
                m["_duong_dan_anh"].relative_to(PROJECT_ROOT)
                if m["_duong_dan_anh"].is_relative_to(PROJECT_ROOT)
                else m["_duong_dan_anh"]
            ).replace("\\", "/"),
            input_employee_name=m.get("employeeName") or "",
            input_course_name=m.get("courseName") or "",
            input_employee_code=_code_from_email(m.get("employeeEmail")),
        )
        for m in metas
    ]
    dataset.write_dataset(cases, output_path)
    return len(cases)


# ===================== chạy pipeline trên bộ dữ liệu =====================

def run_pipeline(cases, azure_client) -> dict:
    """Chạy pipeline thật cho từng ca. Trả về {case_id: ProcessResult | None}."""
    results = {}
    for i, case in enumerate(cases, start=1):
        path = PROJECT_ROOT / case.image_path
        print(f"  [{i}/{len(cases)}] {case.case_id} ... ", end="", flush=True)

        if not path.is_file():
            print("KHÔNG THẤY FILE")
            results[case.case_id] = None
            continue

        try:
            images = file_utils.read_as_images(path)
            kq = pipeline.process(
                images=images,
                given=InputInfo(
                    employee_name=case.input_employee_name,
                    course_name=case.input_course_name,
                    employee_code=case.input_employee_code,
                ),
                extract_from_image=llm_vision.extract_from_image,
                ocr_images=ocr_azure.ocr_images,
                extract_from_text=llm_text.extract_from_text,
                azure_client=azure_client,
            )
            results[case.case_id] = kq
            print(f"{kq.verdict.value} ({kq.stage})")
        except Exception as e:
            # Một ca hỏng KHÔNG được làm chết cả lượt đánh giá — chạy lại từ
            # đầu nghĩa là trả tiền LLM lại cho những ca đã xong.
            logger.warning("Ca %s lỗi: %s", case.case_id, e)
            results[case.case_id] = None
            print(f"LỖI: {e}")
    return results


# ============================== in báo cáo ==============================

def _pct(x: float) -> str:
    return f"{x * 100:5.1f}%"


def print_extraction_report(scores, max_failed_shown=10) -> None:
    print("\n" + "=" * 78)
    print("PHẦN 1 — TRÍCH XUẤT (độ chính xác theo trường)")
    print("=" * 78)
    print(f"{'Trường':<20}{'n':>4}{'Đúng':>8}{'Tuyệt đối':>11}"
          f"{'Sai':>6}{'Bỏ sót':>8}{'Bịa':>6}")
    print("-" * 78)
    for name, d in scores.items():
        if d.total == 0:
            print(f"{name:<20}{0:>4}{'  (chưa gán nhãn)':>33}")
            continue
        print(f"{name:<20}{d.total:>4}{_pct(d.accuracy):>8}"
              f"{_pct(d.exact_accuracy):>11}"
              f"{d.wrong:>6}{d.missed:>8}{d.hallucinated:>6}")
    print("-" * 78)
    print("Đúng      = so theo luật của production (bỏ dấu, không phân biệt thứ tự từ,")
    print("            ngày so theo giá trị nên '10 July 2026' = '10/07/2026').")
    print("Tuyệt đối = so chuỗi y hệt từng ký tự.")
    print("Bịa       = ảnh KHÔNG có trường đó mà model vẫn trả về giá trị.")

    with_failures = [(t, d) for t, d in scores.items() if d.failed_cases]
    if with_failures:
        print("\nCa sai cụ thể:")
        for name, d in with_failures:
            print(f"\n  [{name}]")
            for case_id, expected, predicted, kind in d.failed_cases[:max_failed_shown]:
                print(f"    {kind:<7} {case_id}")
                print(f"            đúng : {expected!r}")
                print(f"            model: {predicted!r}")
            if len(d.failed_cases) > max_failed_shown:
                print(f"    ... còn {len(d.failed_cases) - max_failed_shown} ca nữa")


def print_decision_report(kq) -> None:
    print("\n" + "=" * 78)
    print("PHẦN 2 — PHÊ DUYỆT (precision / recall / F1)")
    print("=" * 78)

    if kq.total == 0:
        print("Không có ca nào chấm được. Kiểm tra cột gt_verdict trong file nhãn.")
        return

    print(f"Số ca chấm được: {kq.total}     Accuracy: {_pct(kq.accuracy)}"
          f"     Macro-F1: {_pct(kq.macro_f1)}")

    print("\nMa trận nhầm lẫn (hàng = thực tế, cột = model đoán):")
    print(f"{'':>22}{'APPROVED':>12}{'REJECTED':>12}")
    for actual in decision_score.LABELS:
        row = "".join(f"{kq.matrix[(actual, dd)]:>12}" for dd in decision_score.LABELS)
        print(f"  thực tế {actual:<12}{row}")

    print("\nTheo từng nhãn (coi nhãn đó là positive):")
    print(f"{'Nhãn':<12}{'Số ca':>7}{'Precision':>11}{'Recall':>9}{'F1':>8}")
    print("-" * 47)
    for label in decision_score.LABELS:
        d = kq.by_label[label]
        print(f"{label:<12}{d.support:>7}{_pct(d.precision):>11}"
              f"{_pct(d.recall):>9}{_pct(d.f1):>8}")
    print("-" * 47)
    print("REJECTED recall thấp    = chứng chỉ sai LỌT QUA, bị duyệt oan.")
    print("REJECTED precision thấp = từ chối OAN người làm thật.")

    if kq.by_stage:
        print("\nTheo tầng xử lý (tầng nào quyết định, và quyết định có đúng không):")
        print(f"{'Tầng':<16}{'Số ca':>7}{'Đúng':>7}{'Tỷ lệ':>9}")
        print("-" * 39)
        for stage, (correct, total) in sorted(kq.by_stage.items()):
            print(f"{stage:<16}{total:>7}{correct:>7}{_pct(correct / total):>9}")
        print("-" * 39)
        print("Tầng llm2 / llm1_vs_llm2 là những ca phải gọi Azure OCR (tốn tiền).")
        print("Nếu tỷ lệ đúng ở đó không cao hơn llm1 thì tầng 2 chưa đáng giá tiền.")

    if kq.wrong_cases:
        print(f"\nCa đoán sai ({len(kq.wrong_cases)}):")
        for case_id, actual, dd, reason, stage in kq.wrong_cases:
            print(f"  {case_id:<24} đúng={actual:<9} model={dd:<9} [{stage}]")
            print(f"  {'':<24} lý do model: {reason}")

    if kq.technical_error_cases:
        print(f"\nLoại khỏi phép đo — hỏng kỹ thuật ({len(kq.technical_error_cases)} ca):")
        for case_id, stage, reason in kq.technical_error_cases:
            print(f"  {case_id:<24} [{stage}] {reason}")
        print("  (Đây là lỗi hạ tầng, không phải model đoán sai — nên không")
        print("   tính vào precision/recall. Nhưng nhiều quá thì số đo mất ý nghĩa")
        print("   vì phần lớn bộ dữ liệu đã bị loại.)")

    if kq.unlabeled_cases:
        print(f"\nChưa gán gt_verdict ({len(kq.unlabeled_cases)} ca): "
              f"{', '.join(kq.unlabeled_cases[:10])}")


# ================================= CLI =================================

def main() -> int:
    p = argparse.ArgumentParser(description="Đánh giá trích xuất + phê duyệt")
    p.add_argument("command",
                   choices=["template", "from-archive", "run"])
    p.add_argument("--file", default=str(DEFAULT_DATASET_FILE),
                   help="file nhãn CSV")
    p.add_argument("--images", default=str(PROJECT_ROOT / "data"),
                   help="thư mục ảnh (chỉ dùng cho lệnh 'template')")
    p.add_argument("--archive", default=None,
                   help="thư mục kho (chỉ dùng cho lệnh 'from-archive')")
    args = p.parse_args()

    if args.command == "from-archive":
        archive = args.archive or str(PROJECT_ROOT / settings.archive_dir)
        n = create_from_archive(archive, args.file)
        print(f"Đã tạo {args.file} với {n} ca từ kho {archive}.")
        print("\nĐã điền sẵn: image_path, input_employee_name,")
        print("             input_course_name, input_employee_code")
        print("Cần điền tay: các cột gt_* (thông tin THẬT trên ảnh)")
        print("              và gt_verdict (kết luận ĐÚNG phải ra)")
        print("\nLưu ý: gt_verdict CỐ Ý để trống dù trong kho có sẵn kết luận")
        print("của hệ thống. Lấy đáp án của model làm đáp án chuẩn thì model")
        print("luôn đúng 100% và bộ đánh giá không đo được gì.")
        return 0

    if args.command == "template":
        n = dataset.create_template(args.images, args.file)
        print(f"Đã tạo {args.file} với {n} ca.")
        print("\nCần điền tay các cột:")
        print("  input_* : dữ liệu eLIS gửi sang (tên NV, tên khóa học, mã NV)")
        print("  gt_*    : thông tin THẬT trên ảnh")
        print("  gt_verdict : APPROVED hoặc REJECTED — kết luận ĐÚNG phải ra")
        print(f"\nĐể trống  = chưa gán nhãn, bỏ qua khi tính điểm.")
        print(f"Ghi '{dataset.NOT_PRESENT}'      = ảnh không in trường đó "
              f"(kỳ vọng model trả về rỗng).")
        return 0

    cases = dataset.read_dataset(args.file)
    print(f"Bộ dữ liệu: {len(cases)} ca từ {args.file}")
    print(f"Sắp gọi LLM thật cho {len(cases)} ca. Ctrl+C để hủy.\n")

    azure_client = ocr_azure.create_client()
    results = run_pipeline(cases, azure_client)

    predicted_extractions = {
        code: (kq.extracted if kq is not None else None)
        for code, kq in results.items()
    }
    print_extraction_report(extraction_score.score(cases, predicted_extractions))

    not_run = extraction_score.failed_case_ids(cases, results)
    if not_run:
        print(f"\nKhông trích xuất được ({len(not_run)} ca): "
              f"{', '.join(not_run)}")

    print_decision_report(decision_score.score(cases, results))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
