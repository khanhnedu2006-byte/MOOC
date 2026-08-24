"""Soạn số liệu báo cáo từ bảng log (report).

Đọc SQLite, gom số liệu một KHOẢNG ngày, trả về dict thuần. KHÔNG gửi mail,
KHÔNG dựng HTML — hai việc đó nằm ở report_layout.py và send_report.py. Tách
ra để test được số liệu mà không cần mạng, và để đổi cách gửi mà không phải
sửa chỗ tính toán.

Dùng:
    from database import report
    so_lieu = report.period_report("2026-08-01", "2026-08-31", "week")
    so_lieu = report.period_report("2026-08-17", "2026-08-17")   # một ngày
"""

import sqlite3
from datetime import date, timedelta

from database.database import DB_PATH, _connect, init_db

def _default_day() -> str:
    """Hôm qua, dạng YYYY-MM-DD.

    Báo cáo thường chạy vào sáng sớm hoặc cuối ngày cho ngày ĐÃ KẾT THÚC,
    nên mặc định là hôm qua chứ không phải hôm nay.
    """
    return (date.today() - timedelta(days=1)).isoformat()


def _count(conn, day, condition="", params=()):
    sql = "SELECT COUNT(*) FROM process_log WHERE substr(created_at,1,10)=?"
    if condition:
        sql += f" AND {condition}"
    return conn.execute(sql, (day, *params)).fetchone()[0]


def _warning_block(total, excluded, send_failed, unsent, rejected) -> list[str]:
    """Những điều người đọc cần chú ý, diễn giải sẵn thành câu.

    Mục đích: người nhận mail không phải tự suy luận từ các con số. Một báo
    cáo "0 xử lý" có thể là ngày nghỉ, cũng có thể là job chết — phải nói rõ.

    excluded = số ca bị loại vì hỏng kỹ thuật. Không nằm trong `total` (xem
    period_report), nhưng vẫn phải cảnh báo khi nó lớn: hệ thống đang bỏ sót
    chứng chỉ mà nhìn các con số chính thì không thấy gì bất thường.
    """
    output = []
    if total == 0 and not excluded:
        output.append("Không xử lý chứng chỉ nào. Có thể không ai nộp, "
                      "cũng có thể job không chạy — nên kiểm tra job còn sống không.")
        return output

    if excluded:
        mau_so = total + excluded
        ratio = excluded / mau_so * 100
        output.append(
            f"{excluded}/{mau_so} chứng chỉ ({ratio:.0f}%) hệ thống không đọc "
            f"được nên đã bị loại khỏi thống kê — lỗi kỹ thuật, không phải "
            f"nhân viên khai sai.")
        if ratio >= 50:
            output.append("Quá nửa số chứng chỉ không đọc được file — nhiều khả "
                          "năng API tải file của eLIS đang hỏng.")

    if send_failed:
        output.append(f"{send_failed} kết quả bị eLIS từ chối khi nộp. Thường do "
                      f"bản ghi đã được duyệt trước đó, hoặc sai mã nhân viên.")

    if unsent:
        output.append(f"{unsent} kết quả chưa nộp được lên eLIS.")

    if total and rejected / total >= 0.9 and rejected > 3:
        output.append(f"Tỷ lệ từ chối rất cao ({rejected}/{total}). Nếu phần lớn "
                      f"cùng một lý do thì nên xem lại luật đối chiếu.")
    return output


# =====================================================================
# BÁO CÁO THEO KHOẢNG (period_report) — dùng cho báo cáo tuần/tháng
# =====================================================================
#
# Nhận KHOẢNG [from_day, to_day] và gom số liệu cho toàn kỳ:
#   - xu hướng theo mốc thời gian (để vẽ biểu đồ đường)
#   - phân loại thất bại theo nhóm nguyên nhân
#   - thống kê theo nhà cung cấp chứng chỉ (providerName)

