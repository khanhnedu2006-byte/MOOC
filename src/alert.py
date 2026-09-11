
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import llm_error
from config import settings

logger = logging.getLogger("alert")

# Đặt ở gốc dự án, cạnh .report_state.json, để docker-compose gắn ra ngoài
# được; nằm trong container thì mốc mất mỗi lần dựng lại.
STATE_FILE = Path(__file__).resolve().parent.parent / ".alert_state.json"

# Gửi thư hỏng thì đợi ngần này phút mới thử lại. Không có mốc này thì một mật
# khẩu SMTP sai thành một lần đăng nhập mỗi hai phút và Gmail khóa tài khoản
# vì nghi brute-force.
RETRY_SEND_MINUTES = 15

# Giải thích stage bằng tiếng người: người nhận thư là người vận hành, không
# phải người viết code.
STAGE_DESCRIPTION = {
    "llm1_error": "Gọi AI đọc ảnh (Gemma/FPT) thất bại — key hết hạn, "
                  "hết hạn mức, hoặc dịch vụ lỗi",
    "stage2_error": "Azure OCR hoặc AI đọc lại thất bại — hết hạn mức Azure "
                    "(gói F0 chỉ 500 trang/tháng), sai key, hoặc dịch vụ lỗi",
    "file_error": "File tải về không phải ảnh/PDF đọc được",
    "download_error": "Gọi API tải file của eLIS thất bại",
    "no_file": "eLIS không trả về file cho chứng chỉ này",
    "soft_fail_zip": "Gói file eLIS trả về bị hỏng",
    "system_error": "Lỗi hệ thống chưa phân loại",
}

# Ba API của eLIS: (tên gọi, việc nó làm, hệ quả khi nó chết). Ba API hỏng cho
# ba mức khẩn cấp khác hẳn nhau:
#   ① chết -> hệ thống đứng im hoàn toàn, không xử lý được cái nào.
#   ② chết -> chứng chỉ ở lại WAITING, tự khỏi khi eLIS sống lại. Nhẹ nhất.
#   ③ chết -> ĐANG ĐỐT TIỀN: quét xong rồi mất kết quả, vòng sau quét lại.
API_INFO = {
    1: ("① getCert",
        "lấy danh sách chứng chỉ chờ duyệt",
        "Hệ thống ĐỨNG IM - không lấy được hàng đợi nên không xử lý được "
        "chứng chỉ nào. Không có gì tự khỏi cho tới khi eLIS trả lời lại."),
    2: ("② download-certificates",
        "tải file chứng chỉ về để quét",
        "Chứng chỉ ở lại WAITING trên eLIS và được thử lại. KHÔNG cái nào bị "
        "từ chối oan. Đây là mức nhẹ nhất trong ba API."),
    3: ("③ ProcessUserCourseStatus",
        "nộp kết quả duyệt về eLIS",
        "ĐANG TỐN TIỀN: chứng chỉ đã quét xong (đã trả phí Gemma + Azure) "
        "nhưng kết quả không nộp được, nên vòng sau eLIS vẫn trả về chúng và "
        "job quét lại từ đầu. Càng để lâu càng tốn."),
}


def _read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Chưa có file hoặc file hỏng: coi như chưa gửi lần nào, thà gửi thừa
        # một thư còn hơn im lặng.
        return {}


def _write_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    except OSError as e:
        # Không ghi được mốc thì chỉ mất khả năng chặn gửi trùng; ném lỗi ra
        # ngoài sẽ giết vòng xử lý chứng chỉ vì một việc phụ.
        logger.warning("Không ghi được %s: %s", STATE_FILE.name, e)


def api_failed(api_code: int, reason: str, now: datetime | None = None) -> None:
    """Ghi nhận MỘT lần gọi API eLIS thất bại (sau khi call_with_retry hết lượt).

    Chỉ ĐẾM, không gửi thư; send_alert() quyết định gửi, để sự cố API và sự cố
    chứng chỉ cùng đi trong MỘT thư. Bộ đếm nằm trong file trạng thái nên sống
    qua restart — loại sự cố này hay đi kèm việc dựng lại container.
    """
    now = now or datetime.now()
    state = _read_state()
    api = state.setdefault("api", {})
    previous = api.get(str(api_code)) or {}
    api[str(api_code)] = {
        "failure_count": (previous.get("failure_count") or 0) + 1,
        "reason": str(reason)[:400],
        "since": previous.get("since") or now.isoformat(timespec="seconds"),
        "last_seen": now.isoformat(timespec="seconds"),
    }
    _write_state(state)


