"""Dựng HTML báo cáo khóa học theo khoảng thời gian (report_layout).

Tách khỏi send_report.py vì đây là báo cáo KHÁC: send_report lo báo cáo MỘT
NGÀY, còn đây là báo cáo tuần/tháng có biểu đồ và thống kê theo nhà cung cấp.

BỐ CỤC (theo mẫu đã chốt):
    CERTIFICATE VERIFICATION REPORT
    ────────────────────────────────
    Total Processed  Approved  Rejected  Approval Rate
    ────────────────────────────────
    Certificate Processing Trend        [biểu đồ đường]
    ────────────────────────────────
    Approval vs Rejection               [biểu đồ cột chồng]
    ────────────────────────────────
    Lý do từ chối · Theo nhà cung cấp
    ────────────────────────────────
    Key Findings

MỌI THỨ DỰNG BẰNG <table>. Outlook trên Windows dựng HTML bằng engine của
Word: flexbox, grid, position, và phần lớn CSS hiện đại bị bỏ qua. Bảng có
thuộc tính width/bgcolor thì chạy ở mọi client, kể cả Outlook 2016.

Biểu đồ trả về dưới dạng (html, danh_sach_anh) — ảnh phải được đính kèm
inline theo Content-ID; xem send_report._send_smtp.
"""

from __future__ import annotations

import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import charts  # noqa: E402

COLORS = {
    "green": "#0ca30c",
    "blue": "#2a78d6",
    "red": "#d03b3b",
    "ink": "#0b0b0b",
    "ink2": "#52514e",
    "muted": "#8a8a85",
    "border": "#e4e4e0",
    "surface": "#fcfcfb",
}


def _n(x) -> str:
    """Số có dấu phân cách nghìn kiểu Việt Nam (1.480 chứ không 1,480)."""
    try:
        return f"{int(x):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(x)


def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _kpi_cell(label: str, value: str, color: str = None, sub: str = "") -> str:
    """Một ô trong hàng KPI."""
    color = color or COLORS["ink"]
    dong_phu = (f"<div style=\"font-size:11px;color:{COLORS['muted']};"
                f"padding-top:3px;\">{_e(sub)}</div>") if sub else ""
    return f"""
      <td align="center" width="25%" style="padding:14px 8px;">
        <div style="font-size:11px;letter-spacing:.4px;color:{COLORS['ink2']};
                    text-transform:uppercase;">{_e(label)}</div>
        <div style="font-size:28px;font-weight:bold;color:{color};
                    padding-top:6px;font-family:Segoe UI,Arial,sans-serif;">{value}</div>
        {dong_phu}
      </td>"""


def _divider() -> str:
    return (f'<tr><td colspan="4" style="border-top:1px solid '
            f'{COLORS["border"]};font-size:0;line-height:0;">&nbsp;</td></tr>')


def _section_title(text: str) -> str:
    return f"""
      <tr><td colspan="4" style="padding:16px 12px 6px;font-size:14px;
          font-weight:bold;color:{COLORS['ink']};
          font-family:Segoe UI,Arial,sans-serif;">{_e(text)}</td></tr>"""


