"""Dựng và gửi báo cáo ngày qua email (gui_bao_cao).

    python gui_bao_cao.py                   # xem trước: ghi ra file HTML, KHÔNG gửi
    python gui_bao_cao.py --ngay 2026-08-17 # báo cáo một ngày cụ thể
    python gui_bao_cao.py --gui             # gửi thật (cần cấu hình SMTP trong .env)

Mặc định là XEM TRƯỚC — phải thêm --gui mới gửi. Cố ý như vậy để chạy thử
không lỡ gửi mail cho cả phòng.

Phần dựng nội dung và phần gửi tách rời nhau. Chưa biết công ty cho gửi
bằng cách nào (SMTP relay nội bộ hay Microsoft Graph) thì vẫn xem trước
được nội dung; lúc biết chỉ cần điền hàm _gui_smtp hoặc viết thêm một hàm
gửi khác.
"""

import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import argparse
import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from config import settings
from database import bao_cao

MAU = {
    "xanh": "#1a7f37",
    "do": "#cf222e",
    "cam": "#bc4c00",
    "xam": "#656d76",
    "vien": "#d0d7de",
    "nen_nhat": "#f6f8fa",
}


def _o_so_lieu(nhan: str, so, mau: str = "#24292f", ghi_chu: str = "") -> str:
    return f"""
      <td style="padding:14px 18px;border:1px solid {MAU['vien']};
                 border-radius:8px;background:#fff;text-align:center;">
        <div style="font-size:28px;font-weight:700;color:{mau};line-height:1.2;">{so}</div>
        <div style="font-size:12px;color:{MAU['xam']};margin-top:4px;">{html.escape(nhan)}</div>
        {f'<div style="font-size:11px;color:{MAU["xam"]};margin-top:2px;">{html.escape(ghi_chu)}</div>' if ghi_chu else ''}
      </td>"""


