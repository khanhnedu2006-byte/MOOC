"""Chạy job THẬT một vòng, nhưng HỎI NGƯỜI trước MỖI lần nộp API ③.

Khác `run.py once` đúng một chỗ: trước khi gửi kết quả của từng chứng chỉ về
eLIS, in kết luận + thứ AI đọc được rồi chờ người gõ:

    y  nộp kết quả này về eLIS
    e  sửa comment gửi người học, rồi hỏi lại
    s  bỏ qua, KHÔNG nộp — chứng chỉ ở lại WAITING và được ĐÁNH DẤU bỏ qua
       (stage skipped_manual): các lần chạy sau, kể cả job thật run.py, KHÔNG
       tải/quét lại. Gõ n cũng được, như trước.
    q  dừng hẳn: không nộp cái này, không xử lý các cái phía sau

Thêm --auto-approve: ca HỢP LỆ (APPROVED) tự nộp luôn, không hỏi — decisions.csv
ghi `auto_submit`. Không có cờ thì ca nào cũng hỏi.

Mọi thứ còn lại GIỐNG HỆT job thật (dùng lại chính code của run.py): chống nộp
trùng, giãn cách ca hỏng kỹ thuật, ghi mooc_log.db, gửi email cảnh báo. Chọn
`n` thì dòng log trong DB giữ elis_sent_ok = NULL ("chưa nộp") — đúng sự thật.

Mặc định chạy LIÊN TỤC (loop): hết hàng đợi thì hỏi lại API ① và xử lý tiếp
những chứng chỉ mới vào. Luật nghỉ như run.py loop: eLIS vừa nhận được cái nào
thì làm vòng kế ngay, không thì nghỉ POLL_INTERVAL_SECONDS. Ca bấm `s` đã được
đánh dấu nên vòng sau không hỏi lại. Ctrl+C hoặc `q` để dừng.

TRƯỚC KHI HỎI, mỗi chứng chỉ có một thư mục riêng để mở xem:
    manual_review/<thời điểm>/
        decisions.csv                   tổng hợp mọi quyết định của lần chạy
        [vong_<NN>/]                    chỉ ở chế độ loop: mỗi vòng một thư mục
        <NN>_<user_course_id>/
            00_info.json                dữ liệu getCert (API ①)
            certificate.<đuôi>          file chứng chỉ tải về (API ②)
            01_result.json              AI đọc được gì + kết luận pipeline
            01a_llm1.json               Gemma (LLM1) đọc ảnh: kết quả + số giây
            01b_ocr.txt                 text OCR (chỉ khi tới tầng 2)
            01c_llm2.json               LLM2 đọc text OCR (chỉ khi tới tầng 2)
                                        bước nào lỗi -> <tên bước>_error.json
            01_history.json             (chỉ ca nộp trùng) mọi khóa của nhân viên
            02_payload.json             payload API ③ SẼ gửi nếu chọn y
            review.txt                  đúng nội dung in ra màn hình lúc hỏi
            03_decision.json            ghi SAU khi trả lời: y/n/q + eLIS có nhận
Ca nộp trùng không tải file nên không có certificate.*. Ca hỏng kỹ thuật / bỏ
qua không gọi API ③ nên không hỏi, thư mục chỉ có 00_info, file và 01_result.
Thư mục này chứa dữ liệu nhân viên thật — không commit.

Cách dùng (BẮT BUỘC chạy trong terminal):
    python run_with_manual_approve.py           # chạy liên tục (loop)
    python run_with_manual_approve.py once      # một vòng rồi thoát, như run.py once
    python run_with_manual_approve.py retry     # một vòng, bỏ giãn cách, như run.py retry
    python run_with_manual_approve.py --include-skipped   # xem lại cả ca đã bấm s
    python run_with_manual_approve.py --auto-approve      # ca hợp lệ tự nộp, chỉ hỏi ca từ chối
"""

import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import csv
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import run  # noqa: E402  (dùng lại luật của job thật, tránh hai bản lệch nhau)
import archive  # noqa: E402
import llm_text  # noqa: E402
import llm_vision  # noqa: E402
import ocr  # noqa: E402
from config import settings  # noqa: E402
from database import database  # noqa: E402

