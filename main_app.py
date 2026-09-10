"""MOOC Console — vỏ desktop cho job xử lý chứng chỉ.

Chạy: python main_app.py

App này KHÔNG chứa luật nghiệp vụ nào. Mọi phán quyết vẫn do run.py đưa ra;
ở đây chỉ có ba việc:
    1. Chạy vòng xử lý trong một luồng nền (thay cho `python run.py loop`).
    2. Bày ra các con số của mỗi vòng.
    3. Thu nhật ký logging vào cửa sổ, và thu nhỏ xuống khay thay vì thoát.

Việc 2 KHÔNG phải chỉ đọc lại RoundResult. Chỉ hai ô số lấy được từ đó
(eLIS nhận, đang hoãn); ba ô còn lại app tự đi hỏi — xem _one_round(). Hệ quả
là app tự gọi API ① và do đó phải tự ghi sổ alert.api_failed/api_succeeded.
Sửa phần đó mà quên ghi sổ thì email cảnh báo sẽ sai.

Nguyên tắc bắt buộc khi sửa file này: MỌI thao tác chạm vào widget Tk đều
phải xảy ra trong luồng chính. Luồng nền chỉ được ghi vào `SharedState` và
`queue.Queue`; luồng chính đọc lại mỗi 250ms trong `_tick`. Gọi widget từ
luồng nền là kiểu lỗi treo cứng không có traceback, rất khó lần ra.
"""

import os
# PHẢI đứng trước mọi import nặng (torch/onnx do Azure SDK kéo theo). Trên
# Windows + conda, thiếu dòng này là OMP Error #15 lúc khởi động.
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import logging
import queue
import socket
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import vault
import settings_file
from app_runner import JobRunner, SharedState
from config import Settings, settings
from database import database

logger = logging.getLogger("app")

APP_NAME = "MOOC Console"
# Cổng loopback chỉ dùng làm khóa chống chạy hai bản, không nghe lệnh gì.
LOCK_PORT = 47615
UI_REFRESH_MS = 250
LOG_LINES_KEPT = 600


# ---------------------------------------------------------------- 1. Khóa

def acquire_single_instance_lock() -> socket.socket | None:
    """Giữ chỗ để bản thứ hai không chạy được. None = đã có bản đang chạy.

    Hai bản cùng poll một hàng đợi nghĩa là mỗi chứng chỉ tốn hai lượt LLM, và
    tệ hơn: hai bản cùng nộp kết quả cho một bản ghi eLIS.

    Dùng socket chứ không dùng file khóa vì file khóa còn nguyên sau khi app
    bị kill — lần mở sau tưởng có bản đang chạy dù không có. Socket thì hệ
    điều hành tự thu khi tiến trình chết. Cố ý KHÔNG đặt SO_REUSEADDR.
    """
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        holder.bind(("127.0.0.1", LOCK_PORT))
    except OSError:
        holder.close()
        return None
    holder.listen(1)
    return holder


# ------------------------------------------------------- 2. Log vào cửa sổ

class QueueLogHandler(logging.Handler):
    """Đẩy mỗi dòng log vào hàng đợi để luồng chính vẽ ra.

    Không đổi một dòng logger nào trong run.py: gắn thêm handler ở đây là đủ,
    nên chạy bằng `python run.py loop` vẫn cho ra đúng nhật ký như cũ.
    """

    def __init__(self, sink: queue.Queue):
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.sink.put_nowait((record.levelno, self.format(record)))
        except queue.Full:
            # Thà mất dòng log còn hơn chặn luồng xử lý chứng chỉ.
            pass


# ------------------------------------------------------------ 5. Cửa sổ

PHASE_TEXT = {
    "starting": ("Đang khởi động", "#96601A"),
    "running": ("Đang chạy", "#1F7A46"),
    "sleeping": ("Đang chờ vòng sau", "#1F7A46"),
    "paused": ("Đã tạm dừng", "#96601A"),
    "error": ("Có sự cố", "#A32F27"),
}

LOG_COLORS = {
    logging.ERROR: "#F09189",
    logging.CRITICAL: "#F09189",
    logging.WARNING: "#E5B45C",
}