def _bang_ca_loi(sl: dict) -> str:
    """Bảng liệt kê chứng chỉ thất bại, kèm id để tra thẳng trên eLIS.

    Chỉ liệt kê ca CẦN NGƯỜI CAN THIỆP — lỗi kỹ thuật và ca bị eLIS từ
    chối. Ca APPROVED/REJECTED bình thường không đưa vào: chúng là kết quả
    đúng của hệ thống, liệt kê ra chỉ làm email dài và loãng mất phần
    đáng đọc.
    """
    if not sl["ca_loi"]:
        return ""

    hang = []
    for c in sl["ca_loi"]:
        uc = c["user_course_id"] or "—"
        emp = c["employee_id"] or "—"
        ghi_chu = c["ly_do"] or ""
        if c["elis_gui_ok"] == 0 and c["elis_message"]:
            ghi_chu = f"eLIS từ chối: {c['elis_message']}"
        hang.append(f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid {MAU['vien']};
                     font-family:ui-monospace,Consolas,monospace;font-size:12px;">{html.escape(uc)}</td>
          <td style="padding:8px 12px;border-bottom:1px solid {MAU['vien']};
                     font-family:ui-monospace,Consolas,monospace;font-size:12px;">{html.escape(emp)}</td>
          <td style="padding:8px 12px;border-bottom:1px solid {MAU['vien']};font-size:13px;">{html.escape(ghi_chu[:120])}</td>
        </tr>""")

    con_lai = ""
    if sl["ca_loi_bi_cat"]:
        con_lai = (f"<div style='font-size:12px;color:{MAU['xam']};margin-top:6px;'>"
                   f"… và {sl['ca_loi_bi_cat']} ca nữa cùng loại, xem đầy đủ trong "
                   f"bảng log.</div>")

    return f"""
  <h3 style="font-size:15px;margin:24px 0 8px;">
    Chứng chỉ cần xem lại ({sl['tong_ca_loi']})
  </h3>
  <table style="width:100%;border-collapse:collapse;background:#fff;
                border:1px solid {MAU['vien']};border-radius:8px;">
    <tr style="background:{MAU['nen_nhat']};font-size:12px;color:{MAU['xam']};text-align:left;">
      <th style="padding:8px 12px;font-weight:600;">UserCourseId</th>
      <th style="padding:8px 12px;font-weight:600;">Mã NV</th>
      <th style="padding:8px 12px;font-weight:600;">Vấn đề</th>
    </tr>
    {''.join(hang)}
  </table>
  {con_lai}"""


def dung_html(sl: dict) -> str:
    """Dựng nội dung email HTML từ số liệu.

    Dùng bảng và style nội tuyến vì Outlook không hỗ trợ tốt CSS ngoài,
    flexbox hay grid — cách duy nhất hiển thị ổn định là bảng cổ điển.
    """
    chenh = sl["chenh_lech"]
    mui_ten = "▲" if chenh > 0 else ("▼" if chenh < 0 else "―")
    ghi_chu_chenh = f"{mui_ten} {abs(chenh)} so với hôm trước" if chenh else "bằng hôm trước"

    canh_bao_html = ""
    if sl["canh_bao"]:
        muc = "".join(f"<li style='margin-bottom:6px;'>{html.escape(c)}</li>"
                      for c in sl["canh_bao"])
        canh_bao_html = f"""
        <div style="margin:20px 0;padding:14px 18px;background:#fff8c5;
                    border:1px solid #d4a72c;border-radius:8px;">
          <div style="font-weight:600;margin-bottom:8px;">Cần chú ý</div>
          <ul style="margin:0;padding-left:20px;font-size:14px;line-height:1.6;">{muc}</ul>
        </div>"""

    tang_html = "".join(
        f"<tr><td style='padding:6px 12px;border-bottom:1px solid {MAU['vien']};'>{html.escape(k)}</td>"
        f"<td style='padding:6px 12px;border-bottom:1px solid {MAU['vien']};text-align:right;'>{v}</td></tr>"
        for k, v in sl["theo_tang"].items()) or \
        f"<tr><td colspan='2' style='padding:10px;color:{MAU['xam']};'>Không có dữ liệu</td></tr>"

    ly_do_html = "".join(
        f"<tr><td style='padding:6px 12px;border-bottom:1px solid {MAU['vien']};'>{html.escape(m['ly_do'])}</td>"
        f"<td style='padding:6px 12px;border-bottom:1px solid {MAU['vien']};text-align:right;'>{m['so_luong']}</td></tr>"
        for m in sl["ly_do_tu_choi"]) or \
        f"<tr><td colspan='2' style='padding:10px;color:{MAU['xam']};'>Không có chứng chỉ nào bị từ chối</td></tr>"

    ca_loi_html = _bang_ca_loi(sl)

    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:24px;background:{MAU['nen_nhat']};
  font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#24292f;">
<div style="max-width:680px;margin:0 auto;">

  <h2 style="margin:0 0 4px;font-size:20px;">Báo cáo xác minh chứng chỉ MOOC</h2>
  <div style="color:{MAU['xam']};font-size:14px;margin-bottom:20px;">
    Ngày {sl['ngay']} &nbsp;·&nbsp; {ghi_chu_chenh}
  </div>

  <table style="width:100%;border-collapse:separate;border-spacing:8px 0;margin-bottom:8px;">
    <tr>
      {_o_so_lieu("Tổng xử lý", sl['tong'])}
      {_o_so_lieu("Duyệt", sl['approved'], MAU['xanh'], f"{sl['ty_le_duyet']}%")}
      {_o_so_lieu("Từ chối", sl['rejected'], MAU['do'])}
      {_o_so_lieu("Lỗi kỹ thuật", sl['that_bai_ky_thuat'], MAU['cam'])}
    </tr>
  </table>

  {canh_bao_html}

  <h3 style="font-size:15px;margin:24px 0 8px;">Nộp kết quả lên eLIS</h3>
  <table style="width:100%;border-collapse:separate;border-spacing:8px 0;">
    <tr>
      {_o_so_lieu("eLIS đã nhận", sl['elis']['da_nhan'], MAU['xanh'])}
      {_o_so_lieu("eLIS từ chối", sl['elis']['tu_choi'], MAU['do'])}
      {_o_so_lieu("Chưa nộp được", sl['elis']['chua_gui'], MAU['cam'])}
    </tr>
  </table>

  <h3 style="font-size:15px;margin:24px 0 8px;">Kết luận ở tầng nào</h3>
  <table style="width:100%;border-collapse:collapse;background:#fff;
                border:1px solid {MAU['vien']};border-radius:8px;font-size:14px;">
    {tang_html}
  </table>
  <div style="font-size:12px;color:{MAU['xam']};margin-top:6px;">
    llm1 = Gemma đọc ảnh là đủ · llm2 / llm1_vs_llm2 = phải gọi thêm Azure OCR (tốn phí)
  </div>

  <h3 style="font-size:15px;margin:24px 0 8px;">Lý do từ chối</h3>
  <table style="width:100%;border-collapse:collapse;background:#fff;
                border:1px solid {MAU['vien']};border-radius:8px;font-size:14px;">
    {ly_do_html}
  </table>

  {ca_loi_html}

  <div style="margin-top:28px;padding-top:14px;border-top:1px solid {MAU['vien']};
              font-size:12px;color:{MAU['xam']};">
    Báo cáo tự động từ job AI scan chứng chỉ MOOC. Chi tiết đầy đủ từng
    chứng chỉ nằm trong bảng log_xu_ly của mooc_log.db.
  </div>

</div></body></html>"""


def dung_text(sl: dict) -> str:
    """Bản chữ thuần, cho ứng dụng mail không hiển thị HTML."""
    d = [
        f"BÁO CÁO XÁC MINH CHỨNG CHỈ MOOC — ngày {sl['ngay']}",
        "",
        f"  Tổng xử lý    : {sl['tong']}",
        f"  Duyệt         : {sl['approved']} ({sl['ty_le_duyet']}%)",
        f"  Từ chối       : {sl['rejected']}",
        f"  Lỗi kỹ thuật  : {sl['that_bai_ky_thuat']}",
        "",
        f"  eLIS đã nhận  : {sl['elis']['da_nhan']}",
        f"  eLIS từ chối  : {sl['elis']['tu_choi']}",
        f"  Chưa nộp được : {sl['elis']['chua_gui']}",
    ]
    if sl["canh_bao"]:
        d += ["", "CẦN CHÚ Ý:"] + [f"  - {c}" for c in sl["canh_bao"]]
    if sl["theo_tang"]:
        d += ["", "KẾT LUẬN Ở TẦNG:"] + [f"  {k}: {v}" for k, v in sl["theo_tang"].items()]
    if sl["ly_do_tu_choi"]:
        d += ["", "LÝ DO TỪ CHỐI:"] + [f"  {m['ly_do']}: {m['so_luong']}"
                                       for m in sl["ly_do_tu_choi"]]
    if sl["ca_loi"]:
        d += ["", f"CHỨNG CHỈ CẦN XEM LẠI ({sl['tong_ca_loi']}):"]
        for c in sl["ca_loi"]:
            ghi_chu = c["ly_do"] or ""
            if c["elis_gui_ok"] == 0 and c["elis_message"]:
                ghi_chu = f"eLIS từ chối: {c['elis_message']}"
            d.append(f"  {c['user_course_id']}  mã NV {c['employee_id']}  "
                     f"{ghi_chu[:90]}")
        if sl["ca_loi_bi_cat"]:
            d.append(f"  ... và {sl['ca_loi_bi_cat']} ca nữa cùng loại.")
    return "\n".join(d)


class LoiGuiMail(Exception):
    """Không gửi được báo cáo, kèm gợi ý cách xử lý."""


# =====================================================================
# Gửi qua Microsoft Teams (Workflows webhook)
# =====================================================================

def _the_adaptive_card(sl: dict) -> dict:
    """Dựng Adaptive Card cho Teams từ số liệu báo cáo.

    Teams KHÔNG hiển thị HTML như email — nó dùng Adaptive Card, một định
    dạng JSON riêng. Nên phải dựng lại nội dung, không dùng chung hàm
    dung_html() được.
    """
    def cot(nhan, gia_tri, mau="default"):
        return {
            "type": "Column", "width": "stretch", "items": [
                {"type": "TextBlock", "text": str(gia_tri), "size": "ExtraLarge",
                 "weight": "Bolder", "color": mau, "spacing": "None",
                 "horizontalAlignment": "Center"},
                {"type": "TextBlock", "text": nhan, "size": "Small",
                 "isSubtle": True, "spacing": "None",
                 "horizontalAlignment": "Center", "wrap": True},
            ]}

    than = [
        {"type": "TextBlock", "text": "Báo cáo xác minh chứng chỉ MOOC",
         "size": "Large", "weight": "Bolder", "wrap": True},
        {"type": "TextBlock", "text": f"Ngày {sl['ngay']}", "isSubtle": True,
         "spacing": "None"},
        {"type": "ColumnSet", "columns": [
            cot("Tổng xử lý", sl["tong"]),
            cot("Duyệt", sl["approved"], "good"),
            cot("Từ chối", sl["rejected"], "attention"),
            cot("Lỗi kỹ thuật", sl["that_bai_ky_thuat"], "warning"),
        ]},
    ]

    if sl["canh_bao"]:
        than.append({
            "type": "Container", "style": "warning", "bleed": True,
            "items": [
                {"type": "TextBlock", "text": "Cần chú ý", "weight": "Bolder",
                 "wrap": True},
                *[{"type": "TextBlock", "text": f"• {c}", "wrap": True,
                   "spacing": "Small"} for c in sl["canh_bao"]],
            ]})

    than.append({
        "type": "FactSet", "facts": [
            {"title": "eLIS đã nhận", "value": str(sl["elis"]["da_nhan"])},
            {"title": "eLIS từ chối", "value": str(sl["elis"]["tu_choi"])},
            {"title": "Chưa nộp được", "value": str(sl["elis"]["chua_gui"])},
        ]})

    if sl["theo_tang"]:
        than.append({
            "type": "FactSet",
            "facts": [{"title": k, "value": str(v)}
                      for k, v in sl["theo_tang"].items()]})

    if sl["ca_loi"]:
        # Teams hiển thị trên điện thoại nữa, nên chỉ đưa vài dòng đầu.
        # Chi tiết đầy đủ đã có trong DB.
        dong = []
        for c in sl["ca_loi"][:8]:
            ghi_chu = c["ly_do"] or ""
            if c["elis_gui_ok"] == 0 and c["elis_message"]:
                ghi_chu = f"eLIS từ chối: {c['elis_message']}"
            dong.append({"title": str(c["user_course_id"] or "—")[:20],
                         "value": ghi_chu[:70]})
        than.append({
            "type": "Container", "items": [
                {"type": "TextBlock",
                 "text": f"Chứng chỉ cần xem lại ({sl['tong_ca_loi']})",
                 "weight": "Bolder", "wrap": True},
                {"type": "FactSet", "facts": dong},
            ]})
        if sl["tong_ca_loi"] > 8:
            than.append({"type": "TextBlock", "isSubtle": True, "wrap": True,
                         "text": f"… và {sl['tong_ca_loi'] - 8} ca nữa, "
                                 f"xem đầy đủ trong bảng log."})

    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": than,
            },
        }],
    }


