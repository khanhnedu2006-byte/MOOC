"""Chạy thử (dry run) job duyệt chứng chỉ — KHÔNG nộp kết quả về eLIS.

Đi đúng đường của `python run.py once`:
  API ① getCert -> lọc hàng đợi -> chống nộp trùng -> API ② tải file
  -> pipeline (LLM1 -> Azure OCR + LLM2 -> so đồng thuận) -> dựng payload API ③

nhưng dừng ở bước cuối: payload API ③ chỉ ghi ra file, KHÔNG gửi.

KHÔNG ĐỂ LẠI DẤU VẾT NÀO trong hệ thống thật:
  - Không gọi API ③ (client.update_status bị thay bằng hàm ném lỗi).
  - Không ghi mooc_log.db: ghi vào đó thì bộ đếm thử lại / danh sách bỏ qua
    của job thật bị lệch theo kết quả chạy thử. Chỉ ĐỌC để lọc hàng đợi.
  - Không gửi email cảnh báo, không đụng .alert_state.json / .report_state.json.
  - Không lưu vào cert_archive/ (ảnh lưu vào thư mục log của lần chạy thử).

VẪN TỐN TIỀN: LLM1, Azure OCR, LLM2 được gọi thật. Dùng --limit khi thử.

Mỗi lần chạy tạo một thư mục riêng:
    dry_run_logs/<YYYYmmdd_HHMMSS>/
        run.log                     log toàn bộ lần chạy
        01_queue.json               API ① trả về gì
        02_filter.json              quyết định lọc cho từng chứng chỉ
        certs/<NN>_<id>/
            steps.log               log riêng của chứng chỉ này
            00_info.json            dữ liệu getCert
            01_duplicate.json       kết quả tra nộp trùng
            02_download.json        + certificate.<đuôi>   file tải về
            03_images.json          + page_<N>.<đuôi>      ảnh đưa vào LLM
            04_llm1.json            Gemma đọc ảnh
            05_ocr.txt              text Azure OCR (chỉ khi tới tầng 2)
            06_llm2.json            LLM2 đọc text OCR (chỉ khi tới tầng 2)
            07_result.json          kết luận pipeline
            08_submit.json          payload API ③ SẼ gửi (hoặc lý do không gửi)
        summary.json / summary.csv  tổng hợp

Thư mục log chứa tên, email, mã nhân viên và ảnh chứng chỉ thật — không commit.

Cách dùng:
    python dry_run.py                       # một vòng, như `run.py once`
    python dry_run.py --limit 3             # chỉ 3 chứng chỉ đầu
    python dry_run.py --id <uc_id> --id <uc_id>
    python dry_run.py --ignore-cooldown     # như `run.py retry`
    python dry_run.py --keep-going          # hỏng kỹ thuật vẫn chạy tiếp
    python dry_run.py --submit-approved     # NỘP THẬT ca APPROVED (hỏi xác nhận)
    python dry_run.py --submit-approved --yes

--submit-approved: CHỈ ca APPROVED được gọi API ③ thật; REJECTED / trùng /
WAITING vẫn chỉ ghi file. Vẫn KHÔNG ghi mooc_log.db, nên các ca nộp thật ở
đây không xuất hiện trong báo cáo email — tra lại bằng summary.csv.
"""

import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import argparse
import csv
import json
import logging
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import run  # noqa: E402  (dùng lại luật của job thật, tránh hai bản lệch nhau)
import alert  # noqa: E402
import archive  # noqa: E402
import client  # noqa: E402
import file_utils  # noqa: E402
import llm_text  # noqa: E402
import llm_vision  # noqa: E402
import ocr  # noqa: E402
import pipeline  # noqa: E402
import scheduler  # noqa: E402
from config import settings  # noqa: E402
from database import database  # noqa: E402
from process_data import code_from_email, normalize  # noqa: E402
from schemas import InputInfo, ProcessResult, Verdict  # noqa: E402

logger = logging.getLogger("dry_run")

