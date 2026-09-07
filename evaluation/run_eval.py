"""Chạy đánh giá (evaluation.run_eval).

    python -m evaluation.run_eval check                # KIỂM TRA bộ dữ liệu, KHÔNG gọi LLM
    python -m evaluation.run_eval template                  # tạo file nhãn rỗng từ data/
    python -m evaluation.run_eval from-archive               # tạo file nhãn từ kho chứng chỉ THẬT
    python -m evaluation.run_eval run                 # chạy pipeline thật + chấm điểm
    python -m evaluation.run_eval run --file X.csv    # dùng file nhãn khác
    python -m evaluation.run_eval export-errors        # xuất Excel các ca lệch cho HR
    python -m evaluation.run_eval export-compare       # bảng HUMAN vs AI (CSV 6 cột)

Chạy `check` TRƯỚC `run`: nó trả lời miễn phí hai câu "ảnh có đủ không" và
"đã gán nhãn tới đâu". Không có nó thì cách duy nhất để biết là chạy hết cả
bộ bằng LLM thật rồi đọc lỗi ở cuối — tốn tiền cho một câu hỏi không cần LLM.

Chạy end-to-end: mỗi lần gọi "run_all" là một lần gọi LLM thật cho toàn bộ ca
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
from process_data import code_from_email          # noqa: E402
from schemas import InputInfo                    # noqa: E402

from evaluation import dataset, decision_score, extraction_score   # noqa: E402

logging.basicConfig(level=logging.WARNING,
                    format="%(levelname)s: %(message)s")
logger = logging.getLogger("evaluation")

# Thư mục này ĐÃ ĐỔI TÊN (danh_gia -> evaluation) khi chuyển định danh sang
# tiếng Anh. Đường dẫn cũ còn sót lại làm mọi lệnh dùng mặc định đều lỗi
# FileNotFoundError, mà thông báo lại chỉ nói "không tìm thấy file nhãn"
# nên rất dễ tưởng là mình chưa tạo file.
DEFAULT_DATASET_FILE = PROJECT_ROOT / "evaluation" / "eval_set.csv"

# Kết quả thô của lượt chạy gần nhất. Tồn tại vì một lượt chạy tốn tiền LLM
# thật: nếu chỉ in ra màn hình thì cuộn mất là mất luôn, muốn xem lại phải
# trả tiền chạy lại từ đầu.
DEFAULT_RESULT_FILE = PROJECT_ROOT / "evaluation" / "last_run.csv"


# ================= sinh file nhãn từ kho chứng chỉ thật =================

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
            input_employee_code=code_from_email(m.get("employeeEmail")),
        )
        for m in metas
    ]
    dataset.write_dataset(cases, output_path)
    return len(cases)


def _write_raw_results(cases, results, errors, path=None) -> None:
    """Ghi kết quả từng ca ra CSV để xem lại mà không phải chạy lại.

    Một lượt chạy gọi LLM thật cho từng ca, nên mất kết quả là mất tiền.
    """
    import csv
    path = Path(path or DEFAULT_RESULT_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        f = path.open("w", encoding="utf-8-sig", newline="")
    except PermissionError as e:
        # KHÔNG ném lên: tới đây thì tiền LLM đã tiêu rồi. Chết vì không ghi
        # được file phụ là vứt cả lượt chạy chỉ vì người dùng quên đóng Excel.
        # Báo rõ rồi đi tiếp — báo cáo vẫn in ra màn hình bình thường.
        print(f"\nKHÔNG ghi được {path.name} ({e.strerror}). "
              f"File đang mở trong Excel? Kết quả vẫn in ở dưới.")
        return
    with f:
        w = csv.writer(f)
        # certificate_name_alt PHẢI có: prompt yêu cầu LLM TÁCH tên khóa song
        # ngữ làm hai phần, nên thiếu cột này thì không thể biết ca song ngữ
        # trượt vì model đọc sai hay vì luật so sánh không ghép hai nửa lại.
        w.writerow(["case_id", "image_path", "verdict", "stage", "reason",
                    "error", "recipient_name", "certificate_name",
                    "certificate_name_alt", "issue_date"])
        for case in cases:
            result = results.get(case.case_id)
            ex = result.extracted if result else None
            w.writerow([
                case.case_id, case.image_path,
                result.verdict.value if result else "", result.stage if result else "",
                (result.reason or "") if result else "",
                errors.get(case.case_id, ""),
                (ex.recipient_name or "") if ex else "",
                (ex.certificate_name or "") if ex else "",
                (ex.certificate_name_alt or "") if ex else "",
                (ex.issue_date or "") if ex else "",
            ])
    print(f"\nĐã ghi kết quả thô: {path}")


def _ly_do_nguoi_duyet(note: str) -> str:
    """Rút lý do người duyệt ra khỏi cột note do match_images ghi.

    Định dạng note: "excel_row=2 | elis=REJECTED | lý do người duyệt: ..."
    """
    for part in (note or "").split("|"):
        part = part.strip()
        if part.startswith("lý do người duyệt:"):
            return part.split(":", 1)[1].strip()
    return "(không ghi lý do)"


def _verdict_nguoi_duyet(note: str) -> str:
    """Rút kết luận của người duyệt (elis=APPROVED/REJECTED) khỏi note."""
    for part in (note or "").split("|"):
        part = part.strip()
        if part.startswith("elis="):
            return part.split("=", 1)[1].strip().upper()
    return ""


def fill_verdict_from_elis(path) -> int:
    """Điền gt_verdict bằng quyết định CỦA NGƯỜI DUYỆT (cột Submit Status).

    ĐÂY KHÔNG PHẢI LẤY ĐÁP ÁN CỦA MODEL LÀM ĐÁP ÁN CHUẨN. Submit Status là
    kết luận của người chấm thật — chính là thứ hệ thống sinh ra để thay thế
    — nên nó là nhãn chuẩn hợp lệ, và không cần gán tay dòng nào.

    Nó trả lời câu: "thay người chấm bằng hệ thống thì kết quả có giống
    người chấm không?" Khác với câu "hệ thống đọc đúng những gì in trên ảnh
    chưa" — câu sau vẫn cần nhãn tay vì không gì ngoài tấm ảnh biết điều đó.

    Phải đọc kèm bảng "bất đồng theo lý do" ở cuối báo cáo phê duyệt: người
    duyệt từ chối vì cả những lý do hệ thống KHÔNG kiểm (nộp trùng, HR đã ghi
    nhận giờ, khóa ngoài danh mục MOOC). Những ca đó lệch là ĐÚNG dự đoán, và
    chúng đo khoảng cách giữa phạm vi hệ thống và công việc thật của người
    duyệt. Đọc con số tổng mà bỏ bảng đó thì sẽ kết luận nhầm thành "AI đọc
    chứng chỉ kém", dẫn tới đi sửa prompt cho một vấn đề thuộc luật nghiệp vụ.
    """
    cases = dataset.read_dataset(path)
    da_dien = 0
    for case in cases:
        elis = _verdict_nguoi_duyet(case.note)
        if elis in ("APPROVED", "REJECTED"):
            case.gt_verdict = elis
            da_dien += 1
    dataset.write_dataset(cases, path)
    return da_dien


def check_dataset(cases) -> int:
    """Kiểm tra bộ dữ liệu mà KHÔNG gọi LLM — miễn phí, chạy bao nhiêu lần cũng được.

    Tồn tại vì hai câu hỏi "ảnh có đủ không" và "đã gán nhãn tới đâu" không
    cần tốn một đồng nào để trả lời, nhưng nếu chỉ có lệnh `run` thì cách duy
    nhất để biết là chạy hết cả bộ bằng LLM thật rồi đọc lỗi ở cuối.
    """
    thieu = [c for c in cases if not (PROJECT_ROOT / c.image_path).is_file()]
    print(f"Bộ dữ liệu: {len(cases)} ca")
    print(f"  Có file ảnh : {len(cases) - len(thieu)}")
    print(f"  THIẾU file  : {len(thieu)}")
    for c in thieu[:15]:
        print(f"      {c.case_id}: {c.image_path}")
    if len(thieu) > 15:
        print(f"      ... còn {len(thieu) - 15} ca nữa")

    print("\nTiến độ gán nhãn (ô trống = chưa gán, bỏ qua khi chấm điểm):")
    truong = ["recipient_name", "certificate_name", "certificate_name_alt",
              "issue_date", "expiry_date"]
    for t in truong:
        n = sum(1 for c in cases if c.field_label(t)[0])
        print(f"  gt_{t:<22} {n:>4}/{len(cases)}")
    n_verdict = sum(1 for c in cases if (c.gt_verdict or "").strip())
    print(f"  gt_{'verdict':<22} {n_verdict:>4}/{len(cases)}")

    if n_verdict == 0:
        print("\nChưa gán gt_verdict ca nào -> PHẦN 2 (precision/recall/F1)")
        print("sẽ không chấm được ca nào. Đó là đúng thiết kế, không phải lỗi.")
    return 0 if not thieu else 1


# ===================== chạy pipeline trên bộ dữ liệu =====================

def run_pipeline(cases, azure_client, dung_khi_loi: bool = False) -> tuple[dict, dict]:
    """Chạy pipeline thật cho từng ca.

    Trả về (results, errors):
      results = {case_id: ProcessResult | None}
      errors  = {case_id: lý do KHÔNG chạy được}  — chỉ chứa ca hỏng.

    VÌ SAO PHẢI GIỮ RIÊNG errors: bản trước chỉ ghi None rồi cuối cùng in ra
    một danh sách case_id trần. Gặp 84 ca hỏng thì người đọc nhận được 84 mã
    vô nghĩa và không có cách nào biết vì sao — thiếu file, hết quota LLM,
    hay file hỏng là ba nguyên nhân cần ba cách xử lý hoàn toàn khác nhau.
    Lý do có được in lúc chạy, nhưng nằm lẫn giữa hàng trăm dòng và cuộn mất.
    """
    results, errors = {}, {}
    for i, case in enumerate(cases, start=1):
        path = PROJECT_ROOT / case.image_path
        print(f"  [{i}/{len(cases)}] {case.case_id} ... ", end="", flush=True)

        if not path.is_file():
            print("KHÔNG THẤY FILE")
            results[case.case_id] = None
            errors[case.case_id] = f"không thấy file: {case.image_path}"
            continue

        try:
            images = file_utils.read_as_images(path)
            result = pipeline.process(
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
            results[case.case_id] = result
            print(f"{result.verdict.value} ({result.stage})")
        except Exception as e:
            # Một ca hỏng KHÔNG được làm chết cả lượt đánh giá — chạy lại từ
            # đầu nghĩa là trả tiền LLM lại cho những ca đã xong.
            logger.warning("Ca %s lỗi: %s", case.case_id, e)
            results[case.case_id] = None
            errors[case.case_id] = f"{type(e).__name__}: {e}"
            print(f"LỖI: {e}")
            if dung_khi_loi:
                # --traceback: dừng ngay ở ca hỏng ĐẦU TIÊN và in đủ ngăn xếp.
                # Thông báo lỗi một dòng thường chỉ nói "lỗi gọi Gemma: ..."
                # mà không nói dòng nào ném ra, nên không sửa được gì từ nó.
                import traceback
                print("\n--- traceback đầy đủ (dừng vì --traceback) ---")
                traceback.print_exc()
                break
    return results, errors


def _gom_theo_ly_do(errors: dict) -> list[tuple[str, list[str]]]:
    """Gom lý do hỏng thành nhóm, nhóm đông nhất lên đầu.

    Gom theo lý do ĐÃ RÚT GỌN: thông báo lỗi thường kèm tên file hoặc id nên
    mỗi ca ra một chuỗi khác nhau, gom nguyên văn thì 84 ca thành 84 nhóm và
    không tóm tắt được gì.
    """
    nhom: dict[str, list[str]] = {}
    for case_id, reason in errors.items():
        rut_gon = reason.split(":")[0].strip() if ":" in reason else reason
        # Giữ thêm một ít ngữ cảnh sau dấu hai chấm, nhưng cắt phần đuôi hay
        # thay đổi (đường dẫn, id) để các ca cùng nguyên nhân về một nhóm.
        duoi = reason.split(":", 1)[1].strip() if ":" in reason else ""
        key = f"{rut_gon}: {duoi[:60]}" if duoi else rut_gon
        nhom.setdefault(key, []).append(case_id)
    return sorted(nhom.items(), key=lambda x: -len(x[1]))


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


# Phân loại lý do người duyệt thành nhóm thô, kèm cờ "hệ thống CÓ kiểm không".
#
# Vì sao cần: lý do viết tay nên cùng một chuyện có chục cách diễn đạt ("CB log
# trùng khóa", "log trùng", "CB submit trùng khoá", "CB log double khóa học").
# Gom nguyên văn thì 133 ca ra vài chục nhóm, không tóm tắt được gì.
#
# Cột cuối là câu quan trọng nhất của cả bảng: hệ thống chỉ so TÊN / KHÓA HỌC /
# NGÀY. Lý do nằm ngoài ba thứ đó thì hệ thống lệch là ĐÚNG DỰ ĐOÁN, và sửa nó
# là việc của luật nghiệp vụ chứ không phải của prompt.
_PHAN_LOAI = (
    (("trùng", "double", "đã log", "đã được hệ thống ghi nhận", "đã ghi nhận rồi"),
     "Nộp trùng khóa", False),
    (("tự động", "đồng bộ", "sync", "không cần submit"),
     "HR/hệ thống đã tự ghi nhận", False),
    (("không thuộc", "ngoài danh mục", "không nằm trong"),
     "Khóa ngoài danh mục MOOC", False),
    (("thời gian hoàn thành", "bổ sung thời gian", "thiếu thời gian"),
     "Chứng chỉ thiếu thời gian hoàn thành", True),
)


def _nhom_ly_do(reason: str) -> tuple[str, bool]:
    """Trả về (tên nhóm, hệ thống CÓ kiểm thứ này không)."""
    thap = (reason or "").lower()
    for tu_khoa, name, trong_pham_vi in _PHAN_LOAI:
        if any(t in thap for t in tu_khoa):
            return name, trong_pham_vi
    # Không khớp mẫu nào -> coi là TRONG phạm vi. Cố ý chọn hướng này: đoán
    # nhầm thành "ngoài phạm vi" là lặng lẽ tha cho một lỗi thật của hệ thống.
    return "Khác / không rõ", True


def _print_disagreements(cases, result) -> None:
    """Gom ca bất đồng theo LÝ DO NGƯỜI DUYỆT đã ghi.

    Bảng này là thứ biến một con số đáng sợ thành một con số hành động được.
    "REJECTED recall 10%" đọc trần sẽ bị hiểu là "AI đọc chứng chỉ kém"; nhìn
    vào đây mới thấy phần lớn là "AI không kiểm trùng lặp" — hai kết luận dẫn
    tới hai việc hoàn toàn khác nhau, một bên sửa prompt, một bên thêm luật
    nghiệp vụ hoặc gọi thêm API eLIS.
    """
    if not result.wrong_cases:
        return
    note_by_id = {c.case_id: c.note for c in cases}
    gom: dict[str, list] = {}
    for case_id, actual, du, _reason, _stage in result.wrong_cases:
        gom.setdefault(_ly_do_nguoi_duyet(note_by_id.get(case_id, "")),
                       []).append((case_id, actual, du))

    # Gom tiếp thành nhóm thô — đây mới là bảng đem vào báo cáo được.
    theo_nhom: dict[tuple[str, bool], list] = {}
    for reason, ds in gom.items():
        theo_nhom.setdefault(_nhom_ly_do(reason), []).extend(ds)

    trong = sum(len(v) for (_, tpv), v in theo_nhom.items() if tpv)
    ngoai = len(result.wrong_cases) - trong

    print(f"\nBất đồng với người duyệt ({len(result.wrong_cases)}/{result.total} ca), "
          f"gom theo LÝ DO NGƯỜI DUYỆT:")
    print("-" * 78)
    print(f"{'Nhóm lý do':<40}{'Số ca':>7}   Hệ thống có kiểm?")
    for (name, trong_pham_vi), ds in sorted(theo_nhom.items(),
                                          key=lambda x: -len(x[1])):
        co = "CÓ  <-- lỗi thật" if trong_pham_vi else "không"
        print(f"{name:<40}{len(ds):>7}   {co}")
        print(f"    vd: {', '.join(c[0] for c in ds[:3])}")
    print("-" * 78)
    print(f"  {ngoai:>3} ca ngoài phạm vi — hệ thống KHÔNG được thiết kế để bắt")
    print("        (nộp trùng, HR đã ghi nhận, khóa ngoài danh mục MOOC).")
    print("        Đây là KHOẢNG CÁCH PHẠM VI, không phải AI đọc ảnh kém. Sửa")
    print("        bằng luật nghiệp vụ hoặc lấy thêm dữ liệu từ eLIS, không")
    print("        phải bằng cách chỉnh prompt.")
    print(f"  {trong:>3} ca TRONG phạm vi — đây mới là lỗi thật của hệ thống,")
    print("        liên quan tên / khóa học / ngày. Nhìn vào đây trước tiên.")


def print_decision_report(result) -> None:
    print("\n" + "=" * 78)
    print("PHẦN 2 — PHÊ DUYỆT (precision / recall / F1)")
    print("=" * 78)

    if result.total == 0:
        print("Không có ca nào chấm được. Kiểm tra cột gt_verdict trong file nhãn.")
        return

    print(f"Số ca chấm được: {result.total}     Accuracy: {_pct(result.accuracy)}"
          f"     Macro-F1: {_pct(result.macro_f1)}")

    print("\nMa trận nhầm lẫn (hàng = thực tế, cột = model đoán):")
    print(f"{'':>22}{'APPROVED':>12}{'REJECTED':>12}")
    for actual in decision_score.LABELS:
        row = "".join(f"{result.matrix[(actual, dd)]:>12}" for dd in decision_score.LABELS)
        print(f"  thực tế {actual:<12}{row}")

    print("\nTheo từng nhãn (coi nhãn đó là positive):")
    print(f"{'Nhãn':<12}{'Số ca':>7}{'Precision':>11}{'Recall':>9}{'F1':>8}")
    print("-" * 47)
    for label in decision_score.LABELS:
        d = result.by_label[label]
        print(f"{label:<12}{d.support:>7}{_pct(d.precision):>11}"
              f"{_pct(d.recall):>9}{_pct(d.f1):>8}")
    print("-" * 47)
    print("REJECTED recall thấp    = chứng chỉ sai LỌT QUA, bị duyệt oan.")
    print("REJECTED precision thấp = từ chối OAN người làm thật.")

    if result.by_stage:
        print("\nTheo tầng xử lý (tầng nào quyết định, và quyết định có đúng không):")
        print(f"{'Tầng':<16}{'Số ca':>7}{'Đúng':>7}{'Tỷ lệ':>9}")
        print("-" * 39)
        for stage, (correct, total) in sorted(result.by_stage.items()):
            print(f"{stage:<16}{total:>7}{correct:>7}{_pct(correct / total):>9}")
        print("-" * 39)
        print("Tầng llm2 / llm1_vs_llm2 là những ca phải gọi Azure OCR (tốn tiền).")
        print("Nếu tỷ lệ đúng ở đó không cao hơn llm1 thì tầng 2 chưa đáng giá tiền.")

    if result.wrong_cases:
        print(f"\nCa đoán sai ({len(result.wrong_cases)}):")
        for case_id, actual, dd, reason, stage in result.wrong_cases:
            print(f"  {case_id:<24} đúng={actual:<9} model={dd:<9} [{stage}]")
            print(f"  {'':<24} lý do model: {reason}")

    if result.technical_error_cases:
        print(f"\nLoại khỏi phép đo — hỏng kỹ thuật ({len(result.technical_error_cases)} ca):")
        for case_id, stage, reason in result.technical_error_cases:
            print(f"  {case_id:<24} [{stage}] {reason}")
        print("  (Đây là lỗi hạ tầng, không phải model đoán sai — nên không")
        print("   tính vào precision/recall. Nhưng nhiều quá thì số đo mất ý nghĩa")
        print("   vì phần lớn bộ dữ liệu đã bị loại.)")

    if result.unlabeled_cases:
        print(f"\nChưa gán gt_verdict ({len(result.unlabeled_cases)} ca): "
              f"{', '.join(result.unlabeled_cases[:10])}")


# ================================= CLI =================================

def main() -> int:
    p = argparse.ArgumentParser(description="Đánh giá trích xuất + phê duyệt")
    p.add_argument("command",
                   choices=["template", "from-archive", "run", "check",
                            "fill-verdict", "export-errors",
                            "export-compare"])
    p.add_argument("--file", default=str(DEFAULT_DATASET_FILE),
                   help="file nhãn CSV")
    p.add_argument("--images", default=str(PROJECT_ROOT / "data"),
                   help="thư mục ảnh (dùng cho 'template' và 'export-compare')")
    p.add_argument("--archive", default=None,
                   help="thư mục kho (chỉ dùng cho lệnh 'from-archive')")
    p.add_argument("--excel", default=None,
                   help="file Excel gốc (lệnh 'export-compare' cần, để in đủ mọi dòng)")
    p.add_argument("--out", default=None,
                   help="file xuất ra (chỉ dùng cho lệnh 'export-errors')")
    p.add_argument("--limit", type=int, default=0,
                   help="chỉ chạy N ca đầu (để thử rẻ trước khi chạy cả bộ)")
    p.add_argument("--traceback", action="store_true",
                   help="in traceback đầy đủ của ca lỗi đầu tiên rồi dừng")
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
        print("\nĐể trống  = chưa gán nhãn, bỏ qua khi tính điểm.")
        print(f"Ghi '{dataset.NOT_PRESENT}'      = ảnh không in trường đó "
              f"(kỳ vọng model trả về rỗng).")
        return 0

    if args.command == "export-compare":
        from evaluation import export_compare
        ra = args.out or str(PROJECT_ROOT / "evaluation" / "logandcompare.csv")
        if not args.excel:
            print("Lệnh này cần --excel (file gốc) để in ĐỦ mọi dòng, kể cả")
            print("dòng chưa có ảnh. Ví dụ:")
            print("    python -m evaluation.run_eval export-compare "
                  "--excel data\\information.xlsx --images data\\image")
            return 1
        tk = export_compare.build(args.excel, args.images, args.file,
                                  DEFAULT_RESULT_FILE, ra)
        print(f"Đã ghi {ra}")
        print(f"  {tk['tong']} dòng (đúng bằng số dòng Excel):")
        print(f"     {tk['co_anh']:>4} dòng CÓ ảnh   -> cột AI có kết luận")
        print(f"     {tk['khong_anh']:>4} dòng KHÔNG ảnh -> AI để trống, "
              f"NOTE ghi '{export_compare.GHI_CHU_KHONG_ANH}'")
        print(f"  {tk['lech']} ca HUMAN khác AI (chỉ tính dòng đã xử lý).")
        if tk["note_giu"]:
            print(f"\n  Chép lại {tk['note_giu']} ghi chú NOTE viết tay từ bản cũ")
            print("  (khớp theo TÊN NV + TÊN KHÓA, không theo case_id — số thứ tự")
            print("   đổi khi bộ dữ liệu đổi, chép theo nó là gán nhầm người).")
        if tk["note_mo_coi"]:
            print(f"\n  !! {tk['note_mo_coi']} ghi chú cũ KHÔNG tìm được dòng "
                  f"tương ứng ở bản mới.")
            print("     Những ca đó không còn trong bộ dữ liệu này. Giữ bản cũ")
            print("     lại nếu còn cần — công viết tay không lấy lại được.")
        return 0

    cases = dataset.read_dataset(args.file)

    if args.command == "check":
        return check_dataset(cases)

    if args.command == "export-compare":
        from evaluation import export_compare
        ra = args.out or str(PROJECT_ROOT / "evaluation" / "logandcompare.csv")
        if not args.excel:
            print("Lệnh này cần --excel (file gốc) để in ĐỦ mọi dòng, kể cả")
            print("dòng chưa có ảnh. Ví dụ:")
            print("    python -m evaluation.run_eval export-compare "
                  "--excel data\\information.xlsx --images data\\image")
            return 1
        tk = export_compare.build(args.excel, args.images, args.file,
                                  DEFAULT_RESULT_FILE, ra)
        print(f"Đã ghi {ra}")
        print(f"  {tk['tong']} dòng (đúng bằng số dòng Excel):")
        print(f"     {tk['co_anh']:>4} dòng CÓ ảnh   -> cột AI có kết luận")
        print(f"     {tk['khong_anh']:>4} dòng KHÔNG ảnh -> AI để trống, "
              f"NOTE ghi '{export_compare.GHI_CHU_KHONG_ANH}'")
        print(f"  {tk['lech']} ca HUMAN khác AI (chỉ tính dòng đã xử lý).")
        if tk["note_giu"]:
            print(f"\n  Chép lại {tk['note_giu']} ghi chú NOTE viết tay từ bản cũ")
            print("  (khớp theo TÊN NV + TÊN KHÓA, không theo case_id — số thứ tự")
            print("   đổi khi bộ dữ liệu đổi, chép theo nó là gán nhầm người).")
        if tk["note_mo_coi"]:
            print(f"\n  !! {tk['note_mo_coi']} ghi chú cũ KHÔNG tìm được dòng "
                  f"tương ứng ở bản mới.")
            print("     Những ca đó không còn trong bộ dữ liệu này. Giữ bản cũ")
            print("     lại nếu còn cần — công viết tay không lấy lại được.")
        return 0

    cases = dataset.read_dataset(args.file)

    if args.command == "check":
        return check_dataset(cases)

    if args.command == "export-errors":
        from evaluation import export_errors
        ra = args.out or str(PROJECT_ROOT / "evaluation" / "error_review.xlsx")
        a, b = export_errors.build(args.file, DEFAULT_RESULT_FILE, ra)
        print(f"Đã ghi {ra}")
        print(f"  Máy duyệt / Người từ chối : {a:>3} ca  (ngoài phạm vi hệ thống)")
        print(f"  Máy từ chối / Người duyệt : {b:>3} ca  (lỗi thật của hệ thống)")
        print("\nHai cột nền vàng cuối mỗi sheet để HR điền. Xem sheet")
        print("'Tổng quan' để biết cách đọc và cách điền.")
        return 0

    if args.command == "fill-verdict":
        n = fill_verdict_from_elis(args.file)
        print(f"Đã điền gt_verdict wait {n}/{len(cases)} ca "
              f"bằng kết luận của NGƯỜI DUYỆT (cột Submit Status).")
        print("\nĐây là nhãn chuẩn hợp lệ: người chấm thật chính là thứ hệ")
        print("thống sinh ra để thay thế. Không cần gán tay dòng nào.")
        print("\nNó đo: 'thay người chấm bằng hệ thống thì kết quả có giống")
        print("người chấm không'. KHÁC với 'hệ thống đọc đúng những gì in trên")
        print("ảnh chưa' — câu sau vẫn cần nhãn tay ở các cột gt_ còn lại.")
        print("\nKhi đọc kết quả, xem bảng 'bất đồng theo lý do' ở cuối:")
        print("người duyệt từ chối vì cả những lý do hệ thống không kiểm.")
        return 0

    if args.limit and args.limit < len(cases):
        # Nói rõ đã CẮT BỚT. Im lặng thì con số ở cuối trông như đo cả bộ.
        print(f"--limit {args.limit}: chỉ chạy {args.limit}/{len(cases)} ca đầu.")
        cases = cases[:args.limit]

    print(f"Bộ dữ liệu: {len(cases)} ca từ {args.file}")
    print(f"Sắp gọi LLM thật wait {len(cases)} ca. Ctrl+C để hủy.\n")

    azure_client = ocr_azure.create_client()
    results, errors = run_pipeline(cases, azure_client, dung_khi_loi=args.traceback)
    _write_raw_results(cases, results, errors)

    predicted_extractions = {
        code: (result.extracted if result is not None else None)
        for code, result in results.items()
    }
    print_extraction_report(extraction_score.score(cases, predicted_extractions))

    not_run = extraction_score.failed_case_ids(cases, results)
    if not_run:
        ty_le = len(not_run) / len(cases) * 100
        print(f"\n{'=' * 78}")
        print(f"KHÔNG CHẠY ĐƯỢC: {len(not_run)}/{len(cases)} ca ({ty_le:.0f}%)")
        print("=" * 78)
        # Ngưỡng 20% là chỗ số đo bắt đầu mất ý nghĩa: phần lớn bộ dữ liệu đã
        # rơi ra ngoài phép đo, nên mọi tỷ lệ tính trên phần còn lại chỉ nói
        # về một mẫu đã bị chọn lọc bởi chính lỗi hạ tầng.
        if ty_le >= 20:
            print("Tỷ lệ này quá cao để tin vào các con số ở trên: phần lớn bộ")
            print("dữ liệu đã rơi ra ngoài phép đo. Sửa nguyên nhân dưới đây")
            print("rồi chạy lại, đừng đọc số trước.\n")
        for reason, ds in _gom_theo_ly_do(errors):
            print(f"  {len(ds):>4} ca — {reason}")
            print(f"         vd: {', '.join(ds[:4])}")
        print(f"\n  Chi tiết từng ca: {DEFAULT_RESULT_FILE.name}")

    kq_quyet_dinh = decision_score.score(cases, results)
    print_decision_report(kq_quyet_dinh)
    _print_disagreements(cases, kq_quyet_dinh)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
