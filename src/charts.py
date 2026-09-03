"""Vẽ biểu đồ cho báo cáo email (charts).

Sinh ảnh PNG bằng Pillow rồi nhúng vào email dưới dạng đính kèm inline (CID).

VÌ SAO PNG CHỨ KHÔNG PHẢI SVG HAY JAVASCRIPT:
Outlook trên Windows KHÔNG dùng engine trình duyệt — nó dựng HTML bằng engine
của Microsoft Word. Hệ quả:
  - <svg> không hiển thị.
  - <canvas> và mọi JavaScript không chạy.
  - flexbox, grid, position, background-image phần lớn bị bỏ qua.
Thứ chắc chắn hiển thị là <img> và <table> có màu nền. Nên biểu đồ phải là
ảnh raster, còn khung báo cáo phải là bảng.

VÌ SAO ĐÍNH KÈM CID CHỨ KHÔNG PHẢI LINK ẢNH:
Outlook mặc định CHẶN ảnh tải từ internet ("Click here to download pictures").
Ảnh đính kèm inline theo Content-ID nằm ngay trong thư nên không bị chặn.

VÌ SAO PILLOW CHỨ KHÔNG PHẢI MATPLOTLIB:
Pillow đã là thư viện của dự án (file_utils dùng để nén ảnh). Thêm matplotlib
chỉ để vẽ hai biểu đồ sẽ làm image Docker phình thêm khoảng 60 MB và kéo theo
numpy — cái giá không đáng cho vài đường kẻ.

BẢNG MÀU đã qua kiểm tra mù màu (deuteranopia/protanopia/tritanopia), khoảng
cách cặp gần nhất ΔE 6.2 ở tritan. Ngưỡng đó CHỈ hợp lệ khi có kênh phân biệt
thứ hai ngoài màu — nên mọi biểu đồ ở đây đều có chú giải, nhãn số trực tiếp,
và khe hở 2px giữa các mảng. Không bao giờ để màu là thứ duy nhất mang nghĩa.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ===== Bảng màu =====
APPROVED = (12, 163, 12)      # #0ca30c  xanh — trạng thái tốt
REJECTED = (42, 120, 214)     # #2a78d6  xanh dương — kết quả hợp lệ, KHÔNG phải lỗi
CAUSE = (208, 59, 59)         # #d03b3b  đỏ — nguyên nhân từ chối
TOTAL_LINE = (82, 81, 78)     # #52514e  mực phụ — đường tổng, giữ vai trò nền

SURFACE = (252, 252, 251)     # #fcfcfb
INK = (11, 11, 11)            # #0b0b0b
INK_2 = (82, 81, 78)          # #52514e
GRID = (228, 228, 224)        # lưới lùi về sau, không tranh với dữ liệu

# Vẽ ở 2x rồi thu nhỏ -> nét mượt trên màn hình HiDPI mà không cần antialias
# thủ công cho từng nét.
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

    Không tìm được font nào thì lùi về font mặc định của Pillow — xấu nhưng
    vẫn đọc được. Biểu đồ xấu còn hơn báo cáo không gửi được.
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

    Trục chạy tới đúng giá trị lớn nhất trông rất lộn xộn (trần 137, vạch
    45.67). Làm tròn lên số đẹp thì người đọc ước lượng được bằng mắt.
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
        nhan = f"{gt:,}".replace(",", ".")
        d.text((x0 - 8 * SCALE - _do_rong(d, nhan, font_nho), y - 7 * SCALE),
               nhan, font=font_nho, fill=INK_2)


def _nhan_moc(bucket: str) -> str:
    """Rút gọn mốc thời gian thành nhãn trục X đọc được.

    Cắt cứng n ký tự cuối cho ra "6-W29" từ "2026-W29" — vừa xấu vừa gây
    hiểu nhầm là ngày 6. Cắt theo CẤU TRÚC của từng loại mốc thì mới đúng.
    """
    b = str(bucket)
    if "-W" in b:                       # 2026-W29 -> W29
        return b.split("-", 1)[1]
    phan = b.split("-")
    if len(phan) == 3:                  # 2026-08-03 -> 03/08
        return f"{phan[2]}/{phan[1]}"
    if len(phan) == 2:                  # 2026-08 -> T08
        return f"T{phan[1]}"
    return b


def _chu_giai(d, x, y, muc: list[tuple[str, tuple]], font):
    """Chú giải: ô màu + CHỮ. Luôn có, kể cả khi chỉ hai chuỗi.

    Chữ ở đây mặc màu mực chứ không mặc màu chuỗi — ô màu bên cạnh đã mang
    danh tính rồi; tô chữ theo màu chuỗi làm chữ khó đọc mà không thêm nghĩa.
    """
    cx = x
    o = 10 * SCALE
    for nhan, mau in muc:
        d.rounded_rectangle([cx, y, cx + o, y + o], radius=2 * SCALE, fill=mau)
        cx += o + 6 * SCALE
        d.text((cx, y - 2 * SCALE), nhan, font=font, fill=INK_2)
        cx += _do_rong(d, nhan, font) + 18 * SCALE


# =====================================================================
# Biểu đồ 1 — ĐƯỜNG: xu hướng khối lượng xử lý
# =====================================================================

def line_chart(diem: list[dict], rong: int = 640, cao: int = 260) -> bytes:
    """Biểu đồ đường: tổng xử lý và số được duyệt theo mốc thời gian.

    diem: [{"bucket": "2026-W30", "total": 120, "approved": 100}, ...]

    HAI ĐƯỜNG CÙNG MỘT ĐƠN VỊ (số chứng chỉ), nên dùng CHUNG một trục.
    Không bao giờ vẽ tỷ lệ duyệt (%) chung khung với số lượng: hai thang đo
    khác nhau trên hai trục Y là cách chắc chắn nhất để người đọc thấy một
    mối tương quan không có thật.
    """
    W, H = rong * SCALE, cao * SCALE
    anh = Image.new("RGB", (W, H), SURFACE)
    d = ImageDraw.Draw(anh)
    f_nho, f_th = _font(11), _font(12)

    if not diem:
        d.text((W // 2 - 60 * SCALE, H // 2), "Chưa có dữ liệu",
               font=f_th, fill=INK_2)
        return _xuat_png(anh)

    # Lề phải rộng hơn lề trên: nhãn số của mốc cuối được vẽ bên phải điểm
    # cuối, sát mép thì bị cắt mất chữ số.
    x0, y0 = 62 * SCALE, 34 * SCALE
    x1, y1 = W - 54 * SCALE, H - 46 * SCALE
    tran, buoc = _thang_do(max(p["total"] for p in diem))
    _khung(d, x0, y0, x1, y1, tran, buoc, f_nho)

    n = len(diem)
    # n == 1: đặt điểm vào giữa thay vì chia cho 0.
    def toa_do(i, gt):
        x = x0 + (x1 - x0) * (i / (n - 1)) if n > 1 else (x0 + x1) / 2
        return x, y1 - (y1 - y0) * gt / tran

    for khoa, mau in (("total", TOTAL_LINE), ("approved", APPROVED)):
        pts = [toa_do(i, p.get(khoa, 0)) for i, p in enumerate(diem)]
        if len(pts) > 1:
            d.line(pts, fill=mau, width=2 * SCALE, joint="curve")
        for x, y in pts:
            # Vòng nền quanh điểm để hai đường chồng nhau vẫn tách được.
            r = 4 * SCALE
            d.ellipse([x - r - SCALE, y - r - SCALE, x + r + SCALE, y + r + SCALE],
                      fill=SURFACE)
            d.ellipse([x - r, y - r, x + r, y + r], fill=mau)

    # Nhãn trực tiếp: CHỈ mốc cuối. Ghi số lên mọi điểm thì biểu đồ thành
    # bảng số, và đường — thứ mang thông tin xu hướng — bị chữ che mất.
    #
    # Đặt bên PHẢI điểm cuối, và nếu hai nhãn quá gần nhau theo chiều dọc thì
    # đẩy ra hai phía. Hai đường hội tụ ở mốc cuối là chuyện thường, để mặc
    # thì hai con số chồng lên nhau thành một mớ không đọc được.
    nhan_cuoi = []
    for khoa in ("total", "approved"):
        gt = diem[-1].get(khoa, 0)
        x, y = toa_do(n - 1, gt)
        nhan_cuoi.append([y, f"{gt:,}".replace(",", "."), x])
    nhan_cuoi.sort()
    if len(nhan_cuoi) == 2 and nhan_cuoi[1][0] - nhan_cuoi[0][0] < 18 * SCALE:
        giua = (nhan_cuoi[0][0] + nhan_cuoi[1][0]) / 2
        nhan_cuoi[0][0], nhan_cuoi[1][0] = giua - 10 * SCALE, giua + 10 * SCALE
    for y, nhan, x in nhan_cuoi:
        d.text((x + 8 * SCALE, y - 7 * SCALE), nhan, font=f_th, fill=INK)

    # Nhãn trục X: thưa dần khi nhiều mốc, tránh chữ đè lên nhau.
    buoc_nhan = max(1, n // 8)
    for i, p in enumerate(diem):
        if i % buoc_nhan and i != n - 1:
            continue
        x, _ = toa_do(i, 0)
        nhan = _nhan_moc(p["bucket"])
        d.text((x - _do_rong(d, nhan, f_nho) / 2, y1 + 8 * SCALE),
               nhan, font=f_nho, fill=INK_2)

    _chu_giai(d, x0, H - 20 * SCALE,
              [("Tổng xử lý", TOTAL_LINE), ("Được duyệt", APPROVED)], f_nho)
    return _xuat_png(anh)


# =====================================================================
# Biểu đồ 2 — CỘT CHỒNG: duyệt / từ chối / lỗi kỹ thuật
# =====================================================================

def stacked_bar_chart(diem: list[dict], rong: int = 640, cao: int = 260) -> bytes:
    """Cột chồng theo mốc: được duyệt / từ chối.

    Chiều cao cột = khối lượng, hai mảng = cơ cấu. Một hình trả lời cả hai
    câu hỏi, thay vì hai biểu đồ.

    Chỉ gồm ca AI THỰC SỰ phán đoán được. Ca hỏng kỹ thuật đã bị loại từ
    tầng truy vấn, nên cột ở đây luôn cộng đúng bằng tổng ở đầu báo cáo.
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
        # Duyệt ở dưới cùng, neo vào trục: mắt so chiều cao từ một đường
        # nền chung thì chính xác hơn nhiều so với mảng lơ lửng giữa cột.
        for khoa, mau in (("approved", APPROVED), ("rejected", REJECTED)):
            gt = p.get(khoa, 0)
            if gt <= 0:
                continue
            chieu_cao = (y1 - y0) * gt / tran
            dinh = day - chieu_cao
            d.rounded_rectangle([trai, dinh, phai, day],
                                radius=min(4 * SCALE, chieu_cao / 2), fill=mau)
            day = dinh - khe

        tong = p.get("total", 0)
        if tong:
            nhan = f"{tong:,}".replace(",", ".")
            d.text((cx - _do_rong(d, nhan, f_th) / 2, day - 20 * SCALE),
                   nhan, font=f_th, fill=INK)

    buoc_nhan = max(1, n // 8)
    for i, p in enumerate(diem):
        if i % buoc_nhan and i != n - 1:
            continue
        cx = x0 + o_rong * (i + 0.5)
        nhan = _nhan_moc(p["bucket"])
        d.text((cx - _do_rong(d, nhan, f_nho) / 2, y1 + 8 * SCALE),
               nhan, font=f_nho, fill=INK_2)

    _chu_giai(d, x0, H - 20 * SCALE,
              [("Được duyệt", APPROVED), ("Từ chối", REJECTED)], f_nho)
    return _xuat_png(anh)