# Giữ bản THẬT trước khi lock_side_effects() thay client.update_status. Chỉ
# submit_approved() được dùng, và chỉ khi có --submit-approved.
_REAL_UPDATE_STATUS = client.update_status

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s]: %(message)s"


#1. Khóa mọi đường ghi ra ngoài

def _blocked(name: str):
    def _raise(*args, **kwargs):
        raise RuntimeError(f"DRY RUN: chặn lời gọi {name}.")
    return _raise


# Thay các hàm có tác dụng phụ bằng hàm ném lỗi. dry_run không gọi chúng, nhưng
# nếu sau này có ai sửa code dùng lại run.submit_result... thì nổ ngay thay vì
# lặng lẽ nộp thật.
def lock_side_effects() -> None:
    client.update_status = _blocked("API ③ client.update_status")
    for name in ("init_db", "write_log", "write_failure_log", "update_send_result"):
        setattr(database, name, _blocked(f"database.{name}"))
    for name in ("send_alert", "api_failed", "api_succeeded"):
        setattr(alert, name, _blocked(f"alert.{name}"))
    archive.save = _blocked("archive.save")
    archive.write_verdict = _blocked("archive.write_verdict")
    scheduler.check_and_send = _blocked("scheduler.check_and_send")


#2. Ghi kết quả từng bước

def _to_jsonable(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, timedelta):
        return value.total_seconds()
    return str(value)


class StepWriter:
    """Ghi file kết quả của một thư mục (cả lần chạy hoặc một chứng chỉ)."""

    def __init__(self, folder: Path):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)

    def json(self, name: str, data) -> None:
        (self.folder / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=_to_jsonable),
            encoding="utf-8")

    def text(self, name: str, content: str) -> None:
        (self.folder / name).write_text(content, encoding="utf-8")

    def bytes(self, name: str, content: bytes) -> None:
        (self.folder / name).write_bytes(content)


def attach_file_log(path: Path) -> logging.Handler:
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logging.getLogger().addHandler(handler)
    return handler


def _safe_name(value) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(value))[:40]


#3. Lọc hàng đợi — CHỈ ĐỌC mooc_log.db

# Cùng luật với run.filter_queue (bỏ ca SKIP, giãn cách ca hỏng kỹ thuật, chặn
# đầu hàng) nhưng trả về lý do cho TỪNG chứng chỉ để ghi log. Không gọi
# database.init_db(): nó tạo file/bảng nếu chưa có.
def filter_queue(items: list[dict], ignore_cooldown: bool) -> tuple[list, list]:
    ids = [i["id"] for i in items]
    skipped, state, db_note = set(), {}, None
    if not database.DB_PATH.is_file():
        db_note = f"Chưa có {database.DB_PATH.name} — coi như chưa xử lý lần nào."
    else:
        try:
            skipped = database.skipped_ids(ids)
            state = database.technical_retry_state(ids)
        except sqlite3.Error as e:
            db_note = f"Không đọc được {database.DB_PATH.name}: {e}"
    if db_note:
        logger.warning(db_note)

    threshold = max(1, settings.technical_alert_after)
    cooldown = timedelta(minutes=max(0, settings.technical_retry_cooldown_minutes))
    now = datetime.now()

    ready, decisions, blocked = [], [], False
    for item in items:
        uc_id = str(item["id"])
        row = {"id": uc_id, "employeeName": item.get("employeeName"),
               "courseName": item.get("courseName")}
        record = state.get(uc_id)
        if record:
            row["technical_failures"] = record[0]
            row["last_failed_at"] = record[1]
            row["alert_threshold_reached"] = record[0] >= threshold

        if uc_id in skipped:
            row["decision"] = "skip: đang chờ người duyệt (không xác minh được danh tính)"
        elif blocked:
            row["decision"] = "deferred: đứng sau ca đang giãn cách (chặn đầu hàng)"
        elif record and not ignore_cooldown:
            try:
                elapsed = now - datetime.fromisoformat(record[1])
            except (TypeError, ValueError):
                elapsed = cooldown
            if elapsed < cooldown:
                blocked = True
                row["decision"] = (f"deferred: giãn cách, còn "
                                   f"~{(cooldown - elapsed).total_seconds() / 60:.0f} phút")
            else:
                row["decision"] = "ready"
        else:
            row["decision"] = "ready"

        decisions.append(row)
        if row["decision"] == "ready":
            ready.append(item)

    return ready, [{"db_note": db_note, "ignore_cooldown": ignore_cooldown,
                    "cooldown_minutes": cooldown.total_seconds() / 60,
                    "alert_after": threshold}] + decisions