def _scrollable(parent):
    """Vùng cuộn được. 31 ô cấu hình không vừa một màn hình laptop."""
    canvas = tk.Canvas(parent, borderwidth=0, highlightthickness=0)
    bar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    body = ttk.Frame(canvas)

    window = canvas.create_window((0, 0), window=body, anchor="nw")
    canvas.configure(yscrollcommand=bar.set)
    body.bind("<Configure>",
              lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    # Không có dòng này thì khung con giữ nguyên bề rộng tự nhiên, các ô nhập
    # bị nén về bên trái dù cửa sổ đã kéo rộng.
    canvas.bind("<Configure>",
                lambda e: canvas.itemconfigure(window, width=e.width))
    # Chỉ bắt con lăn KHI CHUỘT ĐANG Ở TRONG vùng này. bind_all vô điều kiện
    # sẽ cướp con lăn của cả tab Nhật ký và tab Ca bỏ qua — cuộn ở đó lại làm
    # nhảy trang cấu hình.
    def _wheel(event):
        canvas.yview_scroll(-event.delta // 120, "units")

    canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _wheel))
    canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

    bar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    return body, canvas


def _editor_for(box, field: str, info):
    """Ô nhập hợp với KIỂU của trường, trả về (biến, widget)."""
    if info.annotation is bool:
        var = tk.BooleanVar()
        return var, ttk.Checkbutton(box, variable=var, text="bật")
    var = tk.StringVar()
    if field in settings_file.CONFIG_CHOICES:
        return var, ttk.Combobox(box, textvariable=var, width=22,
                                 state="readonly", values=settings_file.CONFIG_CHOICES[field])
    rong = 18 if info.annotation in (int, float) else 46
    return var, ttk.Entry(box, textvariable=var, width=rong)


