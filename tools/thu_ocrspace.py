"""Thử OCR.space trên chứng chỉ thật (thu_ocrspace).

    python tools/thu_ocrspace.py anh.png                  # xem kỹ một ảnh
    python tools/thu_ocrspace.py anh.png --engines 2,3    # so hai engine
    python tools/thu_ocrspace.py --thu-muc data/image     # chạy cả bộ, chấm điểm

CHẠY CẢ BỘ: tên file có dạng <MÃ NV>_<TÊN KHÓA HỌC>.<đuôi>, nên phần sau dấu
gạch dưới đầu tiên chính là ĐÁP ÁN. Script đối chiếu tên khóa học đó với chữ
OCR đọc được rồi chấm theo tỉ lệ từ khớp — không phải đọc tay 190 kết quả.

Bản đọc đầy đủ của từng ảnh được ghi ra thư mục riêng, kèm một file CSV để mở
bằng Excel mà lọc.

KHÔNG phụ thuộc src/ — chỉ cần requests và Pillow.
Lấy key: --key > OCRSPACE_API_KEY (env) > .env. Đăng ký: https://ocr.space/ocrapi
"""

import argparse
import csv
import io
import os
import sys
import time
import unicodedata
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Thiếu thư viện requests:  pip install requests")

try:
    from PIL import Image
except ImportError:
    Image = None

GOC = Path(__file__).resolve().parent.parent
ENV = GOC / ".env"
URL = "https://api.ocr.space/parse/image"

# Giới hạn bậc miễn phí của OCR.space.
GIOI_HAN_BYTE = 1024 * 1024
GIOI_HAN_TRANG_PDF = 3

# Dưới ngưỡng này OCR bắt đầu đọc sai dấu tiếng Việt — đo trên chứng chỉ mẫu:
# 800px đọc đúng 3/3 trường, 700px chỉ còn 2/3.
RONG_TOI_THIEU = 800

# Dưới tỉ lệ này coi như đọc hỏng, cần xem bằng mắt.
NGUONG_DAT = 0.8

ANH = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".gif", ".pdf"}

XANH, DO, VANG, XAM, HET = "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[0m"
if os.name == "nt" and not os.environ.get("WT_SESSION"):
    XANH = DO = VANG = XAM = HET = ""


def ok(s):   print(f"  {XANH}[OK]{HET}   {s}")
def loi(s):  print(f"  {DO}[LỖI]{HET}  {s}")
def canh(s): print(f"  {VANG}[LƯU Ý]{HET} {s}")
def mo(s):   print(f"  {XAM}{s}{HET}")


# ------------------------------------------------------------------ cấu hình

def doc_env(path: Path) -> dict:
    out = {}
    if not path.exists():
        return out
    for dong in path.read_text(encoding="utf-8-sig").splitlines():
        dong = dong.strip()
        if dong and not dong.startswith("#") and "=" in dong:
            ten, _, gt = dong.partition("=")
            out[ten.strip()] = gt.strip().strip("\"'")
    return out


def lay_cau_hinh(ten: str, tham_so=None, mac_dinh=None) -> tuple:
    """Thứ tự: dòng lệnh > biến môi trường > .env > mặc định.

    Giống thứ tự của pydantic-settings trong src/config.py, để thử ở đây ra
    kết quả nào thì chạy thật cũng ra kết quả đó.
    """
    if tham_so:
        return tham_so, "dòng lệnh"
    if os.environ.get(ten):
        return os.environ[ten], "biến môi trường"
    gt = doc_env(ENV).get(ten)
    if gt:
        return gt, ".env"
    return mac_dinh, "mặc định"


# -------------------------------------------------------------- chấm điểm

def bo_dau(s: str) -> str:
    """Bỏ dấu tiếng Việt, hạ chữ thường, mọi ký tự lạ thành khoảng trắng.

    Chấm điểm KHÔNG xét dấu: tên khóa học trong tên file hay bị thay dấu hai
    chấm bằng gạch dưới, viết hoa khác ảnh. Xét dấu thì điểm thấp vì lý do
    không liên quan tới chất lượng OCR.
    """
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return "".join(c if c.isalnum() else " " for c in s)