def _gui_teams(sl: dict) -> None:
    """Gửi báo cáo vào kênh Teams qua Workflows webhook.

    LƯU Ý VỀ CÁCH TẠO WEBHOOK: cách cũ ("Connectors -> Incoming Webhook")
    đã bị Microsoft TẮT VĨNH VIỄN trong tháng 5/2026. Hướng dẫn cũ trên
    mạng không dùng được nữa. Cách hiện tại là tạo một Workflow:

      Trong kênh Teams -> ... -> Workflows -> tìm mẫu
      "Post to a channel when a webhook request is received"
      -> chọn kênh -> Create -> copy URL sinh ra -> dán vào TEAMS_WEBHOOK_URL

    Webhook Workflows không cần mật khẩu: URL chính là thứ xác thực. Nên
    coi nó như mật khẩu — ai có URL cũng đăng bài vào kênh được.
    """
    import requests

    if not settings.teams_webhook_url:
        raise LoiGuiMail(
            "Chưa có TEAMS_WEBHOOK_URL trong .env.\n"
            "  Tạo webhook: mở kênh Teams -> dấu ... -> Workflows ->\n"
            "  mẫu 'Post to a channel when a webhook request is received'\n"
            "  -> chọn kênh -> Create -> copy URL vào .env.\n"
            "  (Cách cũ qua Connectors đã bị Microsoft tắt từ 5/2026.)"
        )

    try:
        resp = requests.post(settings.teams_webhook_url,
                             json=_the_adaptive_card(sl), timeout=30)
    except requests.RequestException as e:
        raise LoiGuiMail(f"Không gọi được webhook Teams: {e}") from e

    # Workflows trả 202 Accepted, connector cũ trả 200. Nhận cả hai.
    if resp.status_code not in (200, 202):
        raise LoiGuiMail(
            f"Teams từ chối (HTTP {resp.status_code}): {resp.text[:300]}\n"
            "  404/410 -> URL sai hoặc workflow đã bị xóa.\n"
            "  400     -> payload sai định dạng, báo lại tôi."
        )


