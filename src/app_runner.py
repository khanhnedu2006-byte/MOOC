"""Phần chạy job của app desktop (app_runner) — KHÔNG có giao diện.

Tách khỏi main_app.py vì đúng một lý do: main_app.py `import tkinter`, mà máy
CI Linux thường không cài python3-tk. Để chung thì cả bộ test không import nổi
file này, và phần dễ sai nhất của app — chỗ tự gọi API ① rồi phải tự ghi sổ
cho alert — sẽ không có test nào canh.

Ở đây không có một lời gọi Tk nào. Luồng nền chỉ ghi vào SharedState; phía
giao diện đọc lại bằng snapshot().
"""

import logging
import threading
import time

import alert
import client
import ocr_azure
import run
import scheduler
from config import settings
from database import database

logger = logging.getLogger("app")


class SharedState:
    """Ô nhớ dùng chung giữa luồng nền và luồng vẽ. Có khóa, đọc ra bản sao."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data = {
            "phase": "starting",     # starting | running | paused | sleeping | error
            "round_index": 0,
            "queue_size": 0,
            "employee_count": 0,
            "scanned_total": 0,
            "accepted_total": 0,
            "rejected_total": 0,      # tính TỪ KHI MỞ APP, xem _one_round
            "skipped_now": 0,
            "deferred_now": 0,
            "llm_calls_total": 0,
            "last_round": (0, 0, 0),   # quét, eLIS nhận, hoãn
            "next_round_at": 0.0,
            "started_at": time.time(),
            "api_error": None,
            "fatal": None,
        }

    def update(self, **fields) -> None:
        with self._lock:
            self._data.update(fields)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._data)


# ----------------------------------------------------------- 4. Luồng chạy

class JobRunner(threading.Thread):
    """Chạy `run.process_one_round` lặp lại, có tạm dừng và đánh thức."""

    def __init__(self, state: SharedState):
        super().__init__(daemon=True, name="mooc-job")
        self.state = state
        self.resume_event = threading.Event()   # đặt = đang chạy
        self.resume_event.set()
        self.wake_event = threading.Event()     # bỏ qua phần nghỉ, chạy ngay
        self.stop_event = threading.Event()
        self.ignore_cooldown_once = False
        self.rejected_at_start = 0

    # -- điều khiển từ giao diện (an toàn khi gọi từ luồng chính) --
    def pause(self) -> None:
        self.resume_event.clear()
        self.state.update(phase="paused")

    def resume(self) -> None:
        self.resume_event.set()
        self.wake_event.set()

    def run_now(self, ignore_cooldown: bool = False) -> None:
        self.ignore_cooldown_once = ignore_cooldown
        self.wake_event.set()

    def stop(self) -> None:
        self.stop_event.set()
        self.resume_event.set()
        self.wake_event.set()

    # -- vòng đời luồng --
    def run(self) -> None:
        try:
            azure_client = ocr_azure.create_client()
        except Exception as e:
            # Sai key Azure là hỏng ngay từ đầu; báo lên giao diện rồi dừng
            # hẳn, đừng lặp vô ích mỗi 5 giây.
            logger.exception("Không tạo được client Azure: %s", e)
            self.state.update(phase="error", fatal=str(e))
            return

        # Mốc đầu phiên. Không có nó thì ô "Từ chối" đếm cả lịch sử trong DB
        # (hàng trăm) trong khi ô "eLIS đã nhận" ngay bên cạnh chỉ đếm từ lúc
        # mở app — hai con số cạnh nhau mà mốc thời gian khác nhau thì người
        # đọc trừ nhẩm ra kết luận sai.
        try:
            self.rejected_at_start = database.count_by_verdict().get("REJECTED", 0)
        except Exception:
            self.rejected_at_start = 0

        logger.info("Đã khởi động. Chu kỳ %d giây.",
                    max(1, settings.poll_interval_seconds))

        while not self.stop_event.is_set():
            self.resume_event.wait()
            if self.stop_event.is_set():
                break

            ignore_cooldown = self.ignore_cooldown_once
            self.ignore_cooldown_once = False
            try:
                self._one_round(azure_client, ignore_cooldown)
            except Exception as e:
                logger.exception("Lỗi trong vòng xử lý: %s", e)

            self._sleep_between_rounds()

    def _one_round(self, azure_client, ignore_cooldown: bool) -> None:
        snapshot = self.state.snapshot()
        self.state.update(phase="running",
                          round_index=snapshot["round_index"] + 1)

        # Tự gọi API ① thay vì để process_one_round gọi, chỉ vì một lý do:
        # giao diện cần biết hàng đợi dài bao nhiêu, mà RoundResult không nói.
        # Đổi lại phải tự ghi sổ thành/bại cho alert — đúng như run.py làm.
        try:
            items = run.call_with_retry(client.get_pending_list, page=1, size=100)
        except client.ElisError as e:
            alert.api_failed(1, str(e))
            logger.error("API ① getCert THẤT BẠI: %s — không lấy được hàng đợi "
                         "nên vòng này không xử lý gì.", e)
            self.state.update(api_error=str(e), queue_size=0,
                              employee_count=0, phase="error")
            return
        alert.api_succeeded(1)

        emails = {str(i.get("employeeEmail") or "").lower() for i in items}
        emails.discard("")
        self.state.update(api_error=None,
                          queue_size=len(items),
                          employee_count=len(emails))

        try:
            skipped = database.skipped_ids([i["id"] for i in items])
        except Exception:
            skipped = set()
        self.state.update(skipped_now=len(skipped))

        result = run.process_one_round(azure_client, items=items,
                                       ignore_cooldown=ignore_cooldown)

        counts = {}
        try:
            counts = database.count_by_verdict()
        except Exception:
            pass

        snapshot = self.state.snapshot()
        self.state.update(
            scanned_total=snapshot["scanned_total"] + result.scanned_count,
            accepted_total=snapshot["accepted_total"] + result.accepted_count,
            rejected_total=(max(0, counts["REJECTED"] - self.rejected_at_start)
                            if "REJECTED" in counts
                            else snapshot["rejected_total"]),
            deferred_now=result.deferred_count,
            last_round=(result.scanned_count, result.accepted_count,
                        result.deferred_count),
        )

        # Lịch báo cáo đi kèm vòng chạy, y như run_forever.
        try:
            scheduler.check_and_send()
        except Exception as e:
            logger.exception("Lỗi lịch báo cáo (không chặn xử lý): %s", e)

    def _sleep_between_rounds(self) -> None:
        seconds = max(1, settings.poll_interval_seconds)
        self.state.update(phase="sleeping", next_round_at=time.time() + seconds)
        # wait() thay cho sleep() để nút "Chạy vòng ngay" có tác dụng tức thì.
        self.wake_event.wait(timeout=seconds)
        self.wake_event.clear()