logger = logging.getLogger("manual_approve")


class StopRound(Exception):
    """Người vận hành chọn `q`."""


#1. Ghi nhớ chứng chỉ đang xử lý + lưu ra thư mục riêng

# submit_result() chỉ nhận payload API ③ (id, status, comment) — không đủ để
# người quyết định. Bọc hai hàm của run.py để giữ lại info getCert và kết luận
# pipeline của chứng chỉ ĐANG xử lý, đồng thời ghi chúng ra đĩa.
_current: dict = {}


def _save_json(name: str, data) -> None:
    folder = _current.get("folder")
    if folder is None:
        return
    try:
        (folder / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
    except OSError as e:
        logger.warning("Không ghi được %s: %s", name, e)


def _safe_name(value) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(value))[:40]


# Thư mục đang ghi. Chế độ loop đổi sang vong_<NN>/ đầu mỗi vòng, để chứng chỉ
# được hỏi lại ở vòng sau (vd hết giãn cách) không đè thư mục vòng trước.
_round_dir: dict = {}


def _wrap_handle(original, review_dir: Path):
    def wrapper(info, ocr_client, position, total):
        _current.clear()
        base = _round_dir.get("dir", review_dir)
        folder = base / f"{position:02d}_{_safe_name(info['id'])}"
        folder.mkdir(parents=True, exist_ok=True)
        _current.update(info=info, position=position, total=total, folder=folder)
        _save_json("00_info.json", info)
        return original(info, ocr_client, position, total)
    return wrapper


# Ca file sai định dạng dừng TRƯỚC pipeline nên _wrap_scan không chạy — vẫn lưu
# file ra để người mở xem được.
def _wrap_skip_file(original):
    def wrapper(info, downloaded, position, total):
        data = downloaded.get("anh_bytes") or b""
        mime = str(downloaded.get("unsupported_mime") or "")
        ext = "." + mime.rsplit("/", 1)[-1].split(".")[-1][:10] if "/" in mime else ".bin"
        try:
            (_current["folder"] / f"certificate{ext}").write_bytes(data)
        except (OSError, KeyError) as e:
            logger.warning("Không lưu được file để xem: %s", e)
        _save_json("01_result.json", {"result": {
            "verdict": "WAITING", "stage": run.database.SKIP_UNSUPPORTED_FILE_STAGE,
            "reason": f"File định dạng không hỗ trợ ({mime})",
            "ten_file": downloaded.get("ten_file")}, "skip": True})
        return original(info, downloaded, position, total)
    return wrapper


def _wrap_skip_provider(original):
    def wrapper(info, position, total):
        _save_json("01_result.json", {"result": {
            "verdict": "WAITING", "stage": run.database.SKIP_PROVIDER_STAGE,
            "reason": f"Nhà cung cấp {info.get('providerName')!r} không xử lý tự động"},
            "skip": True})
        return original(info, position, total)
    return wrapper


def _wrap_skip_link(original):
    def wrapper(info, position, total):
        _save_json("01_result.json", {"result": {
            "verdict": "WAITING", "stage": run.database.SKIP_INVALID_LINK_STAGE,
            "reason": f"Link khóa học không hợp lệ ({info.get('courseLink')!r})"},
            "skip": True})
        return original(info, position, total)
    return wrapper


# Ghi kết quả TỪNG TẦNG của pipeline. run.scan_certificate tra
# llm_vision.extract_from_image / ocr.ocr_images / llm_text.extract_from_text
# lúc gọi, nên thay thuộc tính module là đủ. pipeline.process nuốt lỗi của các
# hàm này, nên phải ghi lỗi TRƯỚC khi ném tiếp.
def _wrap_step(original, step: str, save):
    def wrapper(*args, **kwargs):
        started = time.monotonic()
        try:
            value = original(*args, **kwargs)
        except Exception as e:
            _save_json(f"{step}_error.json", {
                "error": f"{type(e).__name__}: {e}",
                "seconds": round(time.monotonic() - started, 2)})
            raise
        save(value, round(time.monotonic() - started, 2))
        return value
    return wrapper