def tu_khoa(s: str) -> list:
    """Tách thành từ, bỏ từ quá ngắn vì chúng khớp bừa."""
    return [t for t in bo_dau(s).split() if len(t) > 1]


def khoa_hoc_tu_ten_file(p: Path) -> str:
    """Tên file dạng <MÃ NV>_<TÊN KHÓA HỌC> — cắt ở gạch dưới ĐẦU TIÊN.

    Tên khóa học có thể chứa thêm gạch dưới (vd "Mở Khoá AI_cẩm nang viết
    Prompt"), nên chỉ được cắt một lần.
    """
    ten = p.stem
    return ten.split("_", 1)[1].strip() if "_" in ten else ten


def cham_diem(text: str, mong_doi: str) -> tuple:
    """Bao nhiêu từ của tên khóa học xuất hiện trong chữ OCR đọc được."""
    can = tu_khoa(mong_doi)
    if not can:
        return 0, 0, []
    co = set(tu_khoa(text))
    thieu = [t for t in can if t not in co]
    return len(can) - len(thieu), len(can), thieu


# ----------------------------------------------------------- chuẩn bị file

def ep_nho(p: Path) -> tuple:
    """Ép ảnh xuống dưới 1 MB. Trả (bytes, ghi_chú) hoặc (None, lý do hỏng).

    Chỉ ép ẢNH. PDF quá cỡ thì chịu, phải tự xử lý bên ngoài.
    """
    so_byte = p.stat().st_size
    if so_byte <= GIOI_HAN_BYTE:
        return p.read_bytes(), ""

    if p.suffix.lower() == ".pdf":
        return None, f"PDF {so_byte / 1024:.0f} KB vượt trần 1 MB"
    if Image is None:
        return None, "quá 1 MB mà không có Pillow để ép nhỏ"

    try:
        im = Image.open(p)
        im.load()
    except Exception as e:
        return None, f"không mở được ảnh: {e}"

    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")

    # Hạ chất lượng trước, chỉ thu nhỏ khi buộc phải: độ phân giải quan trọng
    # hơn nhiều so với độ nén (đo được: 800px còn đọc đúng, 700px thì không).
    for ti_le in (1.0, 0.85, 0.7, 0.55):
        anh = im
        if ti_le < 1.0:
            anh = im.resize((max(1, int(im.width * ti_le)),
                             max(1, int(im.height * ti_le))), Image.LANCZOS)
        if anh.width < RONG_TOI_THIEU:
            break
        for chat_luong in (90, 80, 70, 60):
            buf = io.BytesIO()
            anh.save(buf, "JPEG", quality=chat_luong, optimize=True)
            if buf.tell() <= GIOI_HAN_BYTE:
                return buf.getvalue(), (f"đã ép {so_byte // 1024}KB -> "
                                        f"{buf.tell() // 1024}KB, {anh.width}px")
    return None, f"ép mãi không xuống dưới 1 MB (gốc {so_byte // 1024} KB)"


# --------------------------------------------------------------- gọi API

