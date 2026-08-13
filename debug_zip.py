"""Soi response thô của API ② (debug_zip).

Gọi download-certificates-zip và in ra MỌI thứ nhận được: status code,
headers, kiểu dữ liệu, vài trăm byte đầu. Dùng để hiểu ELIS thực sự trả về
cái gì khi client.py báo lỗi khó hiểu.

    python debug_zip.py

Chỉ đọc, không nộp gì, không đổi trạng thái bản ghi nào.
"""

import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import requests

import client
from config import settings


def gach(t=""):
    print("─" * 64)
    if t:
        print(t)
        print("─" * 64)


gach("Lấy 1 bản ghi WAITING để có id thật")
body = client.lay_mot_trang_cho_duyet(page=1, size=1)
items = body.get("data") or []
if not items:
    print("Không có bản ghi WAITING nào để thử.")
    raise SystemExit(1)

it = items[0]
print(f"  id      : {it['id']}")
print(f"  cert_id : {it['certificate_id']}")
print(f"  file    : {it.get('certificateName')}")

payload = [{"UserCourseId": it["id"], "certificate_id": it["certificate_id"]}]

print()
gach("Gửi request THÔ (không qua client.py)")
url = settings.url_file("/api/v1/files/download-certificates-zip")
headers = {
    settings.api_key_header: settings.khoa_file,
    "Content-Type": "application/json",
    "Accept": "application/zip, application/json",
}
print(f"  POST {url}")
print(f"  Body: {json.dumps(payload)}")

resp = requests.post(url, headers=headers, json=payload, timeout=120)

print()
gach("RESPONSE")
print(f"  HTTP status  : {resp.status_code} {resp.reason}")
print(f"  Content-Type : {resp.headers.get('Content-Type')!r}")
print(f"  Content-Length: {resp.headers.get('Content-Length')}")
print(f"  Số byte thực : {len(resp.content):,}")
print()
print("  Toàn bộ response headers:")
for k, v in resp.headers.items():
    print(f"    {k}: {v}")

print()
gach("NỘI DUNG")

import io
import zipfile

dau = resp.content[:16]
print(f"  16 byte đầu (hex): {dau.hex(' ')}")
print(f"  16 byte đầu (raw): {dau!r}")   # repr -> không làm loạn terminal
print(f"  Có chữ ký 'PK' ở đầu? {resp.content[:2] == b'PK'}")

# Tìm 'PK' ở đâu đó trong 200 byte đầu (phòng khi có rác chèn trước).
vi_tri_pk = resp.content[:200].find(b"PK\x03\x04")
if vi_tri_pk > 0:
    print(f"  Tìm thấy 'PK' ở offset {vi_tri_pk} (có {vi_tri_pk} byte rác phía trước)")

mo_duoc_zip = client._co_phai_zip(resp.content)
print(f"  zipfile mở được?   {mo_duoc_zip}")

if mo_duoc_zip:
    print()
    print("  >>> ĐÂY LÀ FILE ZIP HỢP LỆ.")
    if resp.content[:2] != b"PK":
        print("  >>> Nhưng KHÔNG bắt đầu bằng 'PK' — có byte lạ ở đầu stream.")
    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            print(f"  Giải nén OK, {len(zf.namelist())} entry:")
            for n in zf.namelist():
                thong_tin = zf.getinfo(n)
                print(f"    - {n}  ({thong_tin.file_size:,} bytes)")
            if "manifest.json" in zf.namelist():
                print()
                print("  manifest.json:")
                man = json.loads(zf.read("manifest.json"))
                print("  " + json.dumps(man, ensure_ascii=False, indent=2).replace("\n", "\n  "))
    except Exception as e:
        print(f"  Nhưng đọc chi tiết lỗi: {e}")
else:
    print()
    print("  >>> KHÔNG phải ZIP. Thử đọc dạng text/JSON:")
    text = resp.text
    print(f"  Độ dài text: {len(text)}")
    print()
    print("  500 ký tự đầu:")
    print("  " + repr(text[:500]))
    print()
    try:
        data = resp.json()
        print(f"  Parse JSON được. Kiểu Python: {type(data).__name__}")
        print()
        if isinstance(data, str):
            print("  >>> JSON là một CHUỖI (không phải object) — đây là nguyên nhân lỗi.")
            print("  Nội dung chuỗi:")
            print("  " + repr(data[:500]))
            print()
            try:
                trong = json.loads(data)
                print("  Chuỗi này parse tiếp được thành:")
                print("  " + json.dumps(trong, ensure_ascii=False, indent=2).replace("\n", "\n  "))
            except Exception:
                print("  (chuỗi này không phải JSON lồng)")
        else:
            print("  " + json.dumps(data, ensure_ascii=False, indent=2).replace("\n", "\n  "))
    except Exception as e:
        print(f"  Không parse được JSON: {e}")

print()
gach("XONG")