def api_succeeded(api_code: int) -> None:
    """Ghi nhận API gọi được -> xóa bộ đếm thất bại của nó.

    CHỈ GHI FILE KHI CÓ THAY ĐỔI: hàm chạy sau mọi lần gọi API thành công,
    ghi mỗi lần là hàng chục nghìn lượt ghi đĩa mỗi ngày.
    """
    state = _read_state()
    api = state.get("api") or {}
    if str(api_code) not in api:
        return
    api.pop(str(api_code))
    state["api"] = api
    _write_state(state)


def _actions_required(failing_certs: list[dict]) -> list[str]:
    """Những việc CON NGƯỜI phải làm, vì máy thử lại mãi cũng không xong.

    Hai nguồn:
      - Lý do có mốc llm_error.TAG_NEEDS_HUMAN: hết tiền, sai key, sai model,
        hết quota Azure. Module sinh lỗi tự gắn mốc nên không phải đoán.
      - stage "file_error": file hỏng thật của riêng một chứng chỉ, nhưng với
        luật chặn đầu hàng nó khóa cả hàng đợi vô thời hạn.

    Bỏ trùng, giữ thứ tự gặp: Azure hết quota làm hàng chục chứng chỉ hỏng
    cùng MỘT lý do.
    """
    out, seen = [], set()
    for c in failing_certs:
        reason = str(c.get("reason") or "")
        if llm_error.TAG_NEEDS_HUMAN in reason:
            # Cắt lấy phần giải thích, bỏ thông báo thô của SDK.
            sentence = reason.split(llm_error.TAG_NEEDS_HUMAN, 1)[1].lstrip(": ")
            sentence = sentence.split(llm_error.SEPARATOR)[0].strip()
        elif c.get("stage") == "file_error":
            sentence = (f"File chứng chỉ của {c.get('employee_name') or '?'} "
                        f"không đọc được (hỏng, rỗng, hoặc sai định dạng). Cần "
                        f"yêu cầu nộp lại file khác — thử lại sẽ không tự khỏi, "
                        f"và chứng chỉ này đang CHẶN cả hàng đợi phía sau.")
        else:
            continue
        if sentence not in seen:
            seen.add(sentence)
            out.append(sentence)
    return out


def _actions_block_html(actions: list[str]) -> str:
    """Khối ĐỎ liệt kê việc con người phải làm. Rỗng thì không in gì."""
    if not actions:
        return ("<p style='color:#2e7d32'>Sự cố khắc phục xong thì chứng chỉ "
                "tự được xử lý, không cần thao tác gì thêm.</p>")
    bullets = "".join(f"<li>{_escape(a)}</li>" for a in actions)
    return (f"<div style='background:#fdecea;border-left:4px solid #b00;"
            f"padding:10px;margin:12px 0'>"
            f"<b style='color:#b00'>SỰ CỐ NÀY KHÔNG TỰ KHỎI — CẦN NGƯỜI XỬ LÝ</b>"
            f"<ul style='margin:8px 0 0'>{bullets}</ul>"
            f"<div style='margin-top:8px;font-size:12px;color:#666'>Hệ thống vẫn "
            f"thử lại đều nhưng sẽ hỏng y như vậy wait tới khi việc trên được "
            f"làm xong.</div></div>")


def _apis_past_threshold(state: dict) -> dict:
    """Những API đã thất bại liên tiếp tới ngưỡng cảnh báo."""
    threshold = max(1, settings.technical_alert_after)
    return {int(code): v for code, v in (state.get("api") or {}).items()
            if (v.get("failure_count") or 0) >= threshold}


def fingerprint(failing_certs: list[dict], failing_apis: dict | None = None) -> str:
    """Định danh MỘT loại sự cố, sắp xếp cho ổn định.

    Gồm tập stage của chứng chỉ đang hỏng CỘNG mã những API đang chết. Thiếu
    vế thứ hai thì lúc API ① chết khóa là chuỗi rỗng, trùng mọi sự cố rỗng
    khác, và chống gửi trùng nuốt luôn thư báo API hỏng.
    """
    part = sorted({(c.get("stage") or "?") for c in failing_certs})
    part += [f"api{code}" for code in sorted(failing_apis or {})]
    return "+".join(part)