def _rejection_reason_table(rc: dict) -> str:
    """Bảng nguyên nhân từ chối, kèm thanh tỷ lệ vẽ bằng ô bảng.

    Mẫu số của % là SỐ CA TỪ CHỐI, không phải tổng ba nguyên nhân — vì câu
    hỏi người đọc đặt ra là "trong số bị từ chối, bao nhiêu phần sai tên",
    chứ không phải "sai tên chiếm bao nhiêu phần trong các lỗi".
    """
    rows = rc["causes"]
    mau_so = rc["total_rejected"]
    if not mau_so:
        return (f'<tr><td colspan="4" style="padding:6px 12px;font-size:13px;'
                f'color:{COLORS["muted"]};">Không có chứng chỉ nào bị từ chối.</td></tr>')

    lon_nhat = max((m["count"] for m in rows), default=0) or 1
    hang = []
    for m in rows:
        pt = m["count"] / mau_so * 100
        w = max(2, round(m["count"] / lon_nhat * 100))
        hang.append(f"""
        <tr>
          <td style="padding:7px 12px;font-size:13px;border-top:1px solid {COLORS['border']};"
              width="180">{_e(m['label'])}</td>
          <td align="right" style="padding:7px 8px;font-size:15px;font-weight:bold;
              color:{COLORS['red']};border-top:1px solid {COLORS['border']};"
              width="60">{_n(m['count'])}</td>
          <td align="right" style="padding:7px 8px;font-size:12px;color:{COLORS['ink2']};
              border-top:1px solid {COLORS['border']};" width="50">{pt:.0f}%</td>
          <td style="padding:7px 12px;border-top:1px solid {COLORS['border']};">
            <table cellpadding="0" cellspacing="0" border="0" width="100%"
                   style="border-collapse:collapse;"><tr>
              <td width="{w}%" bgcolor="{COLORS['red']}"
                  style="height:10px;font-size:0;line-height:0;">&nbsp;</td>
              <td width="{100 - w}%" style="font-size:0;line-height:0;">&nbsp;</td>
            </tr></table>
          </td>
        </tr>""")

    # Câu này BẮT BUỘC phải có khi có ca sai nhiều thứ: không có nó thì người
    # đọc cộng ba số, thấy lớn hơn tổng từ chối, và kết luận báo cáo sai.
    ghi_chu = ""
    if rc["multi_cause"]:
        ghi_chu = (
            f'<tr><td colspan="4" style="padding:8px 12px 2px;font-size:11px;'
            f'color:{COLORS["muted"]};line-height:1.5;">'
            f'Tổng ba con số lớn hơn {_n(mau_so)} vì {_n(rc["multi_cause"])} '
            f'chứng chỉ sai từ hai tiêu chí trở lên — mỗi chứng chỉ được đếm '
            f'ở tất cả các nguyên nhân mà nó vi phạm.</td></tr>')

    return f"""
      <tr><td colspan="4" style="padding:0 12px 8px;">
        <table width="100%" cellpadding="0" cellspacing="0" border="0">{''.join(hang)}{ghi_chu}</table>
      </td></tr>"""


def _provider_table(rows: list[dict]) -> str:
    """Bảng theo nhà cung cấp, kèm thanh tỷ lệ duyệt vẽ bằng ô bảng.

    Thanh tỷ lệ dùng hai <td> có bgcolor và width tính theo phần trăm — cách
    duy nhất vẽ được thanh trong Outlook mà không cần ảnh.
    """
    if not rows:
        return (f'<tr><td colspan="4" style="padding:6px 12px;font-size:13px;'
                f'color:{COLORS["muted"]};">Chưa có dữ liệu nhà cung cấp.</td></tr>')
    hang = [f"""
        <tr>
          <td style="padding:6px 12px;font-size:11px;color:{COLORS['ink2']};
              text-transform:uppercase;">Nhà cung cấp</td>
          <td align="right" style="padding:6px 8px;font-size:11px;color:{COLORS['ink2']};
              text-transform:uppercase;">Tổng</td>
          <td align="right" style="padding:6px 8px;font-size:11px;color:{COLORS['ink2']};
              text-transform:uppercase;">Duyệt</td>
          <td align="right" style="padding:6px 8px;font-size:11px;color:{COLORS['ink2']};
              text-transform:uppercase;">Từ chối</td>
          <td style="padding:6px 12px;font-size:11px;color:{COLORS['ink2']};
              text-transform:uppercase;" width="130">Tỷ lệ duyệt</td>
        </tr>"""]
    for m in rows:
        ty_le = m["approval_rate"]
        w_xanh = max(0, min(100, round(ty_le)))
        thanh = f"""
          <table cellpadding="0" cellspacing="0" border="0" width="110"
                 style="border-collapse:collapse;"><tr>
            <td width="{w_xanh}%" bgcolor="{COLORS['green']}"
                style="height:8px;font-size:0;line-height:0;">&nbsp;</td>
            <td width="{100 - w_xanh}%" bgcolor="{COLORS['border']}"
                style="height:8px;font-size:0;line-height:0;">&nbsp;</td>
          </tr></table>"""
        hang.append(f"""
        <tr>
          <td style="padding:7px 12px;font-size:13px;border-top:1px solid {COLORS['border']};">{_e(m['provider'])}</td>
          <td align="right" style="padding:7px 8px;font-size:13px;font-weight:bold;
              border-top:1px solid {COLORS['border']};">{_n(m['total'])}</td>
          <td align="right" style="padding:7px 8px;font-size:13px;color:{COLORS['green']};
              border-top:1px solid {COLORS['border']};">{_n(m['approved'])}</td>
          <td align="right" style="padding:7px 8px;font-size:13px;color:{COLORS['blue']};
              border-top:1px solid {COLORS['border']};">{_n(m['rejected'])}</td>
          <td style="padding:7px 12px;border-top:1px solid {COLORS['border']};">
            {thanh}
            <div style="font-size:11px;color:{COLORS['ink2']};padding-top:2px;">{ty_le:.0f}%</div>
          </td>
        </tr>""")
    return f"""
      <tr><td colspan="4" style="padding:0 12px 8px;">
        <table width="100%" cellpadding="0" cellspacing="0" border="0">{''.join(hang)}</table>
      </td></tr>"""