# Gom stage thành NHÓM NGUYÊN NHÂN mà người đọc hiểu được.
#
# Vì sao phải gom: "llm1_vs_llm2" hay "stage2_error" chỉ có nghĩa với người
# viết code. Mentor đọc báo cáo cần biết "lỗi do phía nào" để biết gọi ai —
# gọi eLIS, gọi đội hạ tầng, hay tự sửa prompt.
FAILURE_GROUPS = {
    # eLIS không trả được file -> lỗi phía eLIS, không phải phía mình.
    "download_error": "Không tải được file từ eLIS",
    "no_file":        "eLIS không trả về file",
    "file_error":     "File hỏng hoặc sai định dạng",
    # AI/hạ tầng phía mình.
    "llm1_error":     "Lỗi gọi AI",
    "stage2_error":   "Lỗi OCR / AI tầng 2",
    "system_error":   "Lỗi hệ thống",
    "soft_fail_zip":  "Lỗi giải nén (tồn dư)",
}

# Các stage KHÔNG phải lỗi kỹ thuật — là kết luận nghiệp vụ thật.
BUSINESS_STAGES = ("llm1", "llm2", "llm1_vs_llm2")


def _rows(conn, sql, params=()):
    return list(conn.execute(sql, params))


def _period_where(from_day: str, to_day: str) -> tuple[str, tuple]:
    return "substr(created_at,1,10) BETWEEN ? AND ?", (from_day, to_day)


# Ba nguyên nhân từ chối NGHIỆP VỤ. Chuỗi phải khớp CHÍNH XÁC với chuỗi
# pipeline._mismatch_reason() sinh ra — đổi ở một bên mà quên bên kia thì
# báo cáo sẽ đếm ra 0 mà không báo lỗi gì. Có test canh chuyện này.
REJECTION_CAUSES = (
    ("Tên không khớp", "Sai tên"),
    ("Tên khóa học không khớp", "Sai tên khóa học"),
    ("Ngày không hợp lệ", "Sai ngày"),
)


def rejection_causes(from_day: str, to_day: str, db_path=None) -> dict:
    """Đếm ca từ chối theo từng nguyên nhân: sai tên / sai khóa học / sai ngày.

    CHỈ tính ca từ chối NGHIỆP VỤ — ca hỏng kỹ thuật (không tải được file,
    AI lỗi) cũng nằm ở REJECTED nhưng không phải nhân viên khai sai.

    MỘT CHỨNG CHỈ CÓ THỂ SAI NHIỀU THỨ. reason trong DB ghi dạng
    "Tên không khớp; Ngày không hợp lệ", nên tổng ba con số LỚN HƠN số ca
    từ chối. Đây không phải lỗi tính toán — nhưng nếu báo cáo không nói rõ
    thì người đọc sẽ cộng lại, thấy lệch, và mất lòng tin vào cả bản báo cáo.
    Vì vậy hàm trả về cả `total_rejected` và `multi_cause` để chỗ hiển thị
    giải thích được.
    """
    conn = _connect(db_path)
    try:
        w, p = _period_where(from_day, to_day)
        rows = _rows(conn, f"SELECT reason FROM process_log WHERE {w} "
                           f"AND verdict='REJECTED' AND stage IN {BUSINESS_STAGES}", p)
    finally:
        conn.close()

    dem = {nhan: 0 for _, nhan in REJECTION_CAUSES}
    khac = 0
    nhieu_ly_do = 0
    for (reason,) in rows:
        r = reason or ""
        trung = [nhan for chuoi, nhan in REJECTION_CAUSES if chuoi in r]
        for nhan in trung:
            dem[nhan] += 1
        if len(trung) >= 2:
            nhieu_ly_do += 1
        if not trung:
            khac += 1

    muc = [{"label": nhan, "count": dem[nhan]} for _, nhan in REJECTION_CAUSES]
    if khac:
        # Không phân loại được: reason lạ, hoặc dòng cũ ghi bằng định dạng khác.
        # Hiện ra chứ không giấu — giấu thì tổng không bao giờ khớp.
        muc.append({"label": "Không rõ nguyên nhân", "count": khac})

    return {
        "causes": muc,
        "total_rejected": len(rows),
        "multi_cause": nhieu_ly_do,
    }


