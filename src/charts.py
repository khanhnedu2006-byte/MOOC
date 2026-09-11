"""Vẽ biểu đồ cho báo cáo email (charts).

Sinh ảnh PNG bằng Pillow rồi nhúng vào email dưới dạng đính kèm inline (CID).

PNG chứ không SVG/JavaScript: Outlook trên Windows dựng HTML bằng engine của
Microsoft Word, nên <svg>, <canvas>, JavaScript và phần lớn
flexbox/grid/position/background-image không chạy. Chỉ <img> và <table> có màu
nền là chắc chắn hiển thị.

Đính kèm CID chứ không link ảnh: Outlook mặc định chặn ảnh tải từ internet.

Pillow chứ không matplotlib: Pillow đã có sẵn trong dự án, thêm matplotlib làm
image Docker phình ~60 MB và kéo theo numpy.

BẢNG MÀU đã qua kiểm tra mù màu (deuteranopia/protanopia/tritanopia), cặp gần
nhất ΔE 6.2 ở tritan. Ngưỡng đó chỉ hợp lệ khi có kênh phân biệt thứ hai ngoài
màu, nên mọi biểu đồ đều có chú giải, nhãn số và khe hở 2px giữa các mảng.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ===== Bảng màu =====
APPROVED = (12, 163, 12)      # #0ca30c  xanh — trạng thái tốt
REJECTED = (42, 120, 214)     # #2a78d6  xanh dương — kết quả hợp lệ, không phải lỗi
CAUSE = (208, 59, 59)         # #d03b3b  đỏ — nguyên nhân từ chối
TOTAL_LINE = (82, 81, 78)     # #52514e  mực phụ — đường tổng, giữ vai trò nền

SURFACE = (252, 252, 251)     # #fcfcfb
INK = (11, 11, 11)            # #0b0b0b
INK_2 = (82, 81, 78)          # #52514e
GRID = (228, 228, 224)        # lưới lùi về sau, không tranh với dữ liệu

# Vẽ ở 2x rồi thu nhỏ -> nét mượt trên HiDPI, khỏi antialias thủ công.
SCALE = 2

_FONT_DIRS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
)
_FONT_DIRS_BOLD = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)


def _font(size: int, dam: bool = False):
    """Tải font, thử lần lượt các đường dẫn Linux rồi Windows.

    Không có font nào thì lùi về font mặc định của Pillow, xấu nhưng vẫn gửi
    được báo cáo.
    """
    for p in (_FONT_DIRS_BOLD if dam else _FONT_DIRS):
        if Path(p).is_file():
            try:
                return ImageFont.truetype(p, size * SCALE)
            except OSError:
                continue
    return ImageFont.load_default()


def _do_rong(d: ImageDraw.ImageDraw, text: str, font) -> int:
    return int(d.textlength(text, font=font))


def _xuat_png(anh: Image.Image) -> bytes:
    """Thu nhỏ về 1x và xuất PNG."""
    w, h = anh.size
    anh = anh.resize((w // SCALE, h // SCALE), Image.LANCZOS)
    buf = io.BytesIO()
    anh.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _thang_do(gia_tri_max: int) -> tuple[int, int]:
    """Chọn trần trục Y và bước chia thành số tròn.

    Trục chạy tới đúng giá trị lớn nhất cho ra trần 137, vạch 45.67.
    """
    if gia_tri_max <= 0:
        return 1, 1
    for buoc in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500,
                 1000, 2000, 2500, 5000, 10000, 20000, 50000):
        if gia_tri_max <= buoc * 4:
            return buoc * 4, buoc
    buoc = 10 ** (len(str(gia_tri_max)) - 1)
    return ((gia_tri_max // buoc) + 1) * buoc, buoc


def _khung(d, x0, y0, x1, y1, tran, buoc, font_nho):
    """Vẽ lưới ngang + nhãn trục Y. Lưới mảnh, màu nhạt, nằm sau dữ liệu."""
    so_vach = max(1, tran // buoc)
    for i in range(so_vach + 1):
        gt = buoc * i
        y = y1 - (y1 - y0) * gt / tran
        d.line([(x0, y), (x1, y)], fill=GRID, width=1 * SCALE)
        label = f"{gt:,}".replace(",", ".")
        d.text((x0 - 8 * SCALE - _do_rong(d, label, font_nho), y - 7 * SCALE),
               label, font=font_nho, fill=INK_2)


def _nhan_moc(bucket: str) -> str:
    """Rút gọn mốc thời gian thành nhãn trục X đọc được.

    Cắt theo CẤU TRÚC từng loại mốc; cắt cứng n ký tự cuối cho ra "6-W29".
    """
    b = str(bucket)
    if "-W" in b:                       # 2026-W29 -> W29
        return b.split("-", 1)[1]
    part = b.split("-")
    if len(part) == 3:                  # 2026-08-03 -> 03/08
        return f"{part[2]}/{part[1]}"
    if len(part) == 2:                  # 2026-08 -> T08
        return f"T{part[1]}"
    return b


def _chu_giai(d, x, y, items: list[tuple[str, tuple]], font):
    """Chú giải: ô màu + CHỮ. Luôn có, kể cả khi chỉ hai chuỗi.

    Chữ mặc màu mực, không mặc màu chuỗi: ô màu đã mang danh tính.
    """
    cx = x
    o = 10 * SCALE
    for label, color in items:
        d.rounded_rectangle([cx, y, cx + o, y + o], radius=2 * SCALE, fill=color)
        cx += o + 6 * SCALE
        d.text((cx, y - 2 * SCALE), label, font=font, fill=INK_2)
        cx += _do_rong(d, label, font) + 18 * SCALE


# =====================================================================
# Biểu đồ 1 — ĐƯỜNG: xu hướng khối lượng xử lý
# =====================================================================

def line_chart(diem: list[dict], rong: int = 640, cao: int = 260) -> bytes:
    """Biểu đồ đường: tổng xử lý và số được duyệt theo mốc thời gian.

    diem: [{"bucket": "2026-W30", "total": 120, "approved": 100}, ...]

    HAI ĐƯỜNG CÙNG ĐƠN VỊ (số chứng chỉ) nên dùng CHUNG một trục. Không vẽ tỷ
    lệ duyệt (%) chung khung với số lượng: hai trục Y làm người đọc thấy tương
    quan không có thật.
    """
    W, H = rong * SCALE, cao * SCALE
    anh = Image.new("RGB", (W, H), SURFACE)
    d = ImageDraw.Draw(anh)
    f_nho, f_th = _font(11), _font(12)

    if not diem:
        d.text((W // 2 - 60 * SCALE, H // 2), "Chưa có dữ liệu",
               font=f_th, fill=INK_2)
        return _xuat_png(anh)

    # Lề phải rộng hơn lề trên vì nhãn số của mốc cuối vẽ bên phải điểm cuối,
    # sát mép thì bị cắt chữ số.
    x0, y0 = 62 * SCALE, 34 * SCALE
    x1, y1 = W - 54 * SCALE, H - 46 * SCALE
    tran, buoc = _thang_do(max(p["total"] for p in diem))
    _khung(d, x0, y0, x1, y1, tran, buoc, f_nho)

    n = len(diem)
    # n == 1: đặt điểm vào giữa thay vì chia cho 0.
    def toa_do(i, gt):
        x = x0 + (x1 - x0) * (i / (n - 1)) if n > 1 else (x0 + x1) / 2
        return x, y1 - (y1 - y0) * gt / tran

    for key, color in (("total", TOTAL_LINE), ("approved", APPROVED)):
        pts = [toa_do(i, p.get(key, 0)) for i, p in enumerate(diem)]
        if len(pts) > 1:
            d.line(pts, fill=color, width=2 * SCALE, joint="curve")
        for x, y in pts:
            # Vòng nền quanh điểm để hai đường chồng nhau vẫn tách nhau.
            r = 4 * SCALE
            d.ellipse([x - r - SCALE, y - r - SCALE, x + r + SCALE, y + r + SCALE],
                      fill=SURFACE)
            d.ellipse([x - r, y - r, x + r, y + r], fill=color)

    # Nhãn trực tiếp CHỈ ở mốc cuối; ghi số lên mọi điểm thì chữ che mất đường.
    # Đặt bên PHẢI điểm cuối; hai nhãn quá gần nhau theo chiều dọc thì đẩy ra
    # hai phía, vì hai đường hay hội tụ ở mốc cuối.
    nhan_cuoi = []
    for key in ("total", "approved"):
        gt = diem[-1].get(key, 0)
        x, y = toa_do(n - 1, gt)
        nhan_cuoi.append([y, f"{gt:,}".replace(",", "."), x])
    nhan_cuoi.sort()
    if len(nhan_cuoi) == 2 and nhan_cuoi[1][0] - nhan_cuoi[0][0] < 18 * SCALE:
        giua = (nhan_cuoi[0][0] + nhan_cuoi[1][0]) / 2
        nhan_cuoi[0][0], nhan_cuoi[1][0] = giua - 10 * SCALE, giua + 10 * SCALE
    for y, label, x in nhan_cuoi:
        d.text((x + 8 * SCALE, y - 7 * SCALE), label, font=f_th, fill=INK)

    # Nhãn trục X: thưa dần khi nhiều mốc, tránh chữ đè lên nhau.
    buoc_nhan = max(1, n // 8)
    for i, p in enumerate(diem):
        if i % buoc_nhan and i != n - 1:
            continue
        x, _ = toa_do(i, 0)
        label = _nhan_moc(p["bucket"])
        d.text((x - _do_rong(d, label, f_nho) / 2, y1 + 8 * SCALE),
               label, font=f_nho, fill=INK_2)

    _chu_giai(d, x0, H - 20 * SCALE,
              [("Tổng xử lý", TOTAL_LINE), ("Được duyệt", APPROVED)], f_nho)
    return _xuat_png(anh)


# =====================================================================
# Biểu đồ 2 — CỘT CHỒNG: duyệt / từ chối / lỗi kỹ thuật
# =====================================================================

def stacked_bar_chart(diem: list[dict], rong: int = 640, cao: int = 260) -> bytes:
    """Cột chồng theo mốc: được duyệt / từ chối.

    Chiều cao cột = khối lượng, hai mảng = cơ cấu. Chỉ gồm ca AI phán đoán
    được; ca hỏng kỹ thuật đã bị loại từ tầng truy vấn nên cột luôn cộng đúng
    bằng tổng ở đầu báo cáo.
    """
    W, H = rong * SCALE, cao * SCALE
    anh = Image.new("RGB", (W, H), SURFACE)
    d = ImageDraw.Draw(anh)
    f_nho, f_th = _font(11), _font(12)

    if not diem:
        d.text((W // 2 - 60 * SCALE, H // 2), "Chưa có dữ liệu",
               font=f_th, fill=INK_2)
        return _xuat_png(anh)

    x0, y0 = 62 * SCALE, 30 * SCALE
    x1, y1 = W - 16 * SCALE, H - 46 * SCALE
    tran, buoc = _thang_do(max(p["total"] for p in diem))
    _khung(d, x0, y0, x1, y1, tran, buoc, f_nho)

    n = len(diem)
    o_rong = (x1 - x0) / n
    cot_rong = min(46 * SCALE, o_rong * 0.62)
    khe = 2 * SCALE          # khe hở giữa các mảng chồng

    for i, p in enumerate(diem):
        cx = x0 + o_rong * (i + 0.5)
        trai, phai = cx - cot_rong / 2, cx + cot_rong / 2
        day = y1
        # Duyệt ở dưới cùng, neo vào trục: so chiều cao từ một đường nền chung
        # chính xác hơn mảng lơ lửng giữa cột.
        for key, color in (("approved", APPROVED), ("rejected", REJECTED)):
            gt = p.get(key, 0)
            if gt <= 0:
                continue
            chieu_cao = (y1 - y0) * gt / tran
            dinh = day - chieu_cao
            d.rounded_rectangle([trai, dinh, phai, day],
                                radius=min(4 * SCALE, chieu_cao / 2), fill=color)
            day = dinh - khe

        tong = p.get("total", 0)
        if tong:
            label = f"{tong:,}".replace(",", ".")
            d.text((cx - _do_rong(d, label, f_th) / 2, day - 20 * SCALE),
                   label, font=f_th, fill=INK)

    buoc_nhan = max(1, n // 8)
    for i, p in enumerate(diem):
        if i % buoc_nhan and i != n - 1:
            continue
        cx = x0 + o_rong * (i + 0.5)
        label = _nhan_moc(p["bucket"])
        d.text((cx - _do_rong(d, label, f_nho) / 2, y1 + 8 * SCALE),
               label, font=f_nho, fill=INK_2)

    _chu_giai(d, x0, H - 20 * SCALE,
              [("Được duyệt", APPROVED), ("Từ chối", REJECTED)], f_nho)
    return _xuat_png(anh)
