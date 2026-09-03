"""Gửi báo cáo xác minh chứng chỉ qua email (send_report).

    python send_report.py                        # xem trước 7 ngày gần nhất
    python send_report.py --day 2026-08-17       # đúng một ngày
    python send_report.py --from 2026-08-01 --to 2026-08-31 --bucket week
    python send_report.py --send                 # gửi thật

Mặc định là XEM TRƯỚC — phải thêm --send mới gửi. Cố ý như vậy để chạy thử
không lỡ gửi mail cho mentor.

CHỈ CÒN MỘT BỘ DỰNG BÁO CÁO: report_layout.py. Trước đây file này có bộ dựng
HTML riêng cho báo cáo NGÀY, song song với report_layout lo báo cáo kỳ — hai
bản phải sửa đồng bộ bằng tay, và đúng như dự đoán, sửa một bên là bên kia
lệch ngay. Giờ báo cáo ngày chỉ là báo cáo kỳ với from = to.

Phần dựng nội dung và phần gửi tách rời nhau: đổi cách gửi (SMTP relay nội
bộ, Microsoft Graph...) chỉ cần viết thêm một hàm gửi, không đụng tới chỗ
dựng nội dung.
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
    """Không gửi được báo cáo.

    Lỗi RIÊNG chứ không dùng Exception chung: scheduler bắt nó để biết "gửi
    hỏng, thử lại vòng sau" thay vì coi như lỗi lập trình. Thông điệp của
    exception này viết cho người vận hành đọc, không phải cho lập trình viên
    — nên nó liệt kê luôn nguyên nhân thường gặp và cách xử lý.
    """

def _send_smtp(title: str, html_body: str, text_body: str,
               images: list | None = None) -> None:
    """Gửi báo cáo qua SMTP (mặc định Office 365).

    Gửi email nhiều phần (multipart/alternative): bản chữ thuần và bản HTML
    trong cùng một thư. Ứng dụng mail nào đọc được HTML thì hiện bản đẹp,
    không thì rơi về bản chữ. Outlook luôn chọn HTML.

    images: [(cid, png_bytes)] — biểu đồ, đính kèm INLINE theo Content-ID.
    Phải đính kèm chứ không được để link http: Outlook mặc định chặn ảnh tải
    từ internet, người đọc sẽ thấy một ô trống kèm dòng "Click here to
    download pictures". Ảnh nằm trong thư thì không bị chặn.

    LƯU Ý VỀ HẠN SỬ DỤNG: Microsoft đang khai tử Basic Auth cho SMTP AUTH
    trên Exchange Online, mốc hiện tại là 31/12/2026. Sau đó cách này ngừng
    hoạt động và phải chuyển sang Microsoft Graph API hoặc SMTP relay nội
    bộ. Phần dựng nội dung ở trên không phụ thuộc cách gửi nên lúc đó chỉ
    cần viết thêm một hàm gửi khác.
    """
    import smtplib
    from email.message import EmailMessage

    missing = [name for name, ground_truth in [
        ("SMTP_HOST", settings.smtp_host),
        ("SMTP_USER", settings.smtp_user),
        ("SMTP_PASSWORD", settings.smtp_password),
        ("MAIL_TO", settings.mail_to),
    ] if not ground_truth]
    if missing:
        raise MailSendError(
            "Thiếu cấu hình trong .env: " + ", ".join(missing) + "\n"
            "  SMTP_USER     = email công ty của bạn\n"
            "  SMTP_PASSWORD = App Password (KHÔNG phải mật khẩu đăng nhập)\n"
            "  MAIL_TO      = email mentor\n"
            "  Bỏ --send để xem trước nội dung mà không cần cấu hình."
        )

    recipient = [e.strip() for e in settings.mail_to.split(",") if e.strip()]
    sender = settings.mail_from or settings.smtp_user

    candidate = EmailMessage()
    candidate["Subject"] = title
    candidate["From"] = sender
    candidate["To"] = ", ".join(recipient)
    candidate.set_content(text_body)                      # bản chữ thuần
    candidate.add_alternative(html_body, subtype="html")  # bản HTML

    # Gắn ảnh vào ĐÚNG phần HTML (payload cuối), không phải vào thư gốc —
    # gắn nhầm chỗ thì cid: trong HTML không phân giải được và Outlook hiện
    # ảnh vỡ.
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


def _tieu_de(stats: dict) -> str:
    ky = (stats["from_day"] if stats["from_day"] == stats["to_day"]
          else f"{stats['from_day']} → {stats['to_day']}")
    return (f"[MOOC] Báo cáo {ky} — {stats['total']} chứng chỉ, "
            f"duyệt {stats['approval_rate']:.0f}%")


def gui_bao_cao_ky(from_day: str, to_day: str, bucket: str = "day") -> None:
    """Dựng và GỬI báo cáo cho một khoảng thời gian. Scheduler gọi hàm này."""
    import report_layout

    stats = report.period_report(from_day, to_day, bucket)
    html_body, images = report_layout.build_html(stats)
    _send_smtp(_tieu_de(stats), html_body,
               report_layout.build_text(stats), images)


def _doc_ngay(chuoi: str, ten_co: str) -> str:
    """Đọc ngày từ dòng lệnh, chuẩn hóa về YYYY-MM-DD.

    CHẤP NHẬN thiếu số 0 ("2026-8-5") và dấu gạch chéo ("2026/08/05"), rồi tự
    chuẩn hóa. Người gõ tay rất hay bỏ số 0, và bắt họ gõ lại chỉ vì thiếu một
    ký tự là phiền vô ích.

    NHƯNG PHẢI CHUẨN HÓA chứ không chỉ chấp nhận: truy vấn so ngày bằng CHUỖI
    (substr(created_at,1,10) BETWEEN ...), nên '2026-8-25' sẽ không khớp với
    '2026-08-25' trong DB — báo cáo ra rỗng mà không có lỗi nào.

    Sai thật thì báo một dòng rõ ràng, không đổ traceback vào mặt người dùng.
    """
    raw = (chuoi or "").strip().replace("/", "-").replace(".", "-")
    phan = raw.split("-")
    if len(phan) == 3 and all(x.isdigit() for x in phan):
        nam, thang, ngay = phan
        raw = f"{int(nam):04d}-{int(thang):02d}-{int(ngay):02d}"
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        # from None: cố ý KHÔNG kèm traceback của ValueError. Đây là lỗi gõ
        # sai tham số dòng lệnh, người dùng cần một câu hướng dẫn chứ không
        # cần thấy ruột của date.fromisoformat.
        raise SystemExit(
            f"{ten_co} không hợp lệ: {chuoi!r}\n"
            f"  Định dạng: YYYY-MM-DD, ví dụ 2026-08-25\n"
            f"  (Thiếu số 0 như 2026-8-25 cũng được, nhưng {chuoi!r} thì không "
            f"đọc được.)"
        ) from None


def _khoang_mac_dinh() -> tuple[str, str]:
    """7 ngày gần nhất, kết thúc HÔM QUA.

    Vì sao không phải một ngày: biểu đồ đường một điểm thì vô nghĩa, và con
    số một ngày không cho biết nó cao hay thấp so với bình thường. Bảy ngày
    vừa đủ để thấy xu hướng mà vẫn là "báo cáo gần đây".

    Kết thúc hôm qua vì hôm nay chưa chạy hết — số liệu ngày đang dở luôn
    thấp hơn thực tế và làm người đọc tưởng khối lượng đang giảm.
    """
    den = date.today() - timedelta(days=1)
    return (den - timedelta(days=6)).isoformat(), den.isoformat()


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
        from_day = to_day = _doc_ngay(args.day, "--day")
    elif args.from_day or args.to_day:
        if not (args.from_day and args.to_day):
            print("Dùng --from và --to cùng nhau, hoặc --day cho một ngày.")
            return 1
        from_day = _doc_ngay(args.from_day, "--from")
        to_day = _doc_ngay(args.to_day, "--to")
    else:
        from_day, to_day = _khoang_mac_dinh()

    if from_day > to_day:
        print(f"--from ({from_day}) sau --to ({to_day}).")
        return 1

    # Mốc tự chọn theo độ dài kỳ: khoảng dài mà gom theo ngày thì biểu đồ
    # thành một rừng cột không đọc được.
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
            _send_smtp(_tieu_de(stats), html_body,
                       report_layout.build_text(stats), images)
            print(f"Đã gửi email tới {settings.mail_to}: {_tieu_de(stats)}")
        except MailSendError as e:
            print(f"KHÔNG GỬI ĐƯỢC:\n{e}")
            return 1
    else:
        html_body, images = report_layout.build_html(stats)
        # Bản xem trước: nhúng ảnh thẳng vào HTML dưới dạng data URI để mở
        # bằng trình duyệt là thấy ngay. Email thật KHÔNG dùng cách này —
        # ở đó ảnh đi kèm theo Content-ID (xem _send_smtp).
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