#4. Xử lý MỘT chứng chỉ, ghi từng bước

class CertReport(dict):
    """Một dòng của summary; technical=True nghĩa là job thật sẽ dừng vòng."""

    @property
    def technical(self) -> bool:
        return bool(self.get("technical"))


def check_duplicate(info: dict, approved_this_round: set, out: StepWriter) -> bool:
    email = str(info.get("employeeEmail") or "").strip().lower()
    course = normalize(info.get("courseName"))
    record = {"enabled": settings.duplicate_check, "email": email,
              "course_normalized": course}

    if not settings.duplicate_check:
        record["result"] = "tắt DUPLICATE_CHECK"
        out.json("01_duplicate.json", record)
        return False
    if not email or not course:
        record["result"] = "thiếu email/khóa học — không kiểm, để pipeline xử"
        out.json("01_duplicate.json", record)
        return False
    if (email, course) in approved_this_round:
        record["result"] = "TRÙNG với chứng chỉ vừa APPROVED trong lần chạy thử này"
        out.json("01_duplicate.json", record)
        return True

    try:
        approved = run.completed_courses(email)
    except client.ElisError as e:
        record["error"] = str(e)
        record["infrastructure"] = client.is_infrastructure(e)
        out.json("01_duplicate.json", record)
        if client.is_infrastructure(e):
            raise
        logger.warning("Không tra được lịch sử của %s (%s) — bỏ qua luật chống "
                       "nộp trùng.", email, e)
        return False

    record["approved_courses"] = sorted(approved)
    record["result"] = "TRÙNG" if course in approved else "không trùng"
    out.json("01_duplicate.json", record)
    return course in approved


# Như run.scan_certificate, nhưng bọc ba hàm gọi model để ghi lại kết quả
# trung gian của từng tầng. pipeline.process nuốt lỗi của chúng, nên phải ghi
# trước khi ném tiếp.
def scan_with_trace(image_bytes: bytes, info: dict, ocr_client,
                    out: StepWriter) -> ProcessResult:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as tmp:
        tmp.write(image_bytes)
        tmp_path = tmp.name
    try:
        images = file_utils.read_as_images(tmp_path)
    except file_utils.InvalidFileError as e:
        out.json("03_images.json", {"error": str(e)})
        return ProcessResult(employee_code=info.get("employeeId"),
                             verdict=Verdict.REJECTED,
                             reason=f"File không hợp lệ: {e}", stage="file_error")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    pages = []
    for n, page in enumerate(images, start=1):
        name = f"page_{n}{archive._file_extension(page)}"
        out.bytes(name, page)
        pages.append({"file": name, "bytes": len(page)})
    out.json("03_images.json", {"page_count": len(images), "pages": pages,
                                "llm1_reads_page": 1})

    def traced(step_file: str, func, dump):
        def wrapper(*args):
            started = time.monotonic()
            try:
                value = func(*args)
            except Exception as e:
                out.json(step_file, {"error": f"{type(e).__name__}: {e}",
                                     "seconds": round(time.monotonic() - started, 2)})
                raise
            dump(value, round(time.monotonic() - started, 2))
            return value
        return wrapper

    def dump_extracted(step_file):
        return lambda value, secs: out.json(
            step_file, {"seconds": secs, "extracted": value})

    def dump_ocr(text, secs):
        out.text("05_ocr.txt", text)
        logger.info("OCR xong sau %.1fs (%d ký tự).", secs, len(text))

    match_code = code_from_email(info.get("employeeEmail"))
    if not match_code:
        logger.warning("Không lấy được mã đối chiếu từ employeeEmail=%r.",
                       info.get("employeeEmail"))
    given = InputInfo(employee_name=info.get("employeeName") or "",
                      course_name=info.get("courseName") or "",
                      employee_code=match_code)

    return pipeline.process(
        images=images,
        given=given,
        extract_from_image=traced("04_llm1.json", llm_vision.extract_from_image,
                                  dump_extracted("04_llm1.json")),
        ocr_images=traced("05_ocr_error.json", ocr.ocr_images, dump_ocr),
        extract_from_text=traced("06_llm2.json", llm_text.extract_from_text,
                                 dump_extracted("06_llm2.json")),
        ocr_client=ocr_client,
        detect_course_list=(
            traced("04a_course_list.json", llm_vision.detect_course_list,
                   dump_extracted("04a_course_list.json"))
            if settings.multi_course_check else None),
    )