def _may_send(state: dict, key: str, now: datetime) -> bool:
    """Đã tới lượt gửi cho loại sự cố này chưa."""
    last_failure = state.get("last_failure")
    if last_failure:
        # Lần gửi trước hỏng (SMTP lỗi): đợi RETRY_SEND_MINUTES rồi thử lại,
        # bất kể loại sự cố, vì vấn đề nằm ở đường gửi.
        try:
            at = datetime.fromisoformat(last_failure["at"])
        except (KeyError, TypeError, ValueError):
            return True
        if now - at < timedelta(minutes=RETRY_SEND_MINUTES):
            return False
        return True

    if state.get("key") != key:
        return True            # sự cố loại KHÁC -> báo ngay, không đợi

    try:
        at = datetime.fromisoformat(state["at"])
    except (KeyError, TypeError, ValueError):
        return True
    return now - at >= timedelta(hours=max(0, settings.alert_cooldown_hours))


def _subject_line(failing_certs: list[dict], failing_apis: dict) -> str:
    """Tiêu đề nói NGAY chuyện nặng nhất, vì nhiều người chỉ đọc tiêu đề."""
    if failing_apis:
        label = ", ".join(API_INFO[code][0].split()[0] for code in sorted(failing_apis))
        them = f" + {len(failing_certs)} chứng chỉ" if failing_certs else ""
        return f"[MOOC] KHÔNG GỌI ĐƯỢC API eLIS {label}{them}"
    stages = sorted({(c.get("stage") or "?") for c in failing_certs})
    return (f"[MOOC] Lỗi hệ thống — {len(failing_certs)} chứng chỉ chưa xử lý được "
            f"({', '.join(stages)})")


def _api_block_text(failing_apis: dict) -> str:
    """Bảng trạng thái CẢ BA API, dạng chữ thuần. In đủ ba dòng kể cả khi chỉ
    một API hỏng: "không nhắc tới" và "vẫn tốt" là hai chuyện khác nhau.
    """
    lines = ["", "TRẠNG THÁI 3 API CỦA eLIS:"]
    for code in (1, 2, 3):
        label, does, impact = API_INFO[code]
        state_row = failing_apis.get(code)
        lines.append(f"\n  {label} — {does}")
        if not state_row:
            lines.append("      Trạng thái: bình thường")
            continue
        lines.append(f"      Trạng thái: THẤT BẠI "
                     f"{state_row.get('failure_count', '?')} lần liên tiếp")
        lines.append(f"      Hỏng từ  : {_format_time(state_row.get('since'))}")
        lines.append(f"      Lý do    : {str(state_row.get('reason') or '?')[:300]}")
        lines.append(f"      Hệ quả   : {impact}")
    return "\n".join(lines) + "\n"


def _format_time(iso: str | None) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%H:%M %d/%m/%Y")
    except (TypeError, ValueError):
        return "?"