def failure_breakdown(from_day: str, to_day: str, db_path=None) -> list[dict]:
    """Phân loại THẤT BẠI theo nhóm nguyên nhân, nhiều nhất trước.

    Chỉ đếm ca REJECTED do lỗi kỹ thuật. Ca REJECTED vì nghiệp vụ (tên không
    khớp, sai khóa học) KHÔNG phải "thất bại" — đó là hệ thống làm đúng việc
    của nó. Trộn hai loại vào một con số là cách nhanh nhất để báo cáo nói
    dối: một hôm mạng chập sẽ trông y hệt một hôm nhiều người khai gian.
    """
    conn = _connect(db_path)
    try:
        w, p = _period_where(from_day, to_day)
        rows = _rows(conn, f"SELECT stage, COUNT(*) AS n FROM process_log "
                           f"WHERE {w} AND stage NOT IN {BUSINESS_STAGES} "
                           f"GROUP BY stage ORDER BY n DESC", p)
    finally:
        conn.close()

    gom: dict[str, int] = {}
    for stage, n in rows:
        nhan = FAILURE_GROUPS.get(stage, f"Khác ({stage or 'không rõ'})")
        gom[nhan] = gom.get(nhan, 0) + n
    return [{"label": k, "count": v}
            for k, v in sorted(gom.items(), key=lambda x: -x[1])]


def by_provider(from_day: str, to_day: str, db_path=None) -> list[dict]:
    """Thống kê theo nhà cung cấp chứng chỉ (Udemy, Coursera...).

    Dòng cũ chưa có cột provider sẽ mang NULL -> gom vào "(không rõ)" chứ
    KHÔNG bỏ đi, để tổng của bảng này luôn khớp tổng đã xử lý. Bảng con số
    không cộng lại đúng tổng là bảng không ai tin được.

    Cùng phạm vi với period_report: chỉ ca AI phán đoán được, bỏ ca hỏng
    kỹ thuật. Khác phạm vi thì bảng này không cộng ra bằng KPI ở đầu báo cáo.
    """
    conn = _connect(db_path)
    try:
        w, p = _period_where(from_day, to_day)
        rows = _rows(conn, f"""
            SELECT COALESCE(NULLIF(TRIM(provider),''), '(không rõ)') AS ncc,
                   COUNT(*) AS total,
                   SUM(verdict='APPROVED') AS approved,
                   SUM(verdict='REJECTED') AS rejected
            FROM process_log
            WHERE {w} AND stage IN {BUSINESS_STAGES}
            GROUP BY ncc ORDER BY total DESC""", p)
    finally:
        conn.close()

    return [{"provider": r[0], "total": r[1], "approved": r[2] or 0,
             "rejected": r[3] or 0,
             "approval_rate": round((r[2] or 0) / r[1] * 100, 1) if r[1] else 0.0}
            for r in rows]


def trend(from_day: str, to_day: str, bucket: str = "day", db_path=None) -> list[dict]:
    """Xu hướng theo mốc thời gian, để vẽ biểu đồ đường.

    bucket: "day" | "week" | "month".

    Mốc TUẦN dùng %W của SQLite (tuần bắt đầu từ THỨ HAI) — đúng với cách
    người Việt và lịch ISO hiểu "tuần", khác %U vốn bắt đầu Chủ nhật.

    Trả về danh sách theo thứ tự thời gian TĂNG DẦN, đã điền đủ các mốc
    KHÔNG có dữ liệu bằng 0. Thiếu bước điền này thì biểu đồ đường sẽ nối
    thẳng qua ngày nghỉ, trông như hệ thống vẫn chạy đều trong khi thực tế
    là không có gì cả.
    """
    if bucket not in ("day", "week", "month"):
        raise ValueError(f"bucket phải là day/week/month, nhận: {bucket!r}")

    dinh_dang = {"day": "%Y-%m-%d", "week": "%Y-W%W", "month": "%Y-%m"}[bucket]
    conn = _connect(db_path)
    try:
        w, p = _period_where(from_day, to_day)
        rows = _rows(conn, f"""
            SELECT strftime('{dinh_dang}', created_at) AS moc,
                   COUNT(*) AS total,
                   SUM(verdict='APPROVED') AS approved,
                   SUM(verdict='REJECTED') AS rejected
            FROM process_log
            WHERE {w} AND stage IN {BUSINESS_STAGES}
            GROUP BY moc ORDER BY moc""", p)
    finally:
        conn.close()

    co_du_lieu = {
        r[0]: {"bucket": r[0], "total": r[1], "approved": r[2] or 0,
               "rejected": r[3] or 0}
        for r in rows
    }
    ra = []
    for moc in _cac_moc(from_day, to_day, bucket):
        ra.append(co_du_lieu.get(moc, {"bucket": moc, "total": 0,
                                       "approved": 0, "rejected": 0}))
    return ra