def would_submit(out: StepWriter, result: ProcessResult, info: dict) -> dict:
    dto = run.build_result_dto(result, info)
    out.json("08_submit.json", {"sent": False,
                                "note": "DRY RUN — payload API ③ KHÔNG được gửi",
                                "payload": dto})
    return dto


# Gọi API ③ THẬT cho MỘT ca APPROVED (--submit-approved). Không đi qua
# run.submit_result vì hàm đó ghi DB và bộ đếm cảnh báo.
# Trả về (eLIS đã nhận?, message).
def submit_approved(out: StepWriter, result: ProcessResult, info: dict) -> tuple[bool, str | None]:
    dto = run.build_result_dto(result, info)
    record = {"sent": True, "payload": dto}
    try:
        data = run.call_with_retry(_REAL_UPDATE_STATUS, [dto])
    except client.ElisError as e:
        record.update(accepted=False, error=str(e))
        out.json("08_submit.json", record)
        logger.error("API ③ nộp APPROVED cho %s THẤT BẠI: %s", dto["id"], e)
        return False, f"Không gửi được: {e}"

    succeeded = data.get("successList") or []
    failed = data.get("failList") or []
    message = "; ".join(str(f.get("message") or "") for f in failed) or None
    record.update(accepted=bool(succeeded), response=data)
    out.json("08_submit.json", record)
    if failed:
        logger.warning("eLIS từ chối id=%s: %s", dto["id"], message)
    return bool(succeeded), message


