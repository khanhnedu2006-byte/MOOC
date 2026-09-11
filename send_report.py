"""Gửi báo cáo xác minh chứng chỉ qua email (send_report).

    python send_report.py                        # xem trước 7 ngày gần nhất
    python send_report.py --day 2026-08-17       # đúng một ngày
    python send_report.py --from 2026-08-01 --to 2026-08-31 --bucket week
    python send_report.py --send                 # gửi thật

Mặc định là XEM TRƯỚC — phải thêm --send mới gửi, để chạy thử không lỡ gửi
mail cho mentor.

CHỈ MỘT BỘ DỰNG BÁO CÁO: report_layout.py — báo cáo ngày chỉ là báo cáo kỳ với
from = to. Phần dựng nội dung tách rời phần gửi, nên đổi cách gửi (SMTP relay
nội bộ, Microsoft Graph...) chỉ cần viết thêm một hàm gửi.
"""

import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from config import settings
from database import report


class MailSendError(Exception):
    """
    Không gửi được báo cáo.
    """

def _send_smtp(title: str, html_body: str, text_body: str,
               images: list | None = None, mail_to: str | None = None) -> None:
    """Gửi một email qua SMTP (mặc định Office 365).

    multipart/alternative: bản chữ thuần và bản HTML trong cùng một thư. Client
    không đọc được HTML thì rơi về bản chữ; Outlook luôn chọn HTML.

    images: [(cid, png_bytes)] — biểu đồ, đính kèm INLINE theo Content-ID. Không
    được để link http: Outlook mặc định chặn ảnh tải từ internet.

    mail_to: người nhận, rỗng = settings.mail_to. Có tham số này vì alert.py gửi
    cảnh báo tới địa chỉ KHÁC; tách hàm gửi thứ hai thì phần chẩn đoán lỗi SMTP
    bên dưới phải nhân đôi.
    """
    import smtplib
    from email.message import EmailMessage

    # Người nhận mặc định là MAIL_TO; alert.py truyền ALERT_MAIL_TO vào.
    dia_chi = mail_to if mail_to is not None else settings.mail_to
    ten_bien = "MAIL_TO" if mail_to is None else "ALERT_MAIL_TO"

    missing = [name for name, ground_truth in [
        ("SMTP_HOST", settings.smtp_host),
        ("SMTP_USER", settings.smtp_user),
        ("SMTP_PASSWORD", settings.smtp_password),
        (ten_bien, dia_chi),
    ] if not ground_truth]
    if missing:
        raise MailSendError(
            "Thiếu cấu hình trong .env: " + ", ".join(missing) + "\n"
            "  SMTP_USER     = email công ty của bạn\n"
            "  SMTP_PASSWORD = App Password (KHÔNG phải mật khẩu đăng nhập)\n"
            "  MAIL_TO      = email mentor (nhận báo cáo định kỳ)\n"
            "  ALERT_MAIL_TO = email nhận cảnh báo lỗi hệ thống\n"
            "  Bỏ --send để xem trước nội dung mà không cần cấu hình."
        )

    recipient = [e.strip() for e in dia_chi.split(",") if e.strip()]
    sender = settings.mail_from or settings.smtp_user

    candidate = EmailMessage()
    candidate["Subject"] = title
    candidate["From"] = sender
    candidate["To"] = ", ".join(recipient)
    candidate.set_content(text_body)                      # bản chữ thuần
    candidate.add_alternative(html_body, subtype="html")  # bản HTML

    # Gắn ảnh vào ĐÚNG phần HTML (payload cuối), không vào thư gốc: gắn nhầm
    # chỗ thì cid: không phân giải được và Outlook hiện ảnh vỡ.
    for cid, png in (images or []):
        candidate.get_payload()[-1].add_related(
            png, maintype="image", subtype="png", cid=f"<{cid}>",
            filename=f"{cid}.png")

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as s:
            s.starttls()
            s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(candidate)
    except smtplib.SMTPAuthenticationError as e:
        gmail = "gmail" in (settings.smtp_host or "").lower()
        if gmail:
            goi_y = (
                "   1. Dùng mật khẩu Gmail thường thay vì App Password.\n"
                "      Google đã bỏ hẳn 'Quyền truy cập của ứng dụng kém an\n"
                "      toàn' từ 2022 nên mật khẩu thường LUÔN bị từ chối.\n"
                "      -> Bật Xác minh 2 bước, rồi tạo App Password 16 ký tự\n"
                "         tại myaccount.google.com/apppasswords\n"
                "   2. App Password dán còn dấu cách. Google hiển thị nó theo\n"
                "      nhóm 4 ký tự cho dễ đọc; phải dán liền 16 ký tự.\n"
                "   3. Chưa bật Xác minh 2 bước -> trang App Password không\n"
                "      hiện ra chút nào.\n"
                "   4. Sai SMTP_USER (phải là địa chỉ Gmail đầy đủ)."
            )
        else:
            goi_y = (
                "   1. Dùng mật khẩu đăng nhập thay vì App Password. Office 365\n"
                "      không nhận mật khẩu thường khi có MFA — phải tạo App\n"
                "      Password riêng.\n"
                "   2. Công ty đã TẮT SMTP AUTH cho tài khoản của bạn. Microsoft\n"
                "      tắt mặc định, admin phải bật lại từng hộp thư.\n"
                "      -> Nhờ IT bật, hoặc dùng SMTP relay nội bộ / Gmail.\n"
                "   3. Sai SMTP_USER."
            )
        raise MailSendError(
            f"Đăng nhập SMTP thất bại: {e}\n\n"
            f"  Đang dùng {settings.smtp_host}:{settings.smtp_port}\n"
            "  Nguyên nhân thường gặp, theo thứ tự hay gặp nhất:\n" + goi_y
        ) from e
    except smtplib.SMTPException as e:
        raise MailSendError(
            f"Lỗi SMTP: {type(e).__name__}: {e}\n"
            f"  Đang dùng {settings.smtp_host}:{settings.smtp_port}\n"
            "  Nếu là lỗi kết nối: mạng công ty có thể chặn cổng 587."
        ) from e
    except OSError as e:
        raise MailSendError(
            f"Không kết nối được {settings.smtp_host}:{settings.smtp_port} — {e}\n"
            "  Mạng công ty hoặc firewall có thể đang chặn cổng này."
        ) from e