def _save_extracted(step: str):
    return lambda value, secs: _save_json(
        f"{step}.json", {"seconds": secs, "extracted": value.model_dump(mode="json")})


def _save_ocr(text: str, secs: float) -> None:
    folder = _current.get("folder")
    if folder is None:
        return
    try:
        (folder / "01b_ocr.txt").write_text(
            f"# Azure OCR — {secs}s, {len(text)} ký tự\n\n{text}", encoding="utf-8")
    except OSError as e:
        logger.warning("Không ghi được 01b_ocr.txt: %s", e)


def _wrap_scan(original):
    def wrapper(image_bytes, info, ocr_client):
        path = _current["folder"] / f"certificate{archive._file_extension(image_bytes)}"
        try:
            path.write_bytes(image_bytes)
            _current["file"] = path
        except OSError as e:
            logger.warning("Không lưu được file để xem: %s", e)
        result = original(image_bytes, info, ocr_client)
        _current["result"] = result
        _save_json("01_result.json", {
            "result": result.model_dump(mode="json"),
            "technical_failure": run.is_technical_failure(result),
            "skip": run.is_skip(result),
        })
        return result
    return wrapper


#2. Hỏi người trước khi nộp

def _describe(dto: dict) -> str:
    info = _current.get("info") or {}
    result = _current.get("result")
    lines = [
        "",
        "=" * 72,
        f"[{_current.get('position', '?')}/{_current.get('total', '?')}] CHỜ DUYỆT NỘP API ③",
        "-" * 72,
        f"Nhân viên  : {info.get('employeeName')} ({info.get('employeeId')}, "
        f"{info.get('employeeEmail')})",
        f"Khóa eLIS  : {info.get('courseName')}  | {info.get('providerName')}",
    ]
    if result is not None and result.extracted:
        ex = result.extracted
        lines.append(f"AI đọc ảnh : tên={ex.recipient_name!r}")
        lines.append(f"             khóa={ex.certificate_name!r}"
                     + (f" / {ex.certificate_name_alt!r}" if ex.certificate_name_alt else ""))
        lines.append(f"             ngày={ex.issue_date!r}")
    stage = result.stage if result is not None else "duplicate"
    lines += [
        f"Kết luận   : {dto['status']}   (stage: {stage})",
        f"Comment gửi: {dto.get('comment')!r}",
    ]
    if _current.get("file"):
        lines.append(f"File       : {_current['file']}")
    if "history" in _current:
        lines += _history_lines(_current["history"], info)
    lines.append("=" * 72)
    return "\n".join(lines)


#2a. Ca nộp trùng: lịch sử khóa học của nhân viên

# eLIS KHÔNG có trường ngày hoàn thành — ngày đó chỉ in trên ảnh chứng chỉ, mà
# API ② không trả file của bản ghi đã rời WAITING. Hai mốc có được:
#   submitDatetime - lúc nhân viên nộp chứng chỉ lên eLIS
#   ActionDateTime - lúc bản ghi được xử lý / cập nhật lần cuối
HISTORY_FIELDS = ("courseName", "providerName", "submitStatus", "submitDatetime",
                  "ActionDateTime", "ActionBy", "comment", "id")


def _load_history(info: dict) -> list[dict] | None:
    email = str(info.get("employeeEmail") or "").strip().lower()
    if not email:
        return None
    try:
        rows = run.call_with_retry(run.client.get_by_email, email)
    except run.client.ElisError as e:
        logger.warning("Không đọc được lịch sử của %s: %s", email, e)
        return None
    # API từng bỏ qua tham số lọc trong im lặng — chỉ giữ đúng người này.
    rows = [r for r in rows
            if str(r.get("employeeEmail") or "").strip().lower() == email]
    rows.sort(key=lambda r: str(r.get("submitDatetime") or ""), reverse=True)
    return [{k: r.get(k) for k in HISTORY_FIELDS} for r in rows]


def _short_time(value) -> str:
    return str(value or "-")[:16].replace("T", " ")