def handle_one(info: dict, ocr_client, position: int, total: int,
               folder: Path, approved_this_round: set,
               send_approved: bool = False) -> CertReport:
    uc_id = info["id"]
    out = StepWriter(folder)
    handler = attach_file_log(folder / "steps.log")
    started = time.monotonic()
    report = CertReport(position=position, id=uc_id,
                        employeeName=info.get("employeeName"),
                        employeeEmail=info.get("employeeEmail"),
                        courseName=info.get("courseName"),
                        providerName=info.get("providerName"),
                        folder=folder.name)

    def finish(verdict, stage, reason, submit, technical=False):
        report.update(verdict=verdict, stage=stage, reason=reason,
                      would_submit=submit, technical=technical,
                      seconds=round(time.monotonic() - started, 1))
        if report.get("elis_sent"):
            sent = ("ĐÃ GỬI THẬT, eLIS nhận" if report.get("elis_accepted")
                    else f"ĐÃ GỬI THẬT, eLIS KHÔNG nhận ({report.get('elis_message')})")
        else:
            sent = "CÓ (chỉ ghi file)" if submit else "không"
        logger.info("[%d/%d] [%s] %s | %s | stage: %s | nộp API ③: %s",
                    position, total, verdict, info.get("employeeName") or "?",
                    reason or "-", stage, sent)
        return report

    try:
        out.json("00_info.json", info)

        # ---- Bước 0: nhà cung cấp bị loại ----
        if run.is_skipped_provider(info.get("providerName")):
            reason = f"Nhà cung cấp {info.get('providerName')!r} không xử lý tự động"
            out.json("07_result.json", {"result": {
                "verdict": Verdict.WAITING.value,
                "stage": database.SKIP_PROVIDER_STAGE, "reason": reason},
                "skip": True})
            out.json("08_submit.json", {"sent": False,
                                        "note": "Ca BỎ QUA (nhà cung cấp bị loại) "
                                                "— job thật để WAITING"})
            return finish(Verdict.WAITING.value, database.SKIP_PROVIDER_STAGE,
                          reason, False)

        # ---- Bước 0: link khóa học ----
        if not run.is_valid_course_link(info.get("courseLink")):
            reason = f"Link khóa học không hợp lệ ({info.get('courseLink')!r})"
            out.json("07_result.json", {"result": {
                "verdict": Verdict.WAITING.value,
                "stage": database.SKIP_INVALID_LINK_STAGE, "reason": reason},
                "skip": True})
            out.json("08_submit.json", {"sent": False,
                                        "note": "Ca BỎ QUA (link khóa học không hợp lệ) "
                                                "— job thật để WAITING"})
            return finish(Verdict.WAITING.value, database.SKIP_INVALID_LINK_STAGE,
                          reason, False)

        # ---- Bước 1: chống nộp trùng ----
        try:
            duplicate = check_duplicate(info, approved_this_round, out)
        except client.ElisError as e:
            out.json("08_submit.json", {"sent": False,
                                        "note": "Hỏng kỹ thuật — job thật để WAITING"})
            return finish(Verdict.WAITING.value, "duplicate_check_error",
                          f"Không tra được lịch sử trên eLIS: {e}", False, True)
        if duplicate:
            result = ProcessResult(verdict=Verdict.REJECTED,
                                   reason="Cán bộ nộp trùng khóa học",
                                   stage="duplicate")
            out.json("07_result.json", result)
            would_submit(out, result, info)
            return finish(result.verdict.value, result.stage, result.reason, True)

        # ---- Bước 2: API ② tải file ----
        pair = [{"UserCourseId": uc_id, "certificate_id": info["certificate_id"]}]
        try:
            files = run.call_with_retry(client.download_certificates, pair)
        except client.ElisError as e:
            out.json("02_download.json", {"request": pair, "error": str(e)})
            return finish(Verdict.WAITING.value, "download_error",
                          f"Không tải được file từ eLIS: {e}", False, True)

        downloaded = next((f for f in files if f["userCourseId"] == uc_id), None)
        if downloaded is None:
            out.json("02_download.json", {
                "request": pair, "error": "eLIS không trả về file cho chứng chỉ này",
                "returned_ids": [f.get("userCourseId") for f in files]})
            return finish(Verdict.WAITING.value, "no_file",
                          "eLIS không trả về file cho chứng chỉ này", False, True)

        content = downloaded["anh_bytes"]
        cert_file = f"certificate{archive._file_extension(content)}"
        out.bytes(cert_file, content)
        out.json("02_download.json", {"request": pair, "saved_as": cert_file,
                                      "bytes": len(content),
                                      "filename_from_elis": downloaded.get("ten_file"),
                                      "certificate_id": downloaded.get("certificate_id"),
                                      "unsupported_mime": downloaded.get("unsupported_mime")})

        if downloaded.get("unsupported_mime"):
            reason = (f"File định dạng không hỗ trợ ({downloaded['unsupported_mime']})")
            out.json("07_result.json", {"result": {
                "verdict": Verdict.WAITING.value,
                "stage": database.SKIP_UNSUPPORTED_FILE_STAGE, "reason": reason},
                "skip": True})
            out.json("08_submit.json", {"sent": False,
                                        "note": "Ca BỎ QUA (file sai định dạng) — job "
                                                "thật để WAITING, không gọi LLM"})
            return finish(Verdict.WAITING.value, database.SKIP_UNSUPPORTED_FILE_STAGE,
                          reason, False)

        # ---- Bước 3: pipeline ----
        result = scan_with_trace(content, info, ocr_client, out)
        technical = run.is_technical_failure(result)
        out.json("07_result.json", {"result": result, "technical_failure": technical,
                                    "skip": run.is_skip(result)})

        if technical:
            out.json("08_submit.json", {"sent": False,
                                        "note": "Hỏng kỹ thuật — job thật để WAITING, "
                                                "dừng vòng, thử lại sau giãn cách"})
            return finish(Verdict.WAITING.value, result.stage, result.reason, False, True)

        if run.is_skip(result):
            out.json("08_submit.json", {"sent": False,
                                        "note": "Ca BỎ QUA — job thật để WAITING, "
                                                "chờ người duyệt"})
            return finish(Verdict.WAITING.value, result.stage, result.reason, False)

        # ---- Bước 4: API ③ — nộp thật ca APPROVED nếu bật cờ, còn lại chỉ dựng payload ----
        approved = result.verdict == Verdict.APPROVED
        if approved and send_approved:
            accepted, message = submit_approved(out, result, info)
            report.update(elis_sent=True, elis_accepted=accepted, elis_message=message)
        else:
            would_submit(out, result, info)
            # Chạy thử giả định eLIS nhận.
            accepted = approved
        if approved and accepted:
            # Như job thật: chỉ nhớ khi eLIS nhận.
            approved_this_round.add((
                str(info.get("employeeEmail") or "").strip().lower(),
                normalize(info.get("courseName"))))
        return finish(result.verdict.value, result.stage, result.reason, True)

    except Exception as e:
        logger.exception("[%d/%d] Lỗi không lường trước với %s: %s",
                         position, total, uc_id, e)
        out.json("99_crash.json", {"error": f"{type(e).__name__}: {e}"})
        return finish("CRASH", "dry_run_crash", str(e), False, True)
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()