class ConsoleWindow(tk.Tk):
    def __init__(self, state: SharedState, log_queue: queue.Queue,
                 runner: JobRunner | None):
        super().__init__()
        self.state_store = state
        self.log_queue = log_queue
        self.runner = runner
        self.tray_icon = None
        self.alive = True

        # Lệnh từ luồng khay gửi sang. KHÔNG dùng window.after() cho việc này:
        # Tcl/Tk không an toàn đa luồng, gọi after() từ luồng pystray có lúc
        # chạy êm, có lúc làm sập tiến trình mà không để lại traceback. Đẩy
        # hàm vào hàng đợi rồi để _tick (đang ở luồng chính) gọi hộ.
        self.ui_queue: queue.Queue = queue.Queue()

        self.title(APP_NAME)
        self.geometry("980x660")
        self.minsize(820, 560)
        try:
            ttk.Style(self).theme_use("clam")
        except tk.TclError:
            pass

        self._build_header()
        self._build_tabs()
        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.after(UI_REFRESH_MS, self._tick)

    # -- dựng giao diện --
    def _build_header(self) -> None:
        bar = ttk.Frame(self, padding=(12, 8))
        bar.pack(fill="x")
        self.lbl_phase = ttk.Label(bar, text="Đang khởi động",
                                   font=("Segoe UI", 10, "bold"))
        self.lbl_phase.pack(side="left")
        self.lbl_meta = ttk.Label(bar, text="", foreground="#6C7681")
        self.lbl_meta.pack(side="left", padx=14)
        self.lbl_env = ttk.Label(bar, text=settings.elis_base_url,
                                 foreground="#6C7681")
        self.lbl_env.pack(side="right")

    def _build_tabs(self) -> None:
        tabs = ttk.Notebook(self)
        tabs.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        tabs.add(self._build_dashboard(tabs), text="  Bảng điều khiển  ")
        tabs.add(self._build_skip_tab(tabs), text="  Ca bỏ qua  ")
        tabs.add(self._build_config_tab(tabs), text="  Cấu hình  ")

    def _build_dashboard(self, parent) -> ttk.Frame:
        page = ttk.Frame(parent, padding=10)

        kpis = ttk.Frame(page)
        kpis.pack(fill="x")
        self.kpi_values = {}
        # Dòng phụ dưới mỗi số là BẮT BUỘC, không phải trang trí: năm ô này có
        # ba mốc thời gian khác nhau (đang có / từ khi mở app / vòng vừa rồi).
        # Bỏ dòng phụ đi là mời người đọc cộng trừ hai số không cùng mốc.
        spec = [
            ("queue", "Hàng đợi", "đang chờ ở eLIS"),
            ("accepted", "eLIS đã nhận", "từ khi mở app"),
            ("rejected", "Từ chối", "từ khi mở app"),
            ("skipped", "Bỏ qua", "đang trong hàng đợi"),
            ("deferred", "Đang hoãn", "vòng vừa rồi"),
        ]
        for column, (key, label, caption) in enumerate(spec):
            tile = ttk.LabelFrame(kpis, text=label, padding=(12, 6))
            tile.grid(row=0, column=column, sticky="ew", padx=(0, 8))
            kpis.columnconfigure(column, weight=1)
            value = ttk.Label(tile, text="—", font=("Segoe UI", 20, "bold"))
            value.pack(anchor="w")
            ttk.Label(tile, text=caption, foreground="#6C7681").pack(anchor="w")
            self.kpi_values[key] = value

        log_frame = ttk.LabelFrame(page, text="Nhật ký", padding=6)
        log_frame.pack(fill="both", expand=True, pady=(12, 0))
        self.log_view = tk.Text(log_frame, wrap="none", height=18,
                                background="#14181E", foreground="#C6CEDA",
                                insertbackground="#C6CEDA", borderwidth=0,
                                font=("Consolas", 9))
        scroll = ttk.Scrollbar(log_frame, orient="vertical",
                               command=self.log_view.yview)
        self.log_view.configure(yscrollcommand=scroll.set, state="disabled")
        scroll.pack(side="right", fill="y")
        self.log_view.pack(side="left", fill="both", expand=True)
        for level, color in LOG_COLORS.items():
            self.log_view.tag_configure(f"lv{level}", foreground=color)

        buttons = ttk.Frame(page)
        buttons.pack(fill="x", pady=(10, 0))
        self.btn_pause = ttk.Button(buttons, text="Tạm dừng",
                                    command=self.toggle_pause)
        self.btn_pause.pack(side="left")
        ttk.Button(buttons, text="Chạy vòng ngay",
                   command=lambda: self.runner_call("run_now")).pack(
                       side="left", padx=6)
        ttk.Button(buttons, text="Thử lại ca đang hoãn",
                   command=lambda: self.runner_call("run_now", True)).pack(
                       side="left")
        ttk.Button(buttons, text="Mở thư mục log",
                   command=self.open_log_folder).pack(side="left", padx=6)
        return page

    def _build_skip_tab(self, parent) -> ttk.Frame:
        page = ttk.Frame(parent, padding=10)
        ttk.Label(page, wraplength=900, foreground="#6C7681",
                  text="Chứng chỉ đọc được nhưng không xác minh được danh tính "
                       "người học. Hệ thống để nguyên WAITING và không quét lại "
                       "— người duyệt xử lý thẳng trên eLIS.").pack(
                           anchor="w", pady=(0, 8))
        columns = ("time", "name", "course", "reason")
        self.skip_view = ttk.Treeview(page, columns=columns, show="headings",
                                      height=16)
        for key, title, width in (("time", "Lúc", 130),
                                  ("name", "Tên trên ảnh", 180),
                                  ("course", "Khóa học", 220),
                                  ("reason", "Lý do", 380)):
            self.skip_view.heading(key, text=title)
            self.skip_view.column(key, width=width, anchor="w")
        self.skip_view.pack(fill="both", expand=True)
        ttk.Button(page, text="Nạp lại", command=self.reload_skipped).pack(
            anchor="w", pady=(8, 0))
        return page

    def _build_config_tab(self, parent) -> ttk.Frame:
        page = ttk.Frame(parent, padding=(10, 10, 10, 0))

        # Thanh nút dựng và pack TRƯỚC vùng cuộn. Đây không phải chuyện thẩm
        # mỹ: pack cấp chỗ theo thứ tự gọi, nên nếu vùng cuộn (expand=True)
        # đi trước, nó ăn hết chiều cao và thanh nút bị đẩy ra khỏi cửa sổ.
        bar = ttk.Frame(page)
        bar.pack(fill="x", side="bottom", pady=(8, 10))
        ttk.Button(bar, text="Lưu thay đổi", command=self.save_config).pack(
            side="left")
        ttk.Button(bar, text="Hoàn tác", command=self.reload_config).pack(
            side="left", padx=6)
        self.lbl_config_note = ttk.Label(bar, text="", foreground="#6C7681")
        self.lbl_config_note.pack(side="left", padx=10)

        body, _canvas = _scrollable(page)
        self._build_secret_box(body)
        self._build_editors(body)
        return page

    def _build_editors(self, body) -> None:
        """Dựng ô sửa cho mọi cấu hình không phải khóa bí mật.

        Nhãn tiếng Việt để đọc, TÊN BIẾN .env in bên dưới để tra: người vận
        hành đọc nhãn, còn khi hỏi nhau qua chat thì ai cũng gọi tên biến.
        Chú thích lấy thẳng từ `description` trong config.py — một nguồn duy
        nhất, không có bản mô tả thứ hai để lệch nhau.
        """
        self.config_vars = {}
        for title, fields in settings_file.CONFIG_GROUPS:
            box = ttk.LabelFrame(body, text=title, padding=(12, 8))
            box.pack(fill="x", pady=(0, 8))
            box.columnconfigure(1, weight=1)
            for row, (field, label) in enumerate(fields):
                info = Settings.model_fields[field]
                ttk.Label(box, text=label, width=26).grid(
                    row=row * 2, column=0, sticky="w", pady=(3, 0))
                var, widget = _editor_for(box, field, info)
                widget.grid(row=row * 2, column=1, sticky="w", pady=(3, 0))
                self.config_vars[field] = var

                note = settings_file.env_names(field)[0]
                if info.description:
                    note += f" — {info.description}"
                ttk.Label(box, text=note, foreground="#6C7681").grid(
                    row=row * 2 + 1, column=1, sticky="w", pady=(0, 3))
        self.reload_config()

    def reload_config(self) -> None:
        """Đổ giá trị đang chạy vào các ô. Cũng là nút Hoàn tác."""
        for field, var in self.config_vars.items():
            value = getattr(settings, field)
            var.set(value if isinstance(value, bool) else str(value))
        if getattr(self, "lbl_config_note", None):
            self.lbl_config_note.configure(text="")

    def save_config(self) -> None:
        raw = {field: var.get() for field, var in self.config_vars.items()}
        try:
            changes = settings_file.apply_changes(raw)
        except settings_file.SettingsError as e:
            messagebox.showerror(APP_NAME, str(e), parent=self)
            return

        if not changes:
            self.lbl_config_note.configure(text="Không có gì thay đổi.")
            return

        self.reload_config()
        self.lbl_config_note.configure(
            text=f"Đã lưu {len(changes)} thay đổi.")
        dong = "\n".join(f"  {settings_file.env_names(f)[0]}: {old} → {new}"
                         for f, old, new in changes)
        messagebox.showinfo(
            APP_NAME,
            f"Đã ghi vào .env và áp dụng ngay từ vòng sau:\n\n{dong}\n\n"
            f"Nhật ký thay đổi: config_changes.log", parent=self)

    def _build_secret_box(self, page) -> None:
        """Bốn ô nhập khóa bí mật, ghi thẳng vào Credential Manager.

        CỐ Ý KHÔNG ĐỌC NGƯỢC GIÁ TRỊ RA MÀN HÌNH. Ô nhập luôn rỗng, chỉ có
        dòng chữ bên cạnh nói khóa đang lấy từ đâu. Nhập được, xóa được,
        nhưng không xem lại được — bớt một đường lộ khóa qua ảnh chụp màn
        hình lúc demo, mà cũng chẳng ai cần đọc lại key bao giờ.
        """
        box = ttk.LabelFrame(page, text="Khóa bí mật — kho khóa Windows",
                             padding=(12, 8))
        box.pack(fill="x", pady=(0, 8))
        box.columnconfigure(1, weight=1)

        usable = vault.available()
        stored = vault.describe() if usable else {n: False
                                                  for n in vault.SECRET_NAMES}

        head = ("Nhập một lần, Windows mã hóa theo tài khoản đang đăng nhập. "
                "Đổi khóa xong phải khởi động lại app mới có hiệu lực."
                if usable else
                "KHÔNG DÙNG ĐƯỢC trên máy này — thiếu keyring hoặc không có "
                "backend. Đang chạy bằng .env. Cài bằng: pip install keyring")
        ttk.Label(box, text=head, wraplength=880,
                  foreground="#444D59" if usable else "#A32F27").grid(
                      row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))

        self.secret_entries = {}
        self.secret_origin_labels = {}
        for index, name in enumerate(vault.SECRET_NAMES, start=1):
            ttk.Label(box, text=name, width=18).grid(
                row=index, column=0, sticky="w", pady=2)

            entry = ttk.Entry(box, show="•")
            entry.grid(row=index, column=1, sticky="ew", padx=(0, 8), pady=2)
            self.secret_entries[name] = entry

            actions = ttk.Frame(box)
            actions.grid(row=index, column=2, sticky="w")
            state = "normal" if usable else "disabled"
            ttk.Button(actions, text="Lưu", width=6, state=state,
                       command=lambda n=name: self.save_secret(n)).pack(
                           side="left")
            ttk.Button(actions, text="Xóa", width=6, state=state,
                       command=lambda n=name: self.clear_secret(n)).pack(
                           side="left", padx=4)

            origin = ttk.Label(box, text=_origin_text(name, stored.get(name)),
                               foreground="#6C7681")
            origin.grid(row=index, column=3, sticky="w", padx=(8, 0))
            self.secret_origin_labels[name] = origin

    def _refresh_secret_origins(self) -> None:
        stored = vault.describe() if vault.available() else {}
        for name, label in self.secret_origin_labels.items():
            label.configure(text=_origin_text(name, stored.get(name)))

    def save_secret(self, name: str) -> None:
        entry = self.secret_entries[name]
        try:
            vault.set_secret(name, entry.get())
        except vault.VaultError as e:
            # Không nuốt lỗi: báo "đã lưu" trong khi chưa lưu được là kiểu
            # hỏng tốn cả buổi chiều đi tìm vì sao key vẫn sai.
            messagebox.showerror(APP_NAME, str(e), parent=self)
            return
        entry.delete(0, "end")
        self._refresh_secret_origins()
        messagebox.showinfo(
            APP_NAME,
            f"Đã lưu {name} vào kho khóa Windows.\n\n"
            f"Khởi động lại app mới dùng khóa mới — cấu hình chỉ đọc một lần "
            f"lúc chương trình chạy lên.", parent=self)

    def clear_secret(self, name: str) -> None:
        if not messagebox.askyesno(
                APP_NAME, f"Xóa {name} khỏi kho khóa?\n\nSau đó hệ thống quay "
                          f"lại lấy giá trị trong .env (nếu có).", parent=self):
            return
        try:
            vault.delete_secret(name)
        except vault.VaultError as e:
            messagebox.showerror(APP_NAME, str(e), parent=self)
            return
        self._refresh_secret_origins()

    # -- cập nhật định kỳ, chạy trong luồng chính --
    def post_to_ui(self, action) -> None:
        """Nhờ luồng chính chạy `action`. Gọi được từ bất kỳ luồng nào."""
        self.ui_queue.put(action)

    def _tick(self) -> None:
        if not self.alive:
            return
        try:
            self._drain_ui_queue()
            self._drain_log()
            self._refresh_numbers()
        except tk.TclError:
            # Cửa sổ vừa bị hủy giữa chừng. Không phải lỗi, chỉ là hết việc.
            self.alive = False
            return
        self.after(UI_REFRESH_MS, self._tick)

    def _drain_ui_queue(self) -> None:
        while True:
            try:
                action = self.ui_queue.get_nowait()
            except queue.Empty:
                return
            try:
                action()
            except Exception as e:
                logger.exception("Lỗi khi chạy lệnh từ khay: %s", e)

    def _drain_log(self) -> None:
        lines = []
        while True:
            try:
                lines.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        if not lines:
            return
        self.log_view.configure(state="normal")
        for level, text in lines:
            tag = f"lv{level}" if level in LOG_COLORS else ""
            self.log_view.insert("end", text + "\n", tag)
        # Cắt bớt để cửa sổ chạy cả tuần không phình bộ nhớ.
        total = int(self.log_view.index("end-1c").split(".")[0])
        if total > LOG_LINES_KEPT:
            self.log_view.delete("1.0", f"{total - LOG_LINES_KEPT}.0")
        self.log_view.configure(state="disabled")
        self.log_view.see("end")

    def _refresh_numbers(self) -> None:
        data = self.state_store.snapshot()
        text, color = PHASE_TEXT.get(data["phase"], ("—", "#6C7681"))
        if data["fatal"]:
            text = "Dừng: " + str(data["fatal"])[:60]
            color = "#A32F27"
        self.lbl_phase.configure(text=text, foreground=color)

        uptime = int(time.time() - data["started_at"])
        meta = [f"Vòng #{data['round_index']}",
                f"chạy liên tục {uptime // 3600:02d}:{uptime % 3600 // 60:02d}:"
                f"{uptime % 60:02d}"]
        if data["phase"] == "sleeping":
            left = max(0, int(data["next_round_at"] - time.time()))
            meta.append(f"vòng sau sau {left}s")
        if data["api_error"]:
            meta.append("eLIS không phản hồi")
        self.lbl_meta.configure(text="  ·  ".join(meta))

        self.kpi_values["queue"].configure(
            text=str(data["queue_size"]))
        self.kpi_values["accepted"].configure(text=str(data["accepted_total"]))
        self.kpi_values["rejected"].configure(text=str(data["rejected_total"]))
        self.kpi_values["skipped"].configure(text=str(data["skipped_now"]))
        self.kpi_values["deferred"].configure(text=str(data["deferred_now"]))
        self.btn_pause.configure(
            text="Chạy tiếp" if data["phase"] == "paused" else "Tạm dừng")

    # -- hành động --
    def runner_call(self, method: str, *args) -> None:
        if self.runner is not None:
            getattr(self.runner, method)(*args)

    def toggle_pause(self) -> None:
        if self.runner is None:
            return
        if self.state_store.snapshot()["phase"] == "paused":
            self.runner.resume()
        else:
            self.runner.pause()

    def reload_skipped(self) -> None:
        """Đọc lại danh sách bỏ qua từ mooc_log.db.

        Lấy 400 dòng gần nhất rồi lọc, thay vì thêm câu SQL mới: giữ mọi truy
        vấn ở database.py, app này chỉ đọc.
        """
        self.skip_view.delete(*self.skip_view.get_children())
        try:
            rows = database.read_recent_logs(400)
        except Exception as e:
            logger.warning("Không đọc được nhật ký: %s", e)
            return
        seen = set()
        for row in rows:
            if row.get("stage") != database.SKIP_STAGE:
                continue
            key = row.get("user_course_id")
            if key in seen:
                continue
            seen.add(key)
            self.skip_view.insert("", "end", values=(
                str(row.get("created_at") or "")[:19],
                row.get("name_on_image") or "?",
                (row.get("certificate_name") or "?")[:60],
                (row.get("reason") or "")[:160],
            ))

    def open_log_folder(self) -> None:
        folder = str(PROJECT_ROOT)
        try:
            if sys.platform == "win32":
                os.startfile(folder)          # noqa: S606 — chỉ mở Explorer
            else:
                logger.info("Thư mục log: %s", folder)
        except OSError as e:
            logger.warning("Không mở được thư mục: %s", e)

    def hide_to_tray(self) -> None:
        """Nút X thu nhỏ xuống khay chứ không thoát — job phải chạy tiếp.

        Không có khay (chưa cài pystray) thì X thoát hẳn, vì thu nhỏ vào chỗ
        không nhìn thấy được là cách chắc chắn làm người dùng mất app.
        """
        if self.tray_icon is None:
            self.quit_app()
            return
        self.withdraw()
        logger.info("Đã thu nhỏ xuống khay. Job vẫn đang chạy.")

    def show_window(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    def quit_app(self) -> None:
        # Luồng chạy job là daemon: thoát là nó bị cắt ngang, không kịp dọn.
        # Cắt ngang giữa lúc đang quét thì lượt Gemma + Azure của chứng chỉ đó
        # coi như mất tiền — bản ghi vẫn WAITING nên vòng sau quét lại từ đầu.
        # Không nguy hiểm, nhưng đáng hỏi một câu trước khi đốt.
        if (self.runner is not None
                and self.state_store.snapshot()["phase"] == "running"
                and not messagebox.askyesno(
                    APP_NAME,
                    "Đang quét một chứng chỉ. Thoát bây giờ sẽ bỏ dở lượt quét "
                    "đó — chứng chỉ vẫn WAITING và vòng sau quét lại, nhưng "
                    "lượt LLM vừa gọi thì mất.\n\nVẫn thoát?", parent=self)):
            return

        self.alive = False
        if self.runner is not None:
            self.runner.stop()
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        self.destroy()


def _origin_text(name: str, in_vault: bool | None) -> str:
    """Khóa này đang thật sự lấy từ đâu — theo đúng thứ tự trong config.py.

    Quan trọng hơn vẻ ngoài: nếu ai đó lỡ để FPT_API_KEY trong biến môi
    trường của máy, khóa lưu trong kho sẽ KHÔNG được dùng. Dòng này nói ra
    điều đó thay vì để người dùng ngồi đoán vì sao đổi key không ăn.
    """
    if os.environ.get(name):
        return "← đang dùng biến môi trường"
    if in_vault:
        return "✓ trong kho khóa"
    return "← đang dùng .env"


# --------------------------------------------------------------- 6. Khay

def start_tray(window: ConsoleWindow) -> None:
    """Gắn biểu tượng khay nếu có pystray. Thiếu thì bỏ qua, app vẫn chạy."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        logger.info("Chưa cài pystray — chạy không có biểu tượng khay. "
                    "Cài bằng: pip install pystray")
        return

    image = Image.new("RGB", (64, 64), "#0E6C60")
    ImageDraw.Draw(image).text((20, 18), "M", fill="white")

    # Menu chạy trong luồng riêng của pystray. Mọi việc chạm Tk phải đẩy về
    # luồng chính qua hàng đợi — xem giải thích ở ConsoleWindow.__init__.
    def post(action):
        return lambda *_: window.post_to_ui(action)

    menu = pystray.Menu(
        pystray.MenuItem("Mở cửa sổ", post(window.show_window), default=True),
        pystray.MenuItem("Tạm dừng / chạy tiếp", post(window.toggle_pause)),
        pystray.MenuItem("Chạy vòng ngay",
                         post(lambda: window.runner_call("run_now"))),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Thoát", post(window.quit_app)),
    )
    icon = pystray.Icon("mooc", image, APP_NAME, menu)
    window.tray_icon = icon
    threading.Thread(target=icon.run, daemon=True, name="mooc-tray").start()


# ---------------------------------------------------------------- 7. Vào

def main() -> int:
    self_test = "--self-test" in sys.argv

    lock = acquire_single_instance_lock()
    if lock is None and not self_test:
        print(f"{APP_NAME} đang chạy rồi. Mở bản thứ hai sẽ khiến mỗi chứng "
              f"chỉ tốn hai lượt LLM.")
        return 1

    log_queue: queue.Queue = queue.Queue(maxsize=4000)
    handler = QueueLogHandler(log_queue)
    handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s",
                                           datefmt="%H:%M:%S"))
    logging.getLogger().addHandler(handler)

    state = SharedState()
    runner = None
    if not self_test:
        database.init_db()
        runner = JobRunner(state)

    window = ConsoleWindow(state, log_queue, runner)
    window.reload_skipped()

    if self_test:
        # Dựng cửa sổ rồi bơm vòng sự kiện ~1 giây để _tick chạy thật vài
        # lượt — vẽ log, đổi số, đếm ngược. Dựng được mà _tick lỗi thì vẫn
        # là hỏng, nên không dừng ở bước dựng.
        logger.info("self-test: dòng log thường")
        logger.warning("self-test: dòng cảnh báo")
        logger.error("self-test: dòng lỗi")
        state.update(phase="sleeping", round_index=7, queue_size=59,
                     accepted_total=41, rejected_total=7, skipped_now=2,
                     deferred_now=1, next_round_at=time.time() + 5)
        deadline = time.time() + 1.0
        while time.time() < deadline:
            window.update()
            time.sleep(0.05)
        window.destroy()
        print("self-test OK")
        return 0

    start_tray(window)
    runner.start()
    try:
        window.mainloop()
    except KeyboardInterrupt:
        window.quit_app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
