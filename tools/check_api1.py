"""Soi riêng API ① getCert của eLIS (check_api1).

    python tools/check_api1.py                  # gọi thử lấy hàng đợi WAITING
    python tools/check_api1.py --email a@fpt.com   # gọi thử tra lịch sử 1 người
    python tools/check_api1.py --curl           # in lệnh curl tương đương

KHÔNG phụ thuộc src/ — tự đọc .env, chỉ cần thư viện requests. Nhờ vậy vẫn
chạy được khi config.py đang hỏng, và dựng ĐÚNG request mà src/client.py gửi
nên kết quả ở đây phản ánh đúng hệ thống thật.

KHÔNG BAO GIỜ in giá trị key, chỉ nói có hay không và dài bao nhiêu.
"""

import argparse
import ipaddress
import json
import os
import socket
import ssl
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    sys.exit("Thiếu thư viện requests:  pip install requests")

GOC = Path(__file__).resolve().parent.parent
ENV = GOC / ".env"

DUONG_DAN_API1 = "/api/v1/UserCourse/elearning/getCert"

XANH, DO, VANG, XAM, HET = "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[0m"
if os.name == "nt" and not os.environ.get("WT_SESSION"):
    XANH = DO = VANG = XAM = HET = ""     # cmd.exe cũ không hiểu mã màu


def ok(s):   print(f"  {XANH}[OK]{HET}   {s}")
def loi(s):  print(f"  {DO}[LỖI]{HET}  {s}")
def canh(s): print(f"  {VANG}[LƯU Ý]{HET} {s}")
def mo(s):   print(f"  {XAM}{s}{HET}")


def muc(tieu_de):
    print(f"\n=== {tieu_de} ===")


# ---------------------------------------------------------------- đọc .env

def doc_env(path: Path) -> dict:
    """Đọc .env thủ công. Không dùng dotenv để script chạy được ở máy sạch."""
    out = {}
    if not path.exists():
        return out
    for dong in path.read_text(encoding="utf-8-sig").splitlines():
        dong = dong.strip()
        if not dong or dong.startswith("#") or "=" not in dong:
            continue
        ten, _, gia_tri = dong.partition("=")
        out[ten.strip()] = gia_tri.strip()
    return out


def soi_gia_tri(ten: str, tho: str) -> str:
    """Trả về giá trị đã dọn, và cảnh báo những kiểu bẩn hay gặp trong .env."""
    sach = tho.strip()

    if len(sach) >= 2 and sach[0] == sach[-1] and sach[0] in "\"'":
        canh(f"{ten} đang bọc trong dấu nháy — pydantic giữ nguyên cả dấu nháy "
             f"làm một phần của giá trị. Bỏ dấu nháy đi.")
        sach = sach[1:-1]

    if tho != tho.strip():
        canh(f"{ten} có khoảng trắng thừa ở đầu/cuối dòng.")

    if any(c in sach for c in "\r\n\t"):
        canh(f"{ten} lẫn ký tự xuống dòng hoặc tab.")

    return sach


# ------------------------------------------------------------- mạng cơ bản

def la_noi_bo(ip: str) -> bool:
    """Địa chỉ thuộc dải riêng (RFC1918 / loopback / link-local)?"""
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


def thu_mang(url: str, timeout: float):
    """Phân giải tên miền rồi bắt tay TLS. Tách riêng để biết hỏng ở tầng nào.

    Hỏng ở đây thì mọi mã HTTP bên dưới đều vô nghĩa.
    Trả về danh sách IP đích, hoặc None nếu không nối được.
    """
    u = urlparse(url)
    host = u.hostname
    port = u.port or (443 if u.scheme == "https" else 80)

    if not host:
        loi(f"Không đọc được tên miền từ ELIS_BASE_URL: {url!r}")
        return None

    try:
        dia_chi = sorted({t[4][0] for t in socket.getaddrinfo(host, port)})
        ok(f"Phân giải {host} -> {', '.join(dia_chi)}")
    except socket.gaierror as e:
        loi(f"Không phân giải được tên miền {host}: {e}\n"
            "         Server không ra được DNS, hoặc cần cấu hình proxy.\n"
            "         Trong Docker: container có DNS riêng, phân giải được ở "
            "máy chủ KHÔNG có nghĩa là phân giải được trong container.")
        return None

    try:
        t0 = time.time()
        with socket.create_connection((host, port), timeout=timeout) as s:
            if u.scheme == "https":
                ctx = ssl.create_default_context()
                with ctx.wrap_socket(s, server_hostname=host) as ts:
                    cert = ts.getpeercert()
                    het_han = cert.get("notAfter", "?") if cert else "?"
                ok(f"Bắt tay TLS xong sau {time.time() - t0:.2f}s "
                   f"(chứng thư hết hạn {het_han})")
            else:
                ok(f"Nối TCP xong sau {time.time() - t0:.2f}s (http, không TLS)")
        return dia_chi
    except ssl.SSLError as e:
        loi(f"Lỗi TLS: {e}\n"
            "         Hay gặp khi mạng công ty chặn giữa bằng proxy tự ký.")
        return None
    except (socket.timeout, TimeoutError):
        loi(f"Hết {timeout:.0f}s mà không nối được {host}:{port}.\n"
            "         Tường lửa chặn im lặng, hoặc server không ra được Internet.")
        return None
    except OSError as e:
        loi(f"Không nối được {host}:{port} — {e}")
        return None