def _gui_smtp(tieu_de: str, noi_dung_html: str, noi_dung_text: str) -> None:
    """Gửi báo cáo qua SMTP (mặc định Office 365).

    Gửi email nhiều phần (multipart/alternative): bản chữ thuần và bản HTML
    trong cùng một thư. Ứng dụng mail nào đọc được HTML thì hiện bản đẹp,
    không thì rơi về bản chữ. Outlook luôn chọn HTML.

    LƯU Ý VỀ HẠN SỬ DỤNG: Microsoft đang khai tử Basic Auth cho SMTP AUTH
    trên Exchange Online, mốc hiện tại là 31/12/2026. Sau đó cách này ngừng
    hoạt động và phải chuyển sang Microsoft Graph API hoặc SMTP relay nội
    bộ. Phần dựng nội dung ở trên không phụ thuộc cách gửi nên lúc đó chỉ
    cần viết thêm một hàm gửi khác.
    """
    import smtplib
    from email.message import EmailMessage

    thieu = [ten for ten, gt in [
        ("SMTP_HOST", settings.smtp_host),
        ("SMTP_USER", settings.smtp_user),
        ("SMTP_PASSWORD", settings.smtp_password),
        ("MAIL_DEN", settings.mail_den),
    ] if not gt]
    if thieu:
        raise LoiGuiMail(
            "Thiếu cấu hình trong .env: " + ", ".join(thieu) + "\n"
            "  SMTP_USER     = email công ty của bạn\n"
            "  SMTP_PASSWORD = App Password (KHÔNG phải mật khẩu đăng nhập)\n"
            "  MAIL_DEN      = email mentor\n"
            "  Bỏ --gui để xem trước nội dung mà không cần cấu hình."
        )

    nguoi_nhan = [e.strip() for e in settings.mail_den.split(",") if e.strip()]
    nguoi_gui = settings.mail_tu or settings.smtp_user

    thu = EmailMessage()
    thu["Subject"] = tieu_de
    thu["From"] = nguoi_gui
    thu["To"] = ", ".join(nguoi_nhan)
    thu.set_content(noi_dung_text)                      # bản chữ thuần
    thu.add_alternative(noi_dung_html, subtype="html")  # bản HTML

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as s:
            s.starttls()
            s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(thu)
    except smtplib.SMTPAuthenticationError as e:
        raise LoiGuiMail(
            f"Đăng nhập SMTP thất bại: {e}\n\n"
            "  Nguyên nhân thường gặp, theo thứ tự hay gặp nhất:\n"
            "   1. Dùng mật khẩu đăng nhập thay vì App Password. Office 365\n"
            "      không nhận mật khẩu thường khi có MFA — phải tạo App\n"
            "      Password riêng.\n"
            "   2. Công ty đã TẮT SMTP AUTH cho tài khoản của bạn. Microsoft\n"
            "      tắt mặc định, admin phải bật lại từng hộp thư.\n"
            "      -> Nhờ IT bật, hoặc chuyển sang Teams webhook / SMTP relay.\n"
            "   3. Sai SMTP_USER."
        ) from e
    except smtplib.SMTPException as e:
        raise LoiGuiMail(
            f"Lỗi SMTP: {type(e).__name__}: {e}\n"
            f"  Đang dùng {settings.smtp_host}:{settings.smtp_port}\n"
            "  Nếu là lỗi kết nối: mạng công ty có thể chặn cổng 587."
        ) from e
    except OSError as e:
        raise LoiGuiMail(
            f"Không kết nối được {settings.smtp_host}:{settings.smtp_port} — {e}\n"
            "  Mạng công ty hoặc firewall có thể đang chặn cổng này."
        ) from e