def _subject_line(stats: dict) -> str:
    ky = (stats["from_day"] if stats["from_day"] == stats["to_day"]
          else f"{stats['from_day']} → {stats['to_day']}")
    return (f"[MOOC] Báo cáo {ky} — {stats['total']} chứng chỉ, "
            f"duyệt {stats['approval_rate']:.0f}%")


def send_period_report(from_day: str, to_day: str, bucket: str = "day") -> None:
    """Dựng và GỬI báo cáo cho một khoảng thời gian. Scheduler gọi hàm này."""
    import report_layout

    stats = report.period_report(from_day, to_day, bucket)
    html_body, images = report_layout.build_html(stats)
    _send_smtp(_subject_line(stats), html_body,
               report_layout.build_text(stats), images)


def _parse_day(text: str, flag_name: str) -> str:
    raw = (text or "").strip().replace("/", "-").replace(".", "-")
    part = raw.split("-")
    if len(part) == 3 and all(x.isdigit() for x in part):
        year, month, day = part
        raw = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        # from None: cố ý KHÔNG kèm traceback của ValueError — đây là lỗi gõ
        # sai tham số, người dùng cần câu hướng dẫn chứ không cần ruột hàm.
        raise SystemExit(
            f"{flag_name} không hợp lệ: {text!r}\n"
            f"  Định dạng: YYYY-MM-DD, ví dụ 2026-08-25\n"
            f"  (Thiếu số 0 như 2026-8-25 cũng được, nhưng {text!r} thì không "
            f"đọc được.)"
        ) from None


def _default_period() -> tuple[str, str]:
    end = date.today() - timedelta(days=1)
    return (end - timedelta(days=6)).isoformat(), end.isoformat()


def main():
    p = argparse.ArgumentParser(
        description="Báo cáo xác minh chứng chỉ MOOC qua email")
    p.add_argument("--day", help="Báo cáo đúng MỘT ngày (YYYY-MM-DD)")
    p.add_argument("--from", dest="from_day",
                   help="Ngày bắt đầu (YYYY-MM-DD)")
    p.add_argument("--to", dest="to_day", help="Ngày kết thúc (YYYY-MM-DD)")
    p.add_argument("--bucket", choices=["day", "week", "month"], default=None,
                   help="Mốc gom số liệu trong biểu đồ. Mặc định tự chọn.")
    p.add_argument("--send", action="store_true",
                   help="Gửi thật. Không có cờ này thì chỉ xem trước.")
    p.add_argument("--output", default="bao_cao.html",
                   help="File HTML xem trước (mặc định bao_cao.html)")
    args = p.parse_args()

    if args.day:
        from_day = to_day = _parse_day(args.day, "--day")
    elif args.from_day or args.to_day:
        if not (args.from_day and args.to_day):
            print("Dùng --from và --to cùng nhau, hoặc --day cho một ngày.")
            return 1
        from_day = _parse_day(args.from_day, "--from")
        to_day = _parse_day(args.to_day, "--to")
    else:
        from_day, to_day = _default_period()

    if from_day > to_day:
        print(f"--from ({from_day}) sau --to ({to_day}).")
        return 1

    # Mốc tự chọn theo độ dài kỳ: kỳ dài mà gom theo ngày là rừng cột khó đọc.
    bucket = args.bucket
    if not bucket:
        so_ngay = (date.fromisoformat(to_day)
                   - date.fromisoformat(from_day)).days + 1
        bucket = "day" if so_ngay <= 31 else ("week" if so_ngay <= 180 else "month")

    import report_layout
    stats = report.period_report(from_day, to_day, bucket)
    print(report_layout.build_text(stats))
    print()

    if args.send:
        try:
            html_body, images = report_layout.build_html(stats)
            _send_smtp(_subject_line(stats), html_body,
                       report_layout.build_text(stats), images)
            print(f"Đã gửi email tới {settings.mail_to}: {_subject_line(stats)}")
        except MailSendError as e:
            print(f"KHÔNG GỬI ĐƯỢC:\n{e}")
            return 1
    else:
        html_body, images = report_layout.build_html(stats)
        # Bản xem trước nhúng ảnh dạng data URI để mở bằng trình duyệt là thấy.
        # Email thật KHÔNG dùng cách này — ở đó ảnh đi theo Content-ID.
        import base64
        for cid, png in images:
            html_body = html_body.replace(
                f"cid:{cid}",
                "data:image/png;base64," + base64.b64encode(png).decode())
        Path(args.output).write_text(html_body, encoding="utf-8")
        print(f"Đã ghi bản xem trước: {args.output}")
        if not images:
            print("  (Kỳ chỉ có một mốc nên không có biểu đồ. Muốn xem biểu đồ")
            print("   thì dùng --from/--to cho khoảng nhiều ngày.)")
        print("Mở file đó bằng trình duyệt để xem email sẽ trông thế nào.")
        print("Thêm --send để gửi thật qua email.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