#5. Tổng hợp và điểm vào

SUMMARY_COLUMNS = ("position", "id", "employeeName", "employeeEmail", "courseName",
                   "providerName", "verdict", "stage", "reason", "would_submit",
                   "elis_sent", "elis_accepted", "elis_message",
                   "technical", "seconds", "folder")


def write_summary(out: StepWriter, reports: list[CertReport], meta: dict) -> None:
    counts = {}
    for r in reports:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    meta["counts"] = counts
    meta["would_submit"] = sum(1 for r in reports if r.get("would_submit"))
    meta["elis_sent"] = sum(1 for r in reports if r.get("elis_sent"))
    meta["elis_accepted"] = sum(1 for r in reports if r.get("elis_accepted"))
    out.json("summary.json", {"meta": meta, "certificates": reports})

    with open(out.folder / "summary.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(reports)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Chạy thử job duyệt chứng chỉ — KHÔNG nộp kết quả về eLIS.")
    p.add_argument("--limit", type=int, default=None,
                   help="Chỉ xử lý N chứng chỉ đầu (sau khi lọc)")
    p.add_argument("--id", dest="ids", action="append", default=[],
                   help="Chỉ xử lý user_course_id này (lặp lại được)")
    p.add_argument("--ignore-cooldown", action="store_true",
                   help="Bỏ qua giãn cách thử lại, như `run.py retry`")
    p.add_argument("--keep-going", action="store_true",
                   help="Gặp hỏng kỹ thuật vẫn chạy tiếp (job thật sẽ dừng vòng)")
    p.add_argument("--out", default="dry_run_logs",
                   help="Thư mục gốc chứa log (mặc định: dry_run_logs)")
    p.add_argument("--submit-approved", action="store_true",
                   help="GỌI API ③ THẬT cho các ca APPROVED (ca khác vẫn chỉ ghi file)")
    p.add_argument("--yes", action="store_true",
                   help="Bỏ bước xác nhận của --submit-approved")
    return p.parse_args()