def goi(ten_file: str, noi_dung: bytes, key: str, engine: int,
        lang: str, scale: bool, timeout: int):
    """Gọi API một lần. Trả (text, giây, lỗi)."""
    data = {
        "apikey": key,
        "OCREngine": str(engine),
        "isOverlayRequired": "false",
        "detectOrientation": "true",
        "scale": "true" if scale else "false",
    }
    # OCR.space chỉ nhận MỘT ngôn ngữ mỗi lần gọi — không có "vnm+eng" như
    # Tesseract. Engine 3 tự nhận diện nên không cần khai.
    # Engine 1 KHÔNG hỗ trợ tiếng Việt, gọi vào trả lỗi E201.
    if engine != 3:
        data["language"] = lang

    t0 = time.time()
    try:
        r = requests.post(URL, data=data, files={ten_file: noi_dung},
                          timeout=timeout)
    except requests.RequestException as e:
        return None, time.time() - t0, f"{type(e).__name__}: {e}"
    giay = time.time() - t0

    if r.status_code != 200:
        return None, giay, f"HTTP {r.status_code}: {r.text[:150]}"

    try:
        body = r.json()
    except ValueError:
        return None, giay, f"trả về không phải JSON: {r.text[:150]}"

    if body.get("IsErroredOnProcessing"):
        tb = body.get("ErrorMessage") or body.get("ErrorDetails") or "?"
        if isinstance(tb, list):
            tb = " | ".join(str(x) for x in tb)
        if "E201" in str(tb):
            tb = f"{tb}  (engine này không hỗ trợ tiếng Việt, dùng engine 2)"
        return None, giay, str(tb)[:200]

    ket_qua = body.get("ParsedResults") or []
    if not ket_qua:
        return None, giay, "không có ParsedResults"

    return "\n".join(k.get("ParsedText") or "" for k in ket_qua), giay, None


# ------------------------------------------------------------ xem một ảnh

def soi_file(p: Path):
    if not p.exists():
        loi(f"Không thấy {p}")
        return False
    mo(f"{p.name}: {p.stat().st_size / 1024:.0f} KB")
    if p.suffix.lower() == ".pdf":
        canh(f"PDF: bậc miễn phí chỉ đọc {GIOI_HAN_TRANG_PDF} trang đầu.")
        return True
    if Image is None:
        return True
    try:
        with Image.open(p) as im:
            rong, cao = im.size
    except Exception:
        return True
    if rong < RONG_TOI_THIEU:
        canh(f"Ảnh rộng {rong}px, dưới ngưỡng {RONG_TOI_THIEU}px — dấu tiếng "
             f"Việt dễ sai. Thử thêm --scale.")
    else:
        mo(f"Kích thước {rong}x{cao}px")
    return True


def xem_mot_anh(p: Path, key, engines, lang, scale, timeout, mong):
    print(f"\n{'=' * 64}\n{p}")
    if not soi_file(p):
        return

    noi_dung, ghi_chu = ep_nho(p)
    if noi_dung is None:
        loi(ghi_chu)
        return
    if ghi_chu:
        canh(ghi_chu)

    for engine in engines:
        print()
        nhan = "tự nhận diện" if engine == 3 else f"language={lang}"
        mo(f"--- Engine {engine} ({nhan}) ---")
        text, giay, e = goi(p.name, noi_dung, key, engine, lang, scale, timeout)
        if e:
            loi(e)
            continue

        dong = [d for d in text.splitlines() if d.strip()]
        ok(f"Đọc được {len(dong)} dòng, {len(text)} ký tự, {giay:.1f}s")
        for d in dong:
            print(f"     {d}")

        if mong:
            print()
            thieu = [m for m in mong if m not in text]
            if thieu:
                loi(f"Đọc đúng {len(mong) - len(thieu)}/{len(mong)} chuỗi mong đợi")
                for m in thieu:
                    print(f"       THIẾU: {m!r}")
            else:
                ok(f"Đọc đúng cả {len(mong)}/{len(mong)} chuỗi mong đợi")


# ------------------------------------------------------------- chạy cả bộ

