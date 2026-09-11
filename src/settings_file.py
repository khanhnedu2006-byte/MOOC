"""Sửa cấu hình lúc đang chạy và ghi lại vào .env (settings_file).

Ba phần, thiếu phần nào cũng cho ra một nút "Lưu" nói dối:

  1. KIỂM giá trị mới trước, trên một bản sao.
  2. GHI xuống .env.
  3. GÁN vào object `settings` đang chạy, để có hiệu lực từ vòng kế tiếp mà
     không phải khởi động lại. Làm được vì không chỗ nào trong dự án đọc
     `settings.X` ở mức module; tests/test_settings_file.py canh bằng AST.

Bốn khóa bí mật không đi qua đây — chúng thuộc kho khóa Windows
(src/vault.py).

Ghi đè .env không được làm mất chú thích: file .env có chú thích giải thích
từng tham số. Hàm ở đây sửa đúng chỗ giá trị trên từng dòng, giữ nguyên mọi
thứ còn lại, kể cả chú thích cuối dòng.
"""

import logging
import os
import re
from datetime import datetime
from pathlib import Path

import vault
from config import Settings, settings

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
AUDIT_PATH = PROJECT_ROOT / "config_changes.log"

# Trường bí mật — thuộc kho khóa Windows, không ghi vào .env.
SECRET_FIELDS = frozenset(name.lower() for name in vault.SECRET_NAMES)

# ^(thụt lề)(export )?TÊN(  =  )(giá trị)
_ASSIGN = re.compile(r"^(\s*)(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(.*)$")


# Nhóm cấu hình trên tab Cấu hình. Bốn khóa bí mật không có ở đây — chúng
# thuộc kho khóa Windows, xem main_app._build_secret_box.
# test_settings_file.py canh danh sách này phủ đủ mọi trường sửa được: thêm
# trường vào config.py mà quên thêm vào đây thì nó biến mất khỏi giao diện.
CONFIG_GROUPS = [
    ("Kết nối eLIS", [
        ("elis_base_url", "API nghiệp vụ"),
        ("elis_file_base_url", "Service tải ZIP"),
    ]),
    ("AI", [
        ("fpt_base_url", "Endpoint FPT"),
        ("fpt_model", "Tên model"),
        ("llm_temperature", "Temperature"),
        ("llm_max_tokens", "Giới hạn token"),
        ("azure_endpoint", "Endpoint Azure OCR"),
    ]),
    ("Luật nghiệp vụ", [
        ("course_match_mode", "Cách so tên khóa học"),
        ("valid_from", "Ngày hợp lệ từ"),
        ("valid_to", "Ngày hợp lệ đến"),
        ("duplicate_check", "Chặn nộp trùng khóa"),
    ]),
    ("Vận hành", [
        ("poll_interval_seconds", "Nghỉ giữa hai vòng (giây)"),
        ("retry_count", "Số lần thử lại"),
        ("retry_delay_seconds", "Nghỉ giữa hai lần thử (giây)"),
        ("timeout_seconds", "Timeout gọi API (giây)"),
        ("technical_retry_cooldown_minutes", "Hoãn ca hỏng kỹ thuật (phút)"),
        ("technical_alert_after", "Cảnh báo sau mấy lần hỏng"),
        ("save_certificates", "Lưu lại ảnh chứng chỉ"),
        ("archive_dir", "Thư mục kho ảnh"),
    ]),
    ("Email", [
        ("smtp_host", "Máy chủ SMTP"),
        ("smtp_port", "Cổng SMTP"),
        ("smtp_user", "Tài khoản gửi"),
        ("mail_from", "Địa chỉ From"),
        ("mail_to", "Nhận báo cáo"),
        ("alert_mail_to", "Nhận cảnh báo sự cố"),
        ("alert_cooldown_hours", "Giãn cách gửi cảnh báo (giờ)"),
    ]),
    ("Báo cáo định kỳ", [
        ("report_schedule", "Lịch gửi"),
        ("report_time", "Giờ gửi"),
        ("report_weekday", "Thứ trong tuần (0=T2)"),
        ("report_monthday", "Ngày trong tháng"),
        ("report_bucket", "Kỳ báo cáo"),
    ]),
]

# Trường chỉ nhận vài giá trị thì cho chọn, đừng bắt gõ. Gõ "Strict" hoa chữ
# S thì pydantic nhận, nhưng pipeline so bằng == nên luật siết không bật.
CONFIG_CHOICES = {
    "course_match_mode": ["loose", "strict"],
    "report_schedule": ["off", "daily", "weekly", "monthly"],
}


class SettingsError(ValueError):
    """Giá trị mới không hợp lệ, hoặc không ghi được xuống đĩa."""


def editable_fields() -> list[str]:
    """Mọi trường sửa được qua giao diện, theo đúng thứ tự khai trong Settings."""
    return [name for name in Settings.model_fields if name not in SECRET_FIELDS]


def env_names(field: str) -> list[str]:
    """Mọi tên biến .env pydantic chấp nhận cho trường này, tên chính đầu.

    Vài trường nhận nhiều tên: `technical_alert_after` còn ăn
    `TECHNICAL_RETRY_MAX`, `smtp_user` còn ăn `SMTP_USERNAME`. Ghi mù theo
    tên chính trong khi .env dùng tên cũ thì file có hai dòng cho một tham số.
    """
    info = Settings.model_fields[field]
    alias = getattr(info, "validation_alias", None)
    choices = getattr(alias, "choices", None)
    if choices:
        return [str(c).upper() for c in choices]
    if isinstance(alias, str):
        return [alias.upper()]
    return [field.upper()]