def key_findings(stats: dict) -> list[str]:
    """Sinh các gạch đầu dòng Key Findings từ số liệu.

    Chỉ nêu điều SỐ LIỆU CHỨNG MINH được. Không viết "chất lượng đang cải
    thiện" khi chỉ có hai mốc — hai điểm thì nối được đường thẳng nào cũng
    đúng, đó chưa phải xu hướng.
    """
    ra = []
    tong = stats["total"]
    if not tong:
        return ["Không có chứng chỉ nào được xử lý trong kỳ này. "
                "Có thể không ai nộp, cũng có thể job không chạy — nên kiểm tra."]

    ra.append(f"Đã xử lý {_n(tong)} chứng chỉ "
              f"({stats['from_day']} → {stats['to_day']}).")
    ra.append(f"{stats['approval_rate']:.0f}% được duyệt "
              f"({_n(stats['approved'])} chứng chỉ).")
    ra.append(f"{100 - stats['approval_rate']:.0f}% bị từ chối "
              f"({_n(stats['rejected'])} chứng chỉ).")

    rc = stats["rejection_causes"]
    if rc["total_rejected"]:
        lon = max(rc["causes"], key=lambda x: x["count"])
        if lon["count"]:
            pt = lon["count"] / rc["total_rejected"] * 100
            ra.append(f"Lý do từ chối phổ biến nhất: {lon['label'].lower()} — "
                      f"{_n(lon['count'])} ca ({pt:.0f}% số ca bị từ chối).")
        chi_tiet = ", ".join(f"{m['label'].lower()} {_n(m['count'])}"
                             for m in rc["causes"] if m["count"])
        if chi_tiet:
            ra.append(f"Chi tiết: {chi_tiet}.")
        if rc["multi_cause"]:
            ra.append(f"{_n(rc['multi_cause'])} chứng chỉ sai từ hai tiêu chí "
                      f"trở lên, nên tổng các nguyên nhân lớn hơn số ca từ chối.")

    # Xu hướng: chỉ nói khi có TỪ BA MỐC trở lên và mốc cuối đã trọn vẹn.
    xu_huong = [p for p in stats["trend"] if p["total"] > 0]
    if len(xu_huong) >= 3:
        dau, cuoi = xu_huong[0]["total"], xu_huong[-1]["total"]
        if dau:
            chenh = (cuoi - dau) / dau * 100
            if abs(chenh) >= 10:
                huong = "tăng" if chenh > 0 else "giảm"
                ra.append(f"Khối lượng xử lý {huong} {abs(chenh):.0f}% từ mốc "
                          f"đầu kỳ ({_n(dau)}) đến mốc cuối kỳ ({_n(cuoi)}).")

    ncc = stats["by_provider"]
    if ncc:
        d = ncc[0]
        ra.append(f"Nhà cung cấp nhiều nhất: {d['provider']} "
                  f"({_n(d['total'])} chứng chỉ, duyệt {d['approval_rate']:.0f}%).")
        # Nhà cung cấp có tỷ lệ duyệt thấp bất thường -> đáng xem lại luật khớp.
        kem = [x for x in ncc if x["total"] >= 5 and x["approval_rate"] < 50]
        if kem:
            name = ", ".join(f"{x['provider']} ({x['approval_rate']:.0f}%)"
                            for x in kem[:3])
            ra.append(f"Tỷ lệ duyệt thấp bất thường ở: {name}. "
                      f"Nên xem mẫu chứng chỉ của họ có định dạng lạ không.")

    chua_gui = stats["elis"]["unsent"] + stats["elis"]["rejected_by_elis"]
    if chua_gui:
        ra.append(f"{_n(chua_gui)} kết quả chưa về được eLIS "
                  f"(chưa nộp hoặc bị từ chối khi nộp).")
    return ra