def main():
    p = argparse.ArgumentParser(description="Báo cáo ngày qua email")
    p.add_argument("--ngay", help="YYYY-MM-DD. Mặc định: hôm qua")
    p.add_argument("--gui", action="store_true",
                   help="Gửi thật. Không có cờ này thì chỉ xem trước.")
    p.add_argument("--kenh", choices=["teams", "email"],
                   help="Gửi qua đâu. Mặc định lấy KENH_BAO_CAO trong .env")
    p.add_argument("--ra", default="bao_cao.html",
                   help="File HTML xem trước (mặc định bao_cao.html)")
    args = p.parse_args()

    sl = bao_cao.bao_cao_ngay(args.ngay)
    tieu_de = (f"[MOOC] Báo cáo {sl['ngay']} — "
               f"{sl['approved']} duyệt / {sl['rejected']} từ chối")

    print(dung_text(sl))
    print()

    if args.gui:
        kenh = (args.kenh or settings.kenh_bao_cao or "teams").lower()
        try:
            if kenh == "teams":
                _gui_teams(sl)
                print("Đã gửi vào kênh Teams.")
            elif kenh == "email":
                _gui_smtp(tieu_de, dung_html(sl), dung_text(sl))
                print(f"Đã gửi email tới {settings.mail_den}: {tieu_de}")
            else:
                print(f"Kênh không hợp lệ: {kenh!r}. Chọn 'teams' hoặc 'email'.")
                return 1
        except LoiGuiMail as e:
            print(f"KHÔNG GỬI ĐƯỢC:\n{e}")
            return 1
    else:
        Path(args.ra).write_text(dung_html(sl), encoding="utf-8")
        print(f"Đã ghi bản xem trước: {args.ra}")
        print("Mở file đó bằng trình duyệt để xem email sẽ trông thế nào.")
        print("Thêm --gui để gửi thật (hiện chưa cấu hình cách gửi).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