def chay_ca_bo(thu_muc: Path, ra: Path, key, engine, lang, scale,
               timeout, nghi, tiep_tuc):
    files = sorted(f for f in thu_muc.rglob("*") if f.suffix.lower() in ANH)
    if not files:
        loi(f"Không có ảnh nào trong {thu_muc}")
        return 1

    ra.mkdir(parents=True, exist_ok=True)
    print(f"\n{len(files)} file, kết quả ghi vào {ra}/\n")

    hang = []
    for i, p in enumerate(files, 1):
        dich = ra / (p.stem + ".txt")
        if tiep_tuc and dich.exists():
            print(f"  [{i:>3}/{len(files)}] bỏ qua (đã có)  {p.name[:55]}")
            continue

        mong_doi = khoa_hoc_tu_ten_file(p)
        noi_dung, ghi_chu = ep_nho(p)

        if noi_dung is None:
            print(f"  {DO}[{i:>3}/{len(files)}] BỎ{HET}  {p.name[:50]} — {ghi_chu}")
            hang.append({"file": p.name, "khoa_hoc": mong_doi, "trang_thai": "BỎ",
                         "ly_do": ghi_chu, "khop": "", "tong": "", "giay": "",
                         "thieu": ""})
            continue

        text, giay, e = goi(p.name, noi_dung, key, engine, lang, scale, timeout)
        if e:
            print(f"  {DO}[{i:>3}/{len(files)}] LỖI{HET} {p.name[:50]} — {e[:60]}")
            hang.append({"file": p.name, "khoa_hoc": mong_doi, "trang_thai": "LỖI",
                         "ly_do": e, "khop": "", "tong": "",
                         "giay": f"{giay:.1f}", "thieu": ""})
            time.sleep(nghi)
            continue

        dich.write_text(text, encoding="utf-8")
        khop, tong, thieu = cham_diem(text, mong_doi)
        ti_le = khop / tong if tong else 0
        dat = ti_le >= NGUONG_DAT
        mau = XANH if dat else VANG
        print(f"  {mau}[{i:>3}/{len(files)}] {khop:>2}/{tong:<2}{HET} "
              f"{p.name[:50]:<52} {giay:>5.1f}s"
              + ("" if dat else f"  thiếu: {', '.join(thieu[:4])}"))

        hang.append({"file": p.name, "khoa_hoc": mong_doi,
                     "trang_thai": "ĐẠT" if dat else "THẤP", "ly_do": "",
                     "khop": khop, "tong": tong, "giay": f"{giay:.1f}",
                     "thieu": " ".join(thieu)})
        time.sleep(nghi)

    if not hang:
        print("\nKhông có file nào được xử lý mới.")
        return 0

    csv_path = ra / "ket_qua.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(hang[0].keys()))
        w.writeheader()
        w.writerows(hang)

    dat = [h for h in hang if h["trang_thai"] == "ĐẠT"]
    thap = [h for h in hang if h["trang_thai"] == "THẤP"]
    hong = [h for h in hang if h["trang_thai"] in ("LỖI", "BỎ")]
    giay = [float(h["giay"]) for h in hang if h["giay"]]

    print(f"\n{'=' * 64}\n=== Tổng kết ===")
    print(f"  Đọc tốt (>= {NGUONG_DAT:.0%} số từ) : {len(dat)}/{len(hang)}")
    print(f"  Đọc thấp, cần xem bằng mắt   : {len(thap)}")
    print(f"  Lỗi hoặc bỏ qua              : {len(hong)}")
    if giay:
        giay.sort()
        print(f"  Thời gian trung vị           : {giay[len(giay) // 2]:.1f}s")

    if thap:
        print(f"\n  {VANG}Mười ca đọc kém nhất:{HET}")
        for h in sorted(thap, key=lambda x: x["khop"] / max(1, x["tong"]))[:10]:
            print(f"    {h['khop']}/{h['tong']}  {h['file'][:60]}")

    print(f"\n  Bản đọc đầy đủ : {ra}/*.txt")
    print(f"  Bảng tổng hợp  : {csv_path}")
    return 0


# ------------------------------------------------------------------- chính