def _warning_block(warnings: list[str]) -> str:
    """Khối "Cần chú ý" — diễn giải sẵn các con số thành câu hành động.

    Đặt NGAY DƯỚI hàng KPI, trước cả biểu đồ: nếu job chết hoặc eLIS hỏng thì
    đó là thứ duy nhất người đọc cần biết, không nên bắt họ cuộn qua hai biểu
    đồ mới thấy.
    """
    if not warnings:
        return ""
    rows = "".join(
        f'<li style="padding:3px 0;font-size:13px;line-height:1.5;">{_e(c)}</li>'
        for c in warnings)
    return f"""
      <tr><td colspan="4" style="padding:12px;">
        <table width="100%" cellpadding="0" cellspacing="0" border="0"
               bgcolor="#fff8e1" style="border:1px solid #f0d98c;">
          <tr><td style="padding:12px 14px;">
            <div style="font-size:13px;font-weight:bold;color:{COLORS['ink']};
                        padding-bottom:4px;">Cần chú ý</div>
            <ul style="margin:0;padding-left:18px;color:{COLORS['ink']};">{rows}</ul>
          </td></tr>
        </table>
      </td></tr>"""


def _chart_block(has_charts: bool, bucket_label: str) -> str:
    """Hai mục biểu đồ. Rỗng khi kỳ báo cáo quá ngắn để vẽ được gì."""
    if not has_charts:
        return ""
    return f"""
  {_divider()}
  {_section_title("Xu hướng xử lý chứng chỉ")}
  <tr><td colspan="4" align="center" style="padding:4px 12px 12px;">
    <img src="cid:chart_trend" width="640"
         alt="Biểu đồ đường: tổng xử lý và số được duyệt theo {_e(bucket_label)}"
         style="display:block;border:0;max-width:640px;width:100%;"></td></tr>

  {_divider()}
  {_section_title("Được duyệt / Từ chối")}
  <tr><td colspan="4" align="center" style="padding:4px 12px 12px;">
    <img src="cid:chart_split" width="640"
         alt="Biểu đồ cột chồng: cơ cấu kết quả theo {_e(bucket_label)}"
         style="display:block;border:0;max-width:640px;width:100%;"></td></tr>"""


def _exclusion_note(stats: dict) -> str:
    """Một dòng ở chân báo cáo cho số ca bị loại vì hỏng kỹ thuật.

    Không dựng thành mục riêng — người đọc báo cáo này quan tâm kết quả
    nghiệp vụ, không quan tâm hạ tầng. Nhưng cũng KHÔNG giấu hẳn: im lặng
    bỏ bớt bản ghi là cách một báo cáo bắt đầu nói dối. Ai cần chi tiết thì
    tra mooc_log.db, cột stage.
    """
    n = stats.get("excluded_technical", 0)
    if not n:
        return ""
    return (f" Đã loại {_n(n)} chứng chỉ khỏi thống kê vì hệ thống không đọc "
            f"được (eLIS không trả file, AI lỗi…) — không phải nhân viên khai sai.")


def build_html(stats: dict) -> tuple[str, list[tuple[str, bytes]]]:
    """Dựng HTML báo cáo. Trả về (html, [(cid, png_bytes), ...])."""
    anh: list[tuple[str, bytes]] = []
    xu_huong = stats["trend"]

    # Ít hơn hai mốc thì KHÔNG vẽ biểu đồ. Một điểm không thành đường, và
    # một cột chồng chỉ lặp lại đúng những con số đã có ở hàng KPI phía trên
    # — thêm hình chỉ làm báo cáo dài ra mà không nói thêm được gì.
    has_charts = len(xu_huong) >= 2
    if has_charts:
        anh.append(("chart_trend", charts.line_chart(xu_huong)))
        anh.append(("chart_split", charts.stacked_bar_chart(xu_huong)))

    bucket_label = {"day": "ngày", "week": "tuần", "month": "tháng"}.get(
        stats.get("bucket", "day"), stats.get("bucket"))

    bullets = "".join(
        f'<li style="padding:3px 0;font-size:13px;line-height:1.5;">{_e(b)}</li>'
        for b in key_findings(stats))

    return f"""
<div style="font-family:Segoe UI,Arial,Helvetica,sans-serif;
            background-color:{COLORS['surface']};padding:20px;">
 <table width="680" cellpadding="0" cellspacing="0" border="0" align="center"
        style="background-color:#ffffff;border:1px solid {COLORS['border']};">

  <tr><td colspan="4" style="padding:20px 12px 4px;">
    <div style="font-size:18px;font-weight:bold;letter-spacing:.5px;
                color:{COLORS['ink']};">BÁO CÁO XÁC MINH CHỨNG CHỈ</div>
    <div style="font-size:12px;color:{COLORS['ink2']};padding-top:4px;">
      {_e(stats['from_day'])} &nbsp;→&nbsp; {_e(stats['to_day'])}
      &nbsp;·&nbsp; thống kê theo {_e(bucket_label)}</div>
  </td></tr>

  {_divider()}
  <tr>
    {_kpi_cell("Tổng xử lý", _n(stats['total']))}
    {_kpi_cell("Được duyệt", _n(stats['approved']), COLORS['green'])}
    {_kpi_cell("Từ chối", _n(stats['rejected']), COLORS['blue'])}
    {_kpi_cell("Tỷ lệ duyệt", f"{stats['approval_rate']:.0f}%", COLORS['green'])}
  </tr>

  {_warning_block(stats.get("warnings") or [])}
  {_chart_block(has_charts, bucket_label)}

  {_divider()}
  {_section_title(f"Lý do từ chối ({_n(stats['rejected'])} chứng chỉ)")}
  {_rejection_reason_table(stats['rejection_causes'])}

  {_divider()}
  {_section_title("Theo nhà cung cấp chứng chỉ")}
  {_provider_table(stats['by_provider'])}

  {_divider()}
  {_section_title("Key Findings")}
  <tr><td colspan="4" style="padding:0 12px 18px;">
    <ul style="margin:0;padding-left:20px;color:{COLORS['ink']};">{bullets}</ul>
  </td></tr>

  <tr><td colspan="4" bgcolor="#f6f8fa"
      style="padding:10px 12px;font-size:11px;color:{COLORS['muted']};">
    Báo cáo tự động từ hệ thống xác minh chứng chỉ MOOC.{_exclusion_note(stats)}
  </td></tr>
 </table>
</div>""", anh