def _history_lines(history: list[dict] | None, info: dict) -> list[str]:
    lines = ["-" * 72]
    if history is None:
        return lines + ["Lịch sử     : KHÔNG đọc được từ eLIS (xem log)"]
    current = run.normalize(info.get("courseName"))
    approved = [r for r in history if str(r.get("submitStatus") or "").upper() == "APPROVED"]
    lines.append(f"Các khóa ĐÃ DUYỆT của nhân viên ({len(approved)}/{len(history)} bản ghi). "
                 "* = trùng khóa đang xét")
    lines.append(f"  {'':1} {'Nộp lúc':<16}  {'Duyệt/cập nhật':<16}  Khóa học  [duyệt bởi]")
    for r in approved:
        mark = "*" if run.normalize(r.get("courseName")) == current else " "
        lines.append(f"  {mark} {_short_time(r.get('submitDatetime')):<16}  "
                     f"{_short_time(r.get('ActionDateTime')):<16}  "
                     f"{r.get('courseName')}  [{r.get('comment') or r.get('ActionBy') or '-'}]")
    lines.append("  (eLIS không lưu ngày hoàn thành; đầy đủ mọi trạng thái: 01_history.json)")
    return lines


def _ask() -> str:
    while True:
        try:
            answer = input("Nộp kết quả này? [y] nộp  [e] sửa comment  "
                           "[s] bỏ qua (giữ WAITING)  [q] dừng: ").strip().lower()
        except EOFError:
            return "q"
        if answer == "n":                # phím cũ, vẫn nhận
            answer = "s"
        if answer in ("y", "e", "s", "q"):
            return answer
        print("  Gõ y, e, s hoặc q.")


# API ③ bắt buộc có comment, nên bỏ trống = giữ comment hiện tại.
def _edit_comment(current: str) -> str:
    print(f"  Comment hiện tại: {current!r}")
    try:
        new = input("  Comment mới (Enter để giữ nguyên): ").strip()
    except EOFError:
        return current
    if not new:
        print("  -> Giữ nguyên comment.")
        return current
    print(f"  -> Comment sẽ gửi: {new!r}")
    return new


def _wrap_submit(original, decisions_path: Path, auto_approve: bool = False):
    def wrapper(dto: dict) -> bool:
        if "result" not in _current:            # ca nộp trùng: không qua pipeline
            _save_json("01_result.json", {"result": {
                "verdict": dto["status"], "stage": "duplicate",
                "reason": dto.get("comment")}})
            _current["history"] = _load_history(_current.get("info") or {})
            _save_json("01_history.json", {
                "note": "Mọi bản ghi getCert của nhân viên (mọi trạng thái). eLIS "
                        "không lưu ngày hoàn thành; submitDatetime = lúc nộp, "
                        "ActionDateTime = lúc xử lý/cập nhật lần cuối.",
                "records": _current["history"]})
        _save_json("02_payload.json", {"note": "Payload API ③ sẽ gửi nếu chọn y",
                                       "payload": [dto]})
        text = _describe(dto)
        folder = _current.get("folder")
        if folder is not None:
            try:
                (folder / "review.txt").write_text(text.strip() + "\n", encoding="utf-8")
            except OSError as e:
                logger.warning("Không ghi được review.txt: %s", e)
            text += f"\nThư mục    : {folder}"
        print(text, flush=True)

        original_comment = dto.get("comment")
        # --auto-approve: ca hợp lệ (APPROVED) tự nộp, không hỏi; ca từ chối vẫn hỏi.
        auto = auto_approve and dto["status"] == "APPROVED"
        if auto:
            answer = "y"
            print("  -> Hợp lệ: tự nộp, không hỏi.")
        while not auto:
            answer = _ask()
            if answer != "e":
                break
            dto = {**dto, "comment": _edit_comment(dto.get("comment") or "")}
        decision = "auto_submit" if auto else {"y": "submit", "s": "skip", "q": "stop"}[answer]
        edited = dto.get("comment") != original_comment
        if edited:
            _save_json("02_payload.json", {
                "note": "Payload API ③ sẽ gửi nếu chọn y — comment ĐÃ SỬA TAY",
                "original_comment": original_comment, "payload": [dto]})

        info = _current.get("info") or {}
        result = _current.get("result")
        accepted = None
        if answer == "y":
            accepted = original(dto)
            print(f"  -> {'eLIS ĐÃ NHẬN' if accepted else 'eLIS KHÔNG NHẬN (xem log)'}")
        elif answer == "s":
            _mark_manual_skip(info, dto)
            print("  -> KHÔNG nộp. Chứng chỉ ở lại WAITING, đã đánh dấu bỏ qua "
                  "(lần chạy sau không quét lại).")
        else:
            print("  -> Dừng vòng.")

        _save_json("03_decision.json", {
            "time": datetime.now().isoformat(timespec="seconds"),
            "decision": decision,
            "elis_accepted": accepted,
            "comment": dto.get("comment"),
            "original_comment": original_comment if edited else None,
        })
        _record(decisions_path, {
            "time": datetime.now().isoformat(timespec="seconds"),
            "id": dto["id"],
            "employeeName": info.get("employeeName"),
            "employeeEmail": info.get("employeeEmail"),
            "courseName": info.get("courseName"),
            "verdict": dto["status"],
            "stage": result.stage if result is not None else "duplicate",
            "comment": dto.get("comment"),
            "original_comment": original_comment if edited else None,
            "decision": decision,
            "elis_accepted": accepted,
        })

        if answer == "q":
            raise StopRound()
        return bool(accepted)
    return wrapper