def _cac_moc(from_day: str, to_day: str, bucket: str) -> list[str]:
    """Liệt kê MỌI mốc trong khoảng, kể cả mốc không có dữ liệu."""
    d0, d1 = date.fromisoformat(from_day), date.fromisoformat(to_day)
    thay = []
    seen = set()
    d = d0
    while d <= d1:
        if bucket == "day":
            khoa = d.isoformat()
        elif bucket == "week":
            # %W của SQLite: tuần bắt đầu thứ Hai, tuần trước thứ Hai đầu
            # tiên của năm là tuần 00. strftime của Python dùng cùng quy ước.
            khoa = d.strftime("%Y-W%W")
        else:
            khoa = d.strftime("%Y-%m")
        if khoa not in seen:
            seen.add(khoa)
            thay.append(khoa)
        d += timedelta(days=1)
    return thay


def period_report(from_day: str, to_day: str, bucket: str = "day",
                  db_path=None) -> dict:
    """Số liệu đầy đủ cho một khoảng thời gian."""
    init_db(db_path)
    conn = _connect(db_path)
    try:
        w, p = _period_where(from_day, to_day)
        def dem(dk="", extra=()):
            sql = f"SELECT COUNT(*) FROM process_log WHERE {w}"
            if dk:
                sql += f" AND {dk}"
            return conn.execute(sql, p + extra).fetchone()[0]

        # PHẠM VI BÁO CÁO = chứng chỉ AI THỰC SỰ phán đoán được.
        # Ca hỏng kỹ thuật bị loại khỏi mọi con số, vì trộn vào thì "tỷ lệ
        # duyệt" mất nghĩa: một hôm eLIS sập sẽ kéo tỷ lệ xuống trong khi AI
        # không hề làm gì sai. Số bị loại vẫn được trả về ở excluded_technical
        # để báo cáo ghi một dòng chú thích — loại bỏ thì được, giấu thì không.
        nghiep_vu = f"stage IN {BUSINESS_STAGES}"
        total = dem(nghiep_vu)
        approved = dem(f"{nghiep_vu} AND verdict='APPROVED'")
        rejected = dem(f"{nghiep_vu} AND verdict='REJECTED'")
        failed = dem(f"stage NOT IN {BUSINESS_STAGES}")
        sent_ok = dem("elis_sent_ok=1")
        send_failed = dem("elis_sent_ok=0")
        unsent = dem("elis_sent_ok IS NULL")
    finally:
        conn.close()

    return {
        "from_day": from_day,
        "to_day": to_day,
        "bucket": bucket,
        "total": total,
        "approved": approved,
        "rejected": rejected,
        "approval_rate": round(approved / total * 100, 1) if total else 0.0,
        # Số ca bị LOẠI khỏi báo cáo vì hỏng kỹ thuật. Không hiển thị thành
        # mục riêng, chỉ một dòng chú thích ở chân báo cáo.
        "excluded_technical": failed,
        "elis": {"accepted": sent_ok, "rejected_by_elis": send_failed,
                 "unsent": unsent},
        "warnings": _warning_block(total, failed, send_failed, unsent, rejected),
        "rejection_causes": rejection_causes(from_day, to_day, db_path),
        "by_provider": by_provider(from_day, to_day, db_path),
        "trend": trend(from_day, to_day, bucket, db_path),
    }