def build_text(stats: dict) -> str:
    """Bản text thuần — phần text/plain của email, và fallback khi ảnh bị chặn.

    Không phải hình thức: một số client (và người đọc trên đồng hồ, hoặc
    Outlook đặt chế độ text) không thấy ảnh nào cả. Bản này phải tự nó đủ
    nghĩa, nên nó chứa cả bảng số của hai biểu đồ.
    """
    d = ["BÁO CÁO XÁC MINH CHỨNG CHỈ MOOC",
         f"{stats['from_day']} -> {stats['to_day']}", "",
         f"  Tổng xử lý     : {_n(stats['total'])}",
         f"  Được duyệt     : {_n(stats['approved'])} ({stats['approval_rate']:.0f}%)",
         f"  Từ chối        : {_n(stats['rejected'])}",
         f"  Tỷ lệ duyệt    : {stats['approval_rate']:.0f}%"]

    if stats["trend"]:
        d += ["", "XU HƯỚNG (số liệu của biểu đồ):",
              f"  {'Mốc':<12}{'Tổng':>8}{'Duyệt':>8}{'Từ chối':>9}"]
        for p in stats["trend"]:
            d.append(f"  {p['bucket']:<12}{_n(p['total']):>8}"
                     f"{_n(p['approved']):>8}{_n(p['rejected']):>9}")

    rc = stats["rejection_causes"]
    if rc["total_rejected"]:
        d += ["", f"LÝ DO TỪ CHỐI ({_n(rc['total_rejected'])} chứng chỉ):"]
        for m in rc["causes"]:
            pt = m["count"] / rc["total_rejected"] * 100
            d.append(f"  {m['label']:<22}{_n(m['count']):>6}  ({pt:.0f}%)")
        if rc["multi_cause"]:
            d.append(f"  (Tổng lớn hơn {_n(rc['total_rejected'])} vì "
                     f"{_n(rc['multi_cause'])} chứng chỉ sai từ 2 tiêu chí trở lên.)")

    if stats["by_provider"]:
        d += ["", "THEO NHÀ CUNG CẤP:",
              f"  {'Nhà cung cấp':<22}{'Tổng':>7}{'Duyệt':>8}{'Từ chối':>9}{'Tỷ lệ':>8}"]
        for m in stats["by_provider"]:
            d.append(f"  {m['provider'][:21]:<22}{_n(m['total']):>7}"
                     f"{_n(m['approved']):>8}{_n(m['rejected']):>9}"
                     f"{m['approval_rate']:>7.0f}%")

    if stats.get("warnings"):
        d += ["", "CẦN CHÚ Ý:"] + [f"  - {c}" for c in stats["warnings"]]

    d += ["", "KEY FINDINGS:"] + [f"  - {b}" for b in key_findings(stats)]
    return "\n".join(d)