def _build_content(failing_certs: list[dict], now: datetime,
                   failing_apis: dict | None = None) -> tuple[str, str, str]:
    """Dựng (tiêu đề, bản chữ thuần, bản HTML) cho thư cảnh báo."""
    failing_apis = failing_apis or {}
    stages = sorted({(c.get("stage") or "?") for c in failing_certs})
    title = _subject_line(failing_certs, failing_apis)

    # "0 chứng chỉ bị ảnh hưởng" là SAI khi API ① chết: CẢ hàng đợi bị ảnh
    # hưởng, chỉ là không lấy được danh sách nên không đếm được. In số 0 ở đó
    # làm người nhận tưởng sự cố vô hại.
    if 1 in failing_apis:
        affected = ("TOÀN BỘ hàng đợi (không lấy được danh sách nên "
                     "không đếm được)")
    else:
        affected = f"{len(failing_certs)} chứng chỉ"

    actions = _actions_required(failing_certs)

    header = (
        f"Hệ thống xác minh chứng chỉ MOOC gặp lỗi KỸ THUẬT.\n"
        f"Thời điểm: {now.strftime('%H:%M %d/%m/%Y')}\n"
        f"Bị ảnh hưởng: {affected}\n\n"
        f"Chứng chỉ KHÔNG bị từ chối, KHÔNG cái nào bị mất. Chúng ở lại trạng "
        f"thái chờ duyệt trên eLIS và hệ thống tiếp tục thử lại mỗi "
        f"{settings.technical_retry_cooldown_minutes} phút.\n\n"
    )
    if actions:
        # ĐỔI GIỌNG khi sự cố không tự khỏi. Câu mặc định "khắc phục xong thì
        # tự được xử lý" đúng với Azure quá tải hay eLIS chập, nhưng SAI với
        # hết tiền / sai key: phải nói rõ người nhận cần làm gì.
        header += ("*** SỰ CỐ NÀY KHÔNG TỰ KHỎI — CẦN NGƯỜI XỬ LÝ ***\n"
                   + "".join(f"  - {a}\n" for a in actions)
                   + "\nHệ thống vẫn thử lại đều nhưng sẽ hỏng y như vậy cho "
                     "tới khi việc trên được làm xong.\n")
    else:
        header += (f"Sự cố khắc phục xong thì chúng tự được xử lý, không cần "
                   f"thao tác gì thêm.\n\nCần người kiểm tra vì máy đã thử "
                   f"{settings.technical_alert_after} lần không được.\n")

    text = header + _api_block_text(failing_apis)

    if stages:
        text += "\nNGUYÊN NHÂN CÓ THỂ:\n" + "\n".join(
            f"  - {s}: {STAGE_DESCRIPTION.get(s, 'không rõ')}" for s in stages)

    if failing_certs:
        lines = ["", "DANH SÁCH CHỨNG CHỈ:"]
        for c in failing_certs:
            lines.append(
                f"  [{c.get('failure_count', '?')} lần] "
                f"{c.get('employee_name') or '?'} | "
                f"{(c.get('course_name') or '?')[:50]} | "
                f"{c.get('stage') or '?'}")
            if c.get("reason"):
                lines.append(f"      {c['reason'][:160]}")
        text += "\n".join(lines) + "\n"

    cell = "padding:6px 10px;border-bottom:1px solid #eee"
    rows = "".join(
        "<tr>"
        f"<td style='{cell}'>{_escape(c.get('employee_name'))}</td>"
        f"<td style='{cell}'>{_escape(c.get('course_name'))}</td>"
        f"<td style='{cell}'>{_escape(c.get('stage'))}</td>"
        f"<td style='{cell};text-align:center'>{_escape(c.get('failure_count'))}</td>"
        f"<td style='{cell};color:#666'>"
        f"{_escape((c.get('reason') or '')[:160])}</td>"
        "</tr>"
        for c in failing_certs)

    causes = "".join(
        f"<li><code>{_escape(s)}</code> — "
        f"{_escape(STAGE_DESCRIPTION.get(s, 'không rõ'))}</li>"
        for s in stages)

    cert_block = f"""<h3 style="margin-top:24px">Chứng chỉ bị ảnh hưởng ({len(failing_certs)})</h3>
<p><b>Nguyên nhân có thể:</b></p>
<ul>{causes}</ul>
<table style="border-collapse:collapse;width:100%;font-size:13px">
<tr style="background:#f5f5f5;text-align:left">
<th style="padding:6px 10px">Nhân viên</th>
<th style="padding:6px 10px">Khóa học</th>
<th style="padding:6px 10px">Tầng lỗi</th>
<th style="padding:6px 10px">Số lần</th>
<th style="padding:6px 10px">Chi tiết</th></tr>
{rows}
</table>""" if failing_certs else ""

    html = f"""<div style="font-family:Arial,sans-serif;font-size:14px;color:#222">
<h2 style="color:#b00">{_escape(_heading_line(failing_certs, failing_apis))}</h2>
<p><b>Thời điểm:</b> {now.strftime('%H:%M %d/%m/%Y')}</p>
<p><b>Bị ảnh hưởng:</b> {_escape(affected)}</p>
<p style="background:#fff8e1;border-left:4px solid #f0ad4e;padding:10px">
Chứng chỉ <b>không bị từ chối, không cái nào bị mất</b>. Chúng ở lại trạng thái
chờ duyệt trên eLIS và hệ thống tiếp tục thử lại mỗi
{settings.technical_retry_cooldown_minutes} phút.</p>
{_actions_block_html(actions)}
{_api_block_html(failing_apis)}
{cert_block}
<p style="color:#888;font-size:12px">Thư tự động từ hệ thống MOOC. Cùng một loại
sự cố sẽ không gửi lại trong {settings.alert_cooldown_hours} tiếng.</p>
</div>"""
    return title, text, html