def in_proxy():
    """Proxy đặt qua biến môi trường là thứ hay bị quên khi so máy này với máy kia."""
    co = {k: v for k, v in os.environ.items()
          if k.lower() in ("http_proxy", "https_proxy", "no_proxy")}
    if co:
        for k, v in sorted(co.items()):
            mo(f"{k}={v[:100] + '…' if len(v) > 100 else v}")
    else:
        mo("Không đặt HTTP_PROXY/HTTPS_PROXY.")


def ip_nguon_toi(host: str, port: int) -> str | None:
    """IP nguồn hệ điều hành THỰC SỰ dùng để đi tới host này.

    Máy nhiều card mạng (LAN + Wi-Fi + VPN) chọn card nào là do bảng định
    tuyến quyết định. Đây mới là IP eLIS nhìn thấy khi đích nằm trong mạng
    nội bộ. Mở socket UDP nên không gửi gói nào ra ngoài.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, port))
            return s.getsockname()[0]
    except OSError:
        return None


def in_ip_nguon(url: str, dia_chi_dich: list, timeout: float):
    """eLIS lọc theo IP, nên phải biết nó nhìn thấy máy này bằng IP nào.

    Đích nội bộ và đích Internet cho hai câu trả lời khác hẳn nhau — nói nhầm
    là đi xin allowlist sai IP.
    """
    u = urlparse(url)
    host, port = u.hostname, u.port or (443 if u.scheme == "https" else 80)

    nguon = ip_nguon_toi(host, port)
    if nguon:
        ok(f"IP nguồn đi tới {host}: {nguon}")

    if all(la_noi_bo(ip) for ip in dia_chi_dich):
        mo("Đích nằm trong dải NỘI BỘ, lưu lượng không ra Internet nên không "
           "qua NAT.")
        mo(f"-> eLIS nhìn thấy máy này bằng đúng IP {nguon or 'nguồn ở trên'}. "
           f"Cần allowlist thì xin IP đó.")
        return

    for dich_vu in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            r = requests.get(dich_vu, timeout=min(timeout, 5))
            if r.ok and r.text.strip():
                ok(f"IP công khai sau NAT: {r.text.strip()}  "
                   f"(đích ở ngoài Internet nên eLIS thấy IP này)")
                return
        except requests.RequestException:
            continue
    mo("Không hỏi được IP công khai — máy này không ra Internet công cộng.")


# --------------------------------------------------------------- gọi API ①

def goi(url, headers, params, timeout, nhan=""):
    """Gọi một lần, in đầy đủ những gì nhận được. Trả về response hoặc None."""
    print()
    mo(f"GET {url}")
    mo(f"    params={params}")
    mo(f"    headers={{{', '.join(headers)}}}  {nhan}")

    t0 = time.time()
    try:
        r = requests.get(url, headers=headers, params=params, timeout=timeout)
    except requests.exceptions.Timeout:
        loi(f"Hết {timeout:.0f}s mà eLIS chưa trả lời.")
        return None
    except requests.exceptions.SSLError as e:
        loi(f"Lỗi TLS: {e}")
        return None
    except requests.RequestException as e:
        loi(f"Lỗi kết nối: {type(e).__name__}: {e}")
        return None

    giay = time.time() - t0
    mau = XANH if r.status_code == 200 else DO
    print(f"  {mau}HTTP {r.status_code}{HET}  ({giay:.2f}s, {len(r.content)} byte)")

    for h in ("Content-Type", "Server", "WWW-Authenticate", "X-Cache",
              "CF-Ray", "Retry-After"):
        if h in r.headers:
            mo(f"    {h}: {r.headers[h]}")

    return r


def doc_than_response(r):
    """In nội dung trả về. JSON thì in gọn, HTML thì nói thẳng là HTML."""
    kieu = (r.headers.get("Content-Type") or "").lower()

    if "json" in kieu:
        try:
            body = r.json()
        except ValueError:
            canh("Content-Type nói JSON nhưng nội dung không parse được.")
            mo(r.text[:400])
            return None

        if isinstance(body, dict):
            tom_tat = {k: v for k, v in body.items() if k != "data"}
            mo("    " + json.dumps(tom_tat, ensure_ascii=False)[:400])
            data = body.get("data")
            if isinstance(data, list):
                ok(f"data[] có {len(data)} bản ghi")
                if data:
                    mo("    Bản ghi đầu: " +
                       json.dumps(data[0], ensure_ascii=False)[:300])
        return body

    if "html" in kieu:
        canh("eLIS trả về HTML, không phải JSON — gần như chắc chắn là trang "
             "lỗi của gateway/proxy chứ không phải của chính API.")
    mo(r.text[:400].replace("\n", " "))
    return None


def chan_doan(r, co_key: bool):
    """Dịch mã lỗi sang việc cần làm."""
    ma = r.status_code

    if ma == 200:
        return

    print()
    if ma in (401, 403):
        loi(f"HTTP {ma} — bị từ chối.")
        print("         Hai nguyên nhân rất khác nhau, script thử tách bên dưới:")
        print("           · IP máy này chưa nằm trong allowlist của eLIS")
        print("             -> phải xin bên eLIS thêm IP, không có cách vòng qua")
        print("           · ELIS_API_KEY sai hoặc đã bị thu hồi")
    elif ma == 404:
        loi("HTTP 404 — sai đường dẫn.")
        print(f"         Kiểm ELIS_BASE_URL. Đường dẫn đúng là {DUONG_DAN_API1}")
        print("         (thừa dấu / ở cuối ELIS_BASE_URL cũng gây 404)")
    elif ma == 405:
        loi("HTTP 405 — đúng đường dẫn nhưng sai phương thức. eLIS có thể đã "
            "đổi hợp đồng API.")
    elif ma == 429:
        loi("HTTP 429 — bị giới hạn tần suất. Đợi rồi thử lại.")
    elif 500 <= ma < 600:
        loi(f"HTTP {ma} — lỗi phía eLIS, không phải phía mình.")
        print("         Thử lại sau; nếu kéo dài thì báo bên eLIS.")
    else:
        loi(f"HTTP {ma} — chưa có chẩn đoán sẵn cho mã này.")

    if not co_key:
        canh("ELIS_API_KEY đang rỗng, nên mã lỗi trên chưa nói được gì về key.")


def thu_khong_key(url, params, timeout):
    """Gọi LẠI nhưng bỏ header apikey.

    Mẹo tách nguyên nhân: cùng một mã lỗi cho cả hai lần nghĩa là chặn xảy ra
    TRƯỚC khi ai đó nhìn tới key — tức chặn theo IP ở cổng vào. Mã khác nhau
    thì key có được đọc, và vấn đề nằm ở chính cái key.
    """
    muc("Tách nguyên nhân: gọi lại KHÔNG kèm apikey")
    print("  Cùng mã lỗi  -> chặn ở cổng vào, theo IP.")
    print("  Khác mã lỗi  -> key có được đọc, vấn đề nằm ở key.")
    return goi(url, {"Accept": "application/json"}, params, timeout,
               nhan="(cố ý bỏ apikey)")


def in_curl(url, params, timeout):
    """Lệnh curl tương đương, che key — để gửi cho bên eLIS mà không lộ gì."""
    q = "&".join(f"{k}={v}" for k, v in params.items())
    print()
    print(f'curl -i --max-time {int(timeout)} \\')
    print(f'  -H "apikey: <KEY-CUA-BAN>" \\')
    print(f'  -H "Accept: application/json" \\')
    print(f'  "{url}?{q}"')
    print()


# ------------------------------------------------------------------- chính

def main() -> int:
    p = argparse.ArgumentParser(description="Soi riêng API ① getCert của eLIS.")
    p.add_argument("--status", default="WAITING",
                   help="Trạng thái cần lấy (mặc định WAITING, đúng như job).")
    p.add_argument("--email", default=None,
                   help="Gọi kiểu tra lịch sử một người thay vì lấy hàng đợi.")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--size", type=int, default=100)
    p.add_argument("--timeout", type=float, default=None,
                   help="Giây. Mặc định lấy TIMEOUT_SECONDS trong .env.")
    p.add_argument("--curl", action="store_true",
                   help="Chỉ in lệnh curl tương đương rồi thoát.")
    args = p.parse_args()

    muc("1. Cấu hình")
    if not ENV.exists():
        loi(f"Không thấy {ENV}")
        print("         Chép .env từ máy dev sang (đừng commit vào git).")
        return 1
    ok(f"Đọc {ENV}")

    env = doc_env(ENV)
    # Biến môi trường thắng .env, giống thứ tự của pydantic-settings.
    base_url = soi_gia_tri("ELIS_BASE_URL",
                           os.environ.get("ELIS_BASE_URL")
                           or env.get("ELIS_BASE_URL", ""))
    api_key = soi_gia_tri("ELIS_API_KEY",
                          os.environ.get("ELIS_API_KEY")
                          or env.get("ELIS_API_KEY", ""))
    timeout = args.timeout or float(env.get("TIMEOUT_SECONDS") or 60)

    if not base_url:
        loi("ELIS_BASE_URL rỗng.")
        return 1
    if base_url.endswith("/"):
        canh("ELIS_BASE_URL có dấu / ở cuối — nối đường dẫn sẽ thành '//', "
             "một số gateway trả 404 vì chuyện này.")
        base_url = base_url.rstrip("/")
    ok(f"ELIS_BASE_URL = {base_url}")

    if api_key:
        ok(f"ELIS_API_KEY có mặt ({len(api_key)} ký tự)")
    else:
        canh("ELIS_API_KEY rỗng — vẫn gọi thử để xem eLIS trả gì.")

    mo(f"Timeout {timeout:.0f}s")
    in_proxy()

    url = base_url + DUONG_DAN_API1
    if args.email:
        params = {"employeeEmail": args.email, "page": args.page,
                  "size": args.size}
        mo("Kiểu gọi: tra lịch sử một người (giống run.py::completed_courses)")
    else:
        params = {"status": args.status, "page": args.page, "size": args.size}
        mo("Kiểu gọi: lấy hàng đợi (giống run.py::process_one_round)")

    if args.curl:
        in_curl(url, params, timeout)
        return 0

    muc("2. Mạng")
    dia_chi_dich = thu_mang(base_url, timeout)
    if not dia_chi_dich:
        print()
        loi("Chưa nối được tới eLIS. Mọi thứ bên dưới sẽ vô nghĩa nên dừng tại đây.")
        return 1
    in_ip_nguon(base_url, dia_chi_dich, timeout)

    muc("3. Gọi API ①")
    headers = {"apikey": api_key, "Accept": "application/json"}
    r = goi(url, headers, params, timeout)
    if r is None:
        return 1

    doc_than_response(r)
    chan_doan(r, co_key=bool(api_key))

    if r.status_code in (401, 403) and api_key:
        r2 = thu_khong_key(url, params, timeout)
        print()
        if r2 is None:
            canh("Lần gọi không key bị lỗi mạng, chưa tách được nguyên nhân.")
        elif r2.status_code == r.status_code:
            loi(f"Cả hai lần đều HTTP {r.status_code} -> chặn THEO IP.")
            print("         Key không phải thủ phạm. Phải xin bên eLIS thêm IP "
                  "của máy này vào allowlist.")
        else:
            loi(f"Có key: {r.status_code} | Không key: {r2.status_code} "
                f"-> key CÓ được đọc.")
            print("         Vấn đề nằm ở ELIS_API_KEY: sai, hết hạn, hoặc "
                  "không đủ quyền.")

    muc("Kết luận")
    if r.status_code == 200:
        ok("API ① gọi được từ máy này.")
        print("     Vẫn lỗi khi chạy job thì vấn đề nằm ở chỗ khác, không "
              "phải ở API ①.")
        return 0

    loi("API ① KHÔNG gọi được từ máy này.")
    print("     Gửi cho bên eLIS lệnh curl tương đương (key đã che):")
    in_curl(url, params, timeout)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