# Nộp thật là không quay lại được: bản ghi rời WAITING trên eLIS. Bắt gõ xác
# nhận, trừ khi có --yes (chạy không có terminal thì bắt buộc --yes).
def confirm_submit() -> bool:
    print(f"\n!!! --submit-approved: ca APPROVED sẽ được NỘP THẬT lên "
          f"{settings.elis_base_url}\n    Thao tác không hoàn tác được.")
    if not sys.stdin.isatty():
        print("Không có terminal để xác nhận — thêm --yes nếu chắc chắn.")
        return False
    return input("Gõ 'yes' để tiếp tục: ").strip().lower() == "yes"


def main() -> int:
    args = parse_args()
    if args.submit_approved and not args.yes and not confirm_submit():
        print("Đã hủy.")
        return 1
    started_at = datetime.now()
    run_dir = (PROJECT_ROOT / args.out / started_at.strftime("%Y%m%d_%H%M%S"))
    out = StepWriter(run_dir)
    attach_file_log(run_dir / "run.log")
    lock_side_effects()

    if args.submit_approved:
        logger.warning("DRY RUN + --submit-approved — ca APPROVED được NỘP THẬT qua "
                       "API ③; ca khác không nộp. Không ghi DB, không gửi email.")
    else:
        logger.info("DRY RUN — không nộp API ③, không ghi DB, không gửi email.")
    logger.info("Log lưu tại: %s", run_dir)
    meta = {"started_at": started_at.isoformat(timespec="seconds"),
            "elis_base_url": settings.elis_base_url,
            "elis_file_base_url": settings.elis_file_base_url,
            "fpt_model": settings.fpt_model,
            "course_match_mode": settings.course_match_mode,
            "valid_from": settings.valid_from, "valid_to": settings.valid_to,
            "duplicate_check": settings.duplicate_check,
            "args": vars(args)}

    # ---- API ①: hàng đợi ----
    try:
        items = run.call_with_retry(client.get_pending_list, page=1, size=100)
    except client.ElisError as e:
        logger.error("API ① getCert THẤT BẠI: %s", e)
        out.json("01_queue.json", {"error": str(e)})
        meta["error"] = f"API ①: {e}"
        write_summary(out, [], meta)
        return 1
    out.json("01_queue.json", {"count": len(items), "items": items})
    logger.info("API ① trả về %d chứng chỉ WAITING.", len(items))

    if args.ids:
        wanted = {str(i) for i in args.ids}
        items = [i for i in items if str(i["id"]) in wanted]
        missing = wanted - {str(i["id"]) for i in items}
        if missing:
            logger.warning("Không có trong hàng đợi WAITING: %s", ", ".join(sorted(missing)))

    ready, decisions = filter_queue(items, args.ignore_cooldown)
    if args.limit is not None:
        ready = ready[:max(0, args.limit)]
    out.json("02_filter.json", {"ready_count": len(ready), "decisions": decisions})
    logger.info("Sẽ xử lý %d chứng chỉ (lọc bỏ %d).", len(ready), len(items) - len(ready))

    reports: list[CertReport] = []
    if ready:
        ocr_client = ocr.create_client()
        approved_this_round: set[tuple[str, str]] = set()
        for position, info in enumerate(ready, start=1):
            folder = run_dir / "certs" / f"{position:02d}_{_safe_name(info['id'])}"
            report = handle_one(info, ocr_client, position, len(ready),
                                folder, approved_this_round, args.submit_approved)
            reports.append(report)
            if report.technical and not args.keep_going:
                logger.info("DỪNG tại %s — hỏng kỹ thuật (job thật cũng dừng vòng "
                            "ở đây). Dùng --keep-going để chạy tiếp.",
                            info.get("employeeName") or info["id"])
                meta["stopped_at"] = info["id"]
                break

    meta["finished_at"] = datetime.now().isoformat(timespec="seconds")
    write_summary(out, reports, meta)
    logger.info("Xong. %d chứng chỉ đã chạy, %d sẽ được nộp nếu chạy thật, "
                "%d đã nộp thật (eLIS nhận %d). Xem %s",
                len(reports), meta["would_submit"], meta["elis_sent"],
                meta["elis_accepted"], run_dir / "summary.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