def format_value(value) -> str:
    """Đưa giá trị về dạng ghi được vào .env.

    Bọc nháy khi chuỗi có khoảng trắng hoặc `#`: python-dotenv coi phần sau
    `#` là chú thích.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if text == "":
        return ""
    if text != text.strip() or " " in text or "#" in text or '"' in text:
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def _split_comment(raw: str) -> tuple[str, str]:
    """Tách phần giá trị và phần chú thích cuối dòng (` # ...`)."""
    if raw.startswith(('"', "'")):
        quote = raw[0]
        end = raw.find(quote, 1)
        while end != -1 and raw[end - 1] == "\\":
            end = raw.find(quote, end + 1)
        if end != -1:
            return raw[:end + 1], raw[end + 1:]
    found = re.search(r"\s+#", raw)
    if found:
        return raw[:found.start()], raw[found.start():]
    return raw, ""


def update_env_text(text: str, updates: dict[str, str]) -> str:
    """Nội dung .env mới. `updates`: tên trường -> giá trị đã format.

    Sửa tại chỗ nếu thấy bất kỳ tên nào của trường, không thấy thì thêm vào
    cuối file. Mọi dòng khác giữ nguyên từng ký tự.
    """
    lines = text.splitlines(keepends=True)
    remaining = dict(updates)

    for index, line in enumerate(lines):
        body = line.rstrip("\r\n")
        ending = line[len(body):] or "\n"
        found = _ASSIGN.match(body)
        if not found:
            continue
        indent, key, equals, raw = found.groups()
        for field, formatted in list(remaining.items()):
            if key.upper() in env_names(field):
                _, comment = _split_comment(raw)
                lines[index] = f"{indent}{key}{equals}{formatted}{comment}{ending}"
                remaining.pop(field)
                break

    if remaining:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"\n# Thêm bởi MOOC Console — {datetime.now():%Y-%m-%d %H:%M}\n")
        for field, formatted in remaining.items():
            lines.append(f"{env_names(field)[0]}={formatted}\n")

    return "".join(lines)


def validate(raw: dict[str, str]) -> dict:
    """Kiểm giá trị mới trên một BẢN SAO, trả về giá trị đã ép kiểu.

    Bản sao chứ không gán thẳng: gán ba trường mà trường thứ ba sai thì hai
    trường đầu đã kịp đổi, hệ thống chạy tiếp bằng nửa cấu hình mới.
    """
    unknown = set(raw) - set(editable_fields())
    if unknown:
        raise SettingsError(f"Không sửa được ở đây: {', '.join(sorted(unknown))}")

    probe = settings.model_copy(deep=True)
    coerced = {}
    for field, value in raw.items():
        try:
            setattr(probe, field, value)
        except Exception as e:
            raise SettingsError(
                f"{env_names(field)[0]}: giá trị không hợp lệ ({value!r}). "
                f"{_short(e)}") from e
        coerced[field] = getattr(probe, field)
    return coerced


def _short(error: Exception) -> str:
    """Lấy dòng có ích nhất trong thông báo của pydantic.

    ValidationError in bốn dòng, chỉ dòng "Input should be..." đáng đọc.
    """
    useful = [line.strip() for line in str(error).splitlines()
              if line.strip() and not line.strip().startswith("For further")]
    return useful[-1] if useful else str(error)


def apply_changes(raw: dict[str, str], env_path: Path | None = None) -> list[tuple]:
    """Kiểm, ghi .env, rồi gán vào settings đang chạy. Trả về danh sách đổi.

    Ghi đĩa TRƯỚC, gán bộ nhớ SAU. Ngược lại thì khi đĩa đầy hoặc file bị
    khóa, hệ thống chạy cấu hình mới còn file giữ cấu hình cũ, và khởi động
    lại là im lặng quay về giá trị cũ.
    """
    env_path = Path(env_path) if env_path else ENV_PATH
    coerced = validate(raw)

    changes = [(field, getattr(settings, field), value)
               for field, value in coerced.items()
               if getattr(settings, field) != value]
    if not changes:
        return []

    updates = {field: format_value(value) for field, _, value in changes}
    try:
        old_text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        env_path.write_text(update_env_text(old_text, updates), encoding="utf-8")
    except OSError as e:
        raise SettingsError(f"Không ghi được {env_path.name}: {e}") from e

    for field, _, value in changes:
        setattr(settings, field, value)

    _write_audit(changes)
    return changes


def _write_audit(changes: list[tuple]) -> None:
    """Ghi lại ai đổi gì, lúc nào.

    Sửa cấu hình bằng vài cú bấm chuột thì phải có dấu vết, nếu không ba
    tháng sau không ai trả lời được "vì sao COURSE_MATCH_MODE thành strict".
    """
    stamp = datetime.now().isoformat(timespec="seconds")
    who = os.environ.get("USERNAME") or os.environ.get("USER") or "?"
    dong = [f"{stamp}\t{who}\t{env_names(f)[0]}\t{old!r} -> {new!r}"
            for f, old, new in changes]
    for line in dong:
        logger.info("Đổi cấu hình: %s", line.replace("\t", "  "))
    try:
        with AUDIT_PATH.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(dong) + "\n")
    except OSError as e:
        # Mất sổ thì tiếc, nhưng không được làm hỏng chính việc lưu.
        logger.warning("Không ghi được %s: %s", AUDIT_PATH.name, e)
