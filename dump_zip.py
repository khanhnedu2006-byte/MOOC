"""Soi byte thô của API ② và tự dò cách giải mã đúng (dump_zip).

Gọi download-certificates-zip, lưu nguyên response ra đĩa, rồi thử lần lượt
mọi cách giải mã đã biết để xem cách nào lấy lại được file ZIP hợp lệ.

    python dump_zip.py

Chỉ đọc, không nộp gì lên ELIS.

Kết quả in ra cho biết CHÍNH XÁC server đóng gói kiểu gì, để vá đúng chỗ
thay vì đoán.
"""

import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import io
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import requests

import client
from config import settings

FILE_THO = Path("response_tho.bin")


def gach(t=""):
    print("─" * 66)
    if t:
        print(t)
        print("─" * 66)


def la_zip(b) -> bool:
    if not isinstance(b, bytes) or len(b) < 22:
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(b)) as zf:
            zf.namelist()
        return True
    except Exception:
        return False


# ===== Lấy một bản ghi thật =====
gach("Lấy 1 bản ghi WAITING")
ds = client.lay_danh_sach_cho_duyet(page=1, size=1)
if not ds:
    print("Không có bản ghi WAITING nào.")
    raise SystemExit(1)
it = ds[0]
print(f"  id      : {it['id']}")
print(f"  cert_id : {it['certificate_id']}")
print(f"  file    : {it.get('certificateName')}")

# ===== Gọi thô =====
print()
gach("Gọi API ② (không qua client.py)")
url = f"{settings.elis_file_base_url}/api/v1/files/download-certificates-zip"
resp = requests.post(
    url,
    headers={"apikey": settings.elis_api_key,
             "Content-Type": "application/json",
             "Accept": "application/zip, application/json"},
    json=[{"UserCourseId": it["id"], "certificate_id": it["certificate_id"]}],
    timeout=120,
)
than = resp.content
FILE_THO.write_bytes(than)

print(f"  HTTP           : {resp.status_code}")
print(f"  Content-Type   : {resp.headers.get('Content-Type')!r}")
print(f"  Content-Encoding: {resp.headers.get('Content-Encoding')!r}")
print(f"  Số byte        : {len(than):,}")
print(f"  Đã lưu         : {FILE_THO}")

print()
gach("32 BYTE ĐẦU")
print(f"  hex : {than[:32].hex(' ')}")
print(f"  raw : {than[:32]!r}")
_bat_dau_ngoac_pk = than[:3] == b'"PK'
_co_bom = than[:3] == b"\xef\xbb\xbf"
print(f"  Bắt đầu bằng dấu ngoặc kép rồi PK? {_bat_dau_ngoac_pk}")
print(f"  Có BOM UTF-8?                      {_co_bom}")

print()
gach("32 BYTE CUỐI")
print(f"  hex : {than[-32:].hex(' ')}")
print(f"  raw : {than[-32:]!r}")

# ===== Thống kê byte =====
print()
gach("THỐNG KÊ")
n_cao = sum(1 for b in than if b >= 0x80)
n_dk = sum(1 for b in than if b < 0x20 and b not in (9, 10, 13))
_co_escape = b"\\u00" in than
print(f"  Byte >= 0x80 (non-ASCII) : {n_cao:,}")
print(f"  Byte điều khiển thô      : {n_dk:,}")
print(f"  Có escape dạng backslash-u00? {_co_escape}")
try:
    than.decode("utf-8")
    print("  Thân có phải UTF-8 hợp lệ? Có")
except UnicodeDecodeError as e:
    print(f"  Thân có phải UTF-8 hợp lệ? KHÔNG — {str(e)[:60]}")


# ===== Thử mọi cách giải mã =====
def thu(ten, ham):
    try:
        kq = ham()
    except Exception as e:
        return ten, None, f"{type(e).__name__}: {str(e)[:50]}"
    if la_zip(kq):
        return ten, kq, "ZIP HỢP LỆ"
    return ten, None, f"không phải ZIP ({len(kq):,} byte)" if isinstance(kq, bytes) \
        else f"ra {type(kq).__name__}"


def _json_latin1():
    return json.loads(than.decode("latin-1")).encode("latin-1")


def _json_latin1_utf8():
    return json.loads(than.decode("latin-1")).encode("latin-1").decode("utf-8").encode("latin-1")


def _json_utf8():
    return json.loads(than.decode("utf-8")).encode("latin-1")


def _json_utf8_sig():
    return json.loads(than.decode("utf-8-sig")).encode("latin-1")