# Ghi một dòng log MỚI NHẤT với stage skipped_manual: database.skipped_ids()
# đọc dòng mới nhất của mỗi chứng chỉ, nên vòng sau tự loại nó trước API ②.
# Dòng log kết luận của pipeline vẫn giữ nguyên phía trước để tra cứu.
def _mark_manual_skip(info: dict, dto: dict) -> None:
    try:
        database.write_failure_log(
            user_course_id=dto["id"], employee_id=info.get("employeeId"),
            verdict="WAITING",
            reason=f"Người vận hành bỏ qua khi duyệt tay (hệ thống đề xuất "
                   f"{dto['status']}: {dto.get('comment')})",
            stage=database.SKIP_MANUAL_STAGE,
            provider=info.get("providerName"), course_name=info.get("courseName"))
    except Exception as e:
        logger.warning("Không đánh dấu bỏ qua được cho %s: %s — lần sau sẽ quét lại.",
                       dto["id"], e)


DECISION_COLUMNS = ("time", "id", "employeeName", "employeeEmail", "courseName",
                    "verdict", "stage", "comment", "original_comment",
                    "decision", "elis_accepted")


def _record(path: Path, row: dict) -> None:
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=DECISION_COLUMNS)
        if new:
            writer.writeheader()
        writer.writerow(row)


#3. Điểm vào

# Lặp process_one_round như run.run_forever, có hỏi tay ở mỗi lần nộp.
# Luật nghỉ GIỐNG run_forever: chỉ làm vòng kế NGAY khi eLIS vừa nhận ít nhất
# một kết quả. Mốc khác (vd số đã quét) thì ca eLIS từ chối nhận (failList)
# vẫn WAITING, bị quét + hỏi lại liên tục không nghỉ, đốt tiền LLM.
def _run_loop(ocr_client, review_dir: Path) -> None:
    sleep_seconds = max(1, settings.poll_interval_seconds)
    logger.info("Chạy liên tục: hết việc thì hỏi lại eLIS mỗi %d giây. "
                "Ctrl+C hoặc q để dừng.", sleep_seconds)
    round_no, idle = 0, False
    total_scanned = total_accepted = 0
    while True:
        round_no += 1
        _round_dir["dir"] = review_dir / f"vong_{round_no:02d}"
        try:
            result = run.process_one_round(ocr_client)
        except StopRound:
            raise
        except Exception as e:
            logger.exception("Lỗi trong vòng %d: %s", round_no, e)
            result = run.RoundResult(0, 0)

        total_scanned += result.scanned_count
        total_accepted += result.accepted_count
        if result.scanned_count:
            logger.info("Hết vòng %d: xử lý %d, eLIS nhận %d (tổng: %d / %d).",
                        round_no, result.scanned_count, result.accepted_count,
                        total_scanned, total_accepted)

        if result.accepted_count > 0:
            idle = False
            continue
        if not idle:
            logger.info("Chưa có việc làm được ngay%s. Hỏi lại eLIS mỗi %d giây...",
                        f" ({result.deferred_count} ca chờ hết giãn cách)"
                        if result.deferred_count else "", sleep_seconds)
            idle = True
        time.sleep(sleep_seconds)