def main() -> int:
    p = argparse.ArgumentParser(description="Thử OCR.space trên ảnh chứng chỉ.")
    p.add_argument("anh", nargs="?", help="Đường dẫn ảnh hoặc PDF.")
    p.add_argument("--thu-muc", help="Chạy cả thư mục và chấm điểm tự động.")
    p.add_argument("--ra", default="ocr_ket_qua",
                   help="Thư mục ghi bản đọc và CSV. Mặc định ocr_ket_qua/")
    p.add_argument("--tiep-tuc", action="store_true",
                   help="Bỏ qua file đã có kết quả — chạy tiếp lần dở dang.")
    p.add_argument("--key", help="API key. Bỏ trống thì lấy từ env hoặc .env.")
    p.add_argument("--engines",
                   help="Engine, cách nhau dấu phẩy. Bỏ trống thì lấy "
                        "OCRSPACE_ENGINE trong .env, không có nữa thì dùng 2. "
                        "Chạy cả bộ chỉ nhận MỘT engine.")
    p.add_argument("--lang",
                   help="Mã ngôn ngữ cho engine 1/2. Bỏ trống thì lấy "
                        "OCRSPACE_LANG, không có nữa thì dùng vnm. Dùng 'auto' "
                        "cho chứng chỉ song ngữ. Engine 3 bỏ qua tham số này.")
    p.add_argument("--mong", action="append", default=[],
                   help="Chuỗi phải đọc được (chỉ dùng khi xem một ảnh).")
    p.add_argument("--scale", action="store_true",
                   help="Để OCR.space tự phóng to ảnh nhỏ.")
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("--nghi", type=float, default=1.0,
                   help="Giây nghỉ giữa các file (bậc miễn phí chặn tần suất).")
    args = p.parse_args()

    if not args.anh and not args.thu_muc:
        p.error("Cần một đường dẫn ảnh, hoặc --thu-muc.")

    print("\n=== Cấu hình ===")

    tho, nguon_engine = lay_cau_hinh("OCRSPACE_ENGINE", args.engines, "2")
    try:
        engines = [int(x) for x in str(tho).split(",") if x.strip()]
    except ValueError:
        p.error(f"Engine phải là số, đang nhận {tho!r} (từ {nguon_engine}).")
    if not engines:
        p.error("Không có engine nào để thử.")
    if args.thu_muc and len(engines) > 1:
        p.error("Chạy cả bộ chỉ nhận một engine. Muốn so thì chạy hai lần, "
                "mỗi lần một --ra khác nhau.")

    lang, nguon_lang = lay_cau_hinh("OCRSPACE_LANG", args.lang, "vnm")
    key, nguon_key = lay_cau_hinh("OCRSPACE_API_KEY", args.key, "helloworld")

    if key == "helloworld":
        canh("Đang dùng KEY DEMO CÔNG KHAI — bị chặn tần suất rất gắt, không "
             "đủ để chạy cả bộ.")
        print("         Lấy key riêng (miễn phí, không cần thẻ): "
              "https://ocr.space/ocrapi")
    else:
        ok(f"Có API key ({len(key)} ký tự, lấy từ {nguon_key})")

    mo(f"Engine: {', '.join(map(str, engines))}  (từ {nguon_engine})")
    if 1 in engines:
        canh("Engine 1 KHÔNG hỗ trợ tiếng Việt — sẽ trả lỗi E201.")
    if engines != [3]:
        mo(f"Ngôn ngữ: {lang}  (từ {nguon_lang})")
    mo(f"Tự phóng to ảnh: {'bật' if args.scale else 'tắt'}")

    if args.thu_muc:
        thu_muc = Path(args.thu_muc)
        if not thu_muc.is_dir():
            loi(f"Không thấy thư mục {thu_muc}")
            return 1
        return chay_ca_bo(thu_muc, Path(args.ra), key, engines[0], lang,
                          args.scale, args.timeout, args.nghi, args.tiep_tuc)

    xem_mot_anh(Path(args.anh), key, engines, lang, args.scale,
                args.timeout, args.mong)
    print()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nDừng theo yêu cầu. Chạy lại kèm --tiep-tuc để làm nốt.")
        sys.exit(130)