def _bo_ngoac_kep():
    # Bỏ dấu " hai đầu rồi unescape thủ công, giữ nguyên byte thô.
    lot = than.strip()
    if lot[:1] == b'"' and lot[-1:] == b'"':
        lot = lot[1:-1]
    ra = bytearray()
    i = 0
    while i < len(lot):
        if lot[i:i + 2] == b"\\u":
            ra.append(int(lot[i + 2:i + 6], 16) & 0xFF)
            i += 6
        elif lot[i:i + 1] == b"\\":
            m = {b"n": 10, b"r": 13, b"t": 9, b"b": 8, b"f": 12,
                 b'"': 34, b"\\": 92, b"/": 47}
            ra.append(m.get(lot[i + 1:i + 2], lot[i + 1]))
            i += 2
        else:
            ra.append(lot[i])
            i += 1
    return bytes(ra)


def _base64():
    import base64
    s = json.loads(than.decode("latin-1"))
    return base64.b64decode(s)


def _tim_pk():
    # Cắt từ chữ ký PK đầu tiên trong byte thô.
    vt = than.find(b"PK\x03\x04")
    return than[vt:] if vt >= 0 else b""


print()
gach("THỬ CÁC CÁCH GIẢI MÃ")
CACH = [
    ("1. resp.content là ZIP sẵn", lambda: than),
    ("2. json(latin-1) -> latin-1", _json_latin1),
    ("3. json(latin-1) -> latin-1 -> utf-8 -> latin-1", _json_latin1_utf8),
    ("4. json(utf-8) -> latin-1", _json_utf8),
    ("5. json(utf-8-sig) -> latin-1", _json_utf8_sig),
    ("6. bỏ ngoặc kép + unescape thủ công", _bo_ngoac_kep),
    ("7. base64", _base64),
    ("8. cắt từ chữ ký PK trong byte thô", _tim_pk),
]

thang = None
for ten, ham in CACH:
    ten2, kq, ghi_chu = thu(ten, ham)
    dau = "  OK  " if kq else "  --  "
    print(f"{dau}{ten2:<48} {ghi_chu}")
    if kq and thang is None:
        thang = (ten2, kq)

print()
gach("KẾT LUẬN")
if thang:
    ten, du_lieu = thang
    print(f"  Cách đúng: {ten}")
    print(f"  ZIP: {len(du_lieu):,} byte")
    with zipfile.ZipFile(io.BytesIO(du_lieu)) as zf:
        for n in zf.namelist():
            print(f"    - {n}  ({zf.getinfo(n).file_size:,} byte)")
        if "manifest.json" in zf.namelist():
            print()
            print("  manifest.json:")
            man = json.loads(zf.read("manifest.json"))
            print("  " + json.dumps(man, ensure_ascii=False, indent=2).replace("\n", "\n  "))
else:
    print("  KHÔNG cách nào lấy được ZIP. Phân tích sâu:")
    print()

    # Vị trí byte non-ASCII đầu tiên — cho biết server xử lý byte cao thế nào.
    vt = next((i for i, b in enumerate(than) if b >= 0x80), None)
    if vt is not None:
        d, c = max(0, vt - 24), min(len(than), vt + 24)
        print(f"  Byte >= 0x80 đầu tiên ở vị trí {vt}:")
        print(f"    hex : {than[d:c].hex(' ')}")
        print(f"    raw : {than[d:c]!r}")
        print(f"    (byte đó là 0x{than[vt]:02x})")
        print()

    # json.loads có chạy không, và ra cái gì.
    try:
        gt = json.loads(than.decode("latin-1"))
        print(f"  json.loads(latin-1) -> {type(gt).__name__}, "
              f"{len(gt) if hasattr(gt, '__len__') else '?'} phần tử")
        if isinstance(gt, str):
            qua = [c for c in gt if ord(c) > 255]
            print(f"    ký tự > U+00FF: {len(qua)}")
            if qua:
                print(f"    ví dụ: {[hex(ord(c)) for c in qua[:8]]}")
            print(f"    16 ký tự đầu: {gt[:16]!r}")
            try:
                bb = gt.encode("latin-1")
                print(f"    encode latin-1 -> {len(bb):,} byte, "
                      f"bắt đầu {bb[:8]!r}, là ZIP: {la_zip(bb)}")
            except Exception as e:
                print(f"    encode latin-1 lỗi: {e}")
    except Exception as e:
        print(f"  json.loads(latin-1) LỖI: {type(e).__name__}: {str(e)[:100]}")

    print()
    print(f"  200 byte đầu (hex):")
    for i in range(0, min(200, len(than)), 32):
        print(f"    {i:04d}  {than[i:i+32].hex(' ')}")

    print()
    print(f"  Đã lưu nguyên response vào {FILE_THO} ({len(than):,} byte).")
    print("  Gửi phần in ở trên cho tôi là đủ để phân tích.")
print()