def main() -> int:
    args = sys.argv[1:]
    include_skipped = "--include-skipped" in args
    auto_approve = "--auto-approve" in args
    args = [a for a in args if a not in ("--include-skipped", "--auto-approve")]
    mode = args[0] if args else "loop"
    if mode not in ("loop", "once", "retry") or len(args) > 1:
        print("Usage: python run_with_manual_approve.py [loop|once|retry] [--include-skipped] [--auto-approve]")
        return 1
    if not sys.stdin.isatty():
        print("Phải chạy trong terminal để trả lời y/n/q.")
        return 1

    review_dir = PROJECT_ROOT / "manual_review" / datetime.now().strftime("%Y%m%d_%H%M%S")
    review_dir.mkdir(parents=True, exist_ok=True)
    decisions_path = review_dir / "decisions.csv"

    run.handle_one_certificate = _wrap_handle(run.handle_one_certificate, review_dir)
    run.scan_certificate = _wrap_scan(run.scan_certificate)
    llm_vision.detect_course_list = _wrap_step(
        llm_vision.detect_course_list, "01_0_course_list",
        _save_extracted("01_0_course_list"))
    llm_vision.extract_from_image = _wrap_step(
        llm_vision.extract_from_image, "01a_llm1", _save_extracted("01a_llm1"))
    ocr.ocr_images = _wrap_step(ocr.ocr_images, "01b_ocr", _save_ocr)
    llm_text.extract_from_text = _wrap_step(
        llm_text.extract_from_text, "01c_llm2", _save_extracted("01c_llm2"))
    run.skip_unsupported_file = _wrap_skip_file(run.skip_unsupported_file)
    run.skip_invalid_course_link = _wrap_skip_link(run.skip_invalid_course_link)
    run.skip_provider = _wrap_skip_provider(run.skip_provider)
    run.submit_result = _wrap_submit(run.submit_result, decisions_path, auto_approve)

    if auto_approve:
        logger.info("--auto-approve: ca hợp lệ tự nộp, chỉ hỏi ca từ chối.")

    if include_skipped:
        # Chỉ mở lại ca bấm `s`; ca bỏ qua tự động (email ngoài, file sai định
        # dạng, link sai) vẫn bị lọc như cũ.
        original_skipped_ids = database.skipped_ids

        def skipped_ids_without_manual(ids, db_path=None):
            manual = set()
            if ids:
                conn = database._connect(db_path)
                try:
                    for i in ids:
                        row = conn.execute(
                            "SELECT stage FROM process_log WHERE user_course_id = ? "
                            "ORDER BY id DESC LIMIT 1", (str(i),)).fetchone()
                        if row and row[0] == database.SKIP_MANUAL_STAGE:
                            manual.add(str(i))
                finally:
                    conn.close()
            return original_skipped_ids(ids, db_path) - manual

        database.skipped_ids = skipped_ids_without_manual
        logger.info("--include-skipped: xem lại cả các ca đã bấm s trước đó.")

    logger.warning("CHẠY THẬT với eLIS %s — mỗi lần nộp API ③ đều hỏi trước.",
                   settings.elis_base_url)
    logger.info("Mỗi chứng chỉ được lưu vào thư mục con của: %s", review_dir)

    database.init_db()
    ocr_client = ocr.create_client()
    try:
        if mode == "loop":
            _run_loop(ocr_client, review_dir)
        else:
            result = run.process_one_round(ocr_client, ignore_cooldown=(mode == "retry"))
            logger.info("Xong. Đã xử lý %d chứng chỉ, eLIS nhận %d.",
                        result.scanned_count, result.accepted_count)
    except StopRound:
        logger.info("Đã dừng theo lựa chọn của người vận hành.")
    except KeyboardInterrupt:
        logger.info("Đã dừng (Ctrl+C).")
    logger.info("Quyết định đã ghi: %s", decisions_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