def _heading_line(failing_certs: list[dict], failing_apis: dict) -> str:
    """Dòng tiêu đề in trong thân thư (không có tiền tố [MOOC])."""
    if failing_apis:
        label = ", ".join(API_INFO[code][0] for code in sorted(failing_apis))
        return f"Không gọi được API eLIS: {label}"
    return f"Lỗi hệ thống — {len(failing_certs)} chứng chỉ chưa xử lý được"


def _api_block_html(failing_apis: dict) -> str:
    """Bảng trạng thái CẢ BA API. Luôn in đủ ba dòng — xem _khoi_api_text()."""
    cell = "padding:8px 10px;border-bottom:1px solid #eee;vertical-align:top"
    rows = []
    for code in (1, 2, 3):
        label, does, impact = API_INFO[code]
        state_row = failing_apis.get(code)
        if not state_row:
            rows.append(
                f"<tr><td style='{cell}'><b>{_escape(label)}</b><br>"
                f"<span style='color:#777;font-size:12px'>{_escape(does)}</span></td>"
                f"<td style='{cell};color:#2e7d32'>bình thường</td>"
                f"<td style='{cell};color:#999'>—</td></tr>")
            continue
        rows.append(
            f"<tr style='background:#fff5f5'><td style='{cell}'><b>{_escape(label)}</b><br>"
            f"<span style='color:#777;font-size:12px'>{_escape(does)}</span></td>"
            f"<td style='{cell};color:#b00'><b>THẤT BẠI "
            f"{_escape(state_row.get('failure_count'))} lần liên tiếp</b><br>"
            f"<span style='color:#777;font-size:12px'>"
            f"từ {_format_time(state_row.get('since'))}</span></td>"
            f"<td style='{cell}'><code style='font-size:12px'>"
            f"{_escape(str(state_row.get('reason') or '')[:300])}</code>"
            f"<div style='margin-top:6px;color:#b00;font-size:12px'>"
            f"{_escape(impact)}</div></td></tr>")
    return f"""<h3 style="margin-top:24px">Trạng thái 3 API của eLIS</h3>
<table style="border-collapse:collapse;width:100%;font-size:13px">
<tr style="background:#f5f5f5;text-align:left">
<th style="padding:6px 10px">API</th>
<th style="padding:6px 10px">Trạng thái</th>
<th style="padding:6px 10px">Lý do / hệ quả</th></tr>
{"".join(rows)}
</table>"""


def _escape(value) -> str:
    from html import escape
    return escape(str(value if value not in (None, "") else "?"))


def send_alert(failing_certs: list[dict], now: datetime | None = None,
               send=None) -> bool:
    now = now or datetime.now()
    state = _read_state()
    failing_apis = _apis_past_threshold(state)

    if not failing_certs and not failing_apis:
        return False

    key = fingerprint(failing_certs, failing_apis)

    if not _may_send(state, key, now):
        logger.debug("Đã cảnh báo cho sự cố %r gần đây, không gửi lại.", key)
        return False

    if send is None:
        import send_report
        send = send_report._send_smtp

    title, text, html = _build_content(failing_certs, now, failing_apis)
    try:
        send(title, html, text, None, settings.alert_mail_to)
    except Exception as e:
        # Ghi mốc THẤT BẠI, không ghi mốc đã gửi: vẫn phải thử lại, nhưng
        # không thử ngay ở vòng kế tiếp.
        state["last_failure"] = {"at": now.isoformat(timespec="seconds"),
                             "key": key}
        _write_state(state)
        logger.error("KHÔNG gửi được email cảnh báo tới %s: %s\n"
                     "  Sự cố: %s (%d chứng chỉ)\n"
                     "  Sẽ thử gửi lại sau %d phút.",
                     settings.alert_mail_to, e, key, len(failing_certs),
                     RETRY_SEND_MINUTES)
        return False

    state.pop("last_failure", None)
    state["key"] = key
    state["at"] = now.isoformat(timespec="seconds")
    state["cert_count"] = len(failing_certs)
    _write_state(state)
    logger.warning("Đã gửi email cảnh báo tới %s: %s (%d chứng chỉ).",
                   settings.alert_mail_to, key, len(failing_certs))
    return True
