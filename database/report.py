"""Soạn số liệu báo cáo từ bảng log (report).

Đọc SQLite, gom số liệu một KHOẢNG ngày, trả về dict thuần. KHÔNG gửi mail,
KHÔNG dựng HTML — hai việc đó nằm ở report_layout.py và send_report.py, tách
ra để test số liệu không cần mạng.

Dùng:
    from database import report
    so_lieu = report.period_report("2026-08-01", "2026-08-31", "week")
    so_lieu = report.period_report("2026-08-17", "2026-08-17")   # một ngày
"""

from datetime import date, timedelta

from database.database import _connect, init_db

def _default_day() -> str:
    """Hôm qua, dạng YYYY-MM-DD.

    Báo cáo luôn tính cho ngày ĐÃ KẾT THÚC, nên mặc định là hôm qua.
    """
    return (date.today() - timedelta(days=1)).isoformat()


def _count(conn, day, condition="", params=()):
    sql = "SELECT COUNT(*) FROM process_log WHERE substr(created_at,1,10)=?"
    if condition:
        sql += f" AND {condition}"
    return conn.execute(sql, (day, *params)).fetchone()[0]


def _warning_block(total, excluded, send_failed, unsent, rejected) -> list[str]:
    """Những điều người đọc cần chú ý, diễn giải sẵn thành câu.

    Báo cáo "0 xử lý" có thể là ngày nghỉ hoặc job chết, phải nói rõ.

    excluded = ca bị loại vì hỏng kỹ thuật, KHÔNG nằm trong `total` (xem
    period_report) nhưng vẫn phải cảnh báo khi lớn.
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
# Nhận [from_day, to_day] và gom cho toàn kỳ: xu hướng theo mốc thời gian,
# phân loại thất bại theo nhóm nguyên nhân, thống kê theo providerName.

# Gom stage thành NHÓM NGUYÊN NHÂN người đọc hiểu được: "stage2_error" chỉ có
# nghĩa với người viết code, còn người đọc báo cáo cần biết lỗi do phía nào.
FAILURE_GROUPS = {
    # Lỗi phía eLIS.
    "download_error": "Không tải được file từ eLIS",
    "no_file":        "eLIS không trả về file",
    "file_error":     "File hỏng hoặc sai định dạng",
    "duplicate_check_error": "Không tra được lịch sử để kiểm nộp trùng",
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
# pipeline._mismatch_reason() sinh ra: lệch một bên thì báo cáo đếm ra 0 mà
# không báo lỗi. Có test canh chuyện này.
REJECTION_CAUSES = (
    ("Tên không khớp", "Sai tên"),
    ("Tên khóa học không khớp", "Sai tên khóa học"),
    ("Ngày không hợp lệ", "Sai ngày"),
)


def rejection_causes(from_day: str, to_day: str, db_path=None) -> dict:
    """Đếm ca từ chối theo từng nguyên nhân: sai tên / sai khóa học / sai ngày.

    CHỈ tính ca từ chối NGHIỆP VỤ; ca hỏng kỹ thuật cũng ở REJECTED nhưng
    không phải nhân viên khai sai.

    MỘT CHỨNG CHỈ CÓ THỂ SAI NHIỀU THỨ (reason ghi dạng "Tên không khớp; Ngày
    không hợp lệ"), nên tổng ba con số LỚN HƠN số ca từ chối; hàm trả thêm
    `total_rejected` và `multi_cause` để chỗ hiển thị giải thích.
    """
    conn = _connect(db_path)
    try:
        w, p = _period_where(from_day, to_day)
        rows = _rows(conn, f"SELECT reason FROM process_log WHERE {w} "
                           f"AND verdict='REJECTED' AND stage IN {BUSINESS_STAGES}", p)
    finally:
        conn.close()

    dem = {label: 0 for _, label in REJECTION_CAUSES}
    khac = 0
    nhieu_ly_do = 0
    for (reason,) in rows:
        r = reason or ""
        trung = [label for text, label in REJECTION_CAUSES if text in r]
        for label in trung:
            dem[label] += 1
        if len(trung) >= 2:
            nhieu_ly_do += 1
        if not trung:
            khac += 1

    items = [{"label": label, "count": dem[label]} for _, label in REJECTION_CAUSES]
    if khac:
        # Không phân loại được (reason lạ, hoặc dòng cũ khác định dạng).
        # Vẫn hiện ra, giấu thì tổng không khớp.
        items.append({"label": "Không rõ nguyên nhân", "count": khac})

    return {
        "causes": items,
        "total_rejected": len(rows),
        "multi_cause": nhieu_ly_do,
    }


def failure_breakdown(from_day: str, to_day: str, db_path=None) -> list[dict]:
    """Phân loại THẤT BẠI theo nhóm nguyên nhân, nhiều nhất trước.

    Chỉ đếm ca REJECTED do lỗi kỹ thuật; ca REJECTED vì nghiệp vụ không phải
    "thất bại". Trộn hai loại thì một hôm mạng chập trông y hệt một hôm nhiều
    người khai gian.
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
        label = FAILURE_GROUPS.get(stage, f"Khác ({stage or 'không rõ'})")
        gom[label] = gom.get(label, 0) + n
    return [{"label": k, "count": v}
            for k, v in sorted(gom.items(), key=lambda x: -x[1])]


def by_provider(from_day: str, to_day: str, db_path=None) -> list[dict]:
    """Thống kê theo nhà cung cấp chứng chỉ (Udemy, Coursera...).

    Dòng cũ chưa có cột provider mang NULL -> gom vào "(không rõ)" chứ KHÔNG
    bỏ đi, để tổng bảng luôn khớp tổng đã xử lý. Cùng phạm vi với
    period_report (bỏ ca hỏng kỹ thuật), nếu không thì bảng không cộng ra
    bằng KPI ở đầu báo cáo.
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

    Mốc TUẦN dùng %W của SQLite (tuần bắt đầu THỨ HAI, theo lịch ISO), khác
    %U vốn bắt đầu Chủ nhật. Trả về theo thời gian TĂNG DẦN, đã điền 0 cho các
    mốc trống; thiếu bước này thì biểu đồ đường nối thẳng qua ngày nghỉ.
    """
    if bucket not in ("day", "week", "month"):
        raise ValueError(f"bucket phải là day/week/month, nhận: {bucket!r}")

    dinh_dang = {"day": "%Y-%m-%d", "week": "%Y-W%W", "month": "%Y-%m"}[bucket]
    conn = _connect(db_path)
    try:
        w, p = _period_where(from_day, to_day)
        rows = _rows(conn, f"""
            SELECT strftime('{dinh_dang}', created_at) AS bucket,
                   COUNT(*) AS total,
                   SUM(verdict='APPROVED') AS approved,
                   SUM(verdict='REJECTED') AS rejected
            FROM process_log
            WHERE {w} AND stage IN {BUSINESS_STAGES}
            GROUP BY bucket ORDER BY bucket""", p)
    finally:
        conn.close()

    co_du_lieu = {
        r[0]: {"bucket": r[0], "total": r[1], "approved": r[2] or 0,
               "rejected": r[3] or 0}
        for r in rows
    }
    out = []
    for point in _cac_moc(from_day, to_day, bucket):
        out.append(co_du_lieu.get(point, {"bucket": point, "total": 0,
                                          "approved": 0, "rejected": 0}))
    return out


def _cac_moc(from_day: str, to_day: str, bucket: str) -> list[str]:
    """Liệt kê MỌI mốc trong khoảng, kể cả mốc không có dữ liệu."""
    d0, d1 = date.fromisoformat(from_day), date.fromisoformat(to_day)
    thay = []
    seen = set()
    d = d0
    while d <= d1:
        if bucket == "day":
            key = d.isoformat()
        elif bucket == "week":
            # %W: tuần bắt đầu thứ Hai; tuần trước thứ Hai đầu tiên của năm là
            # tuần 00. SQLite và Python dùng cùng quy ước.
            key = d.strftime("%Y-W%W")
        else:
            key = d.strftime("%Y-%m")
        if key not in seen:
            seen.add(key)
            thay.append(key)
        d += timedelta(days=1)
    return thay


# Danh sách chứng chỉ liệt kê trong một thư. Cắt bớt để thư không phình:
# Outlook cắt thư quá ~102 KB và giấu phần sau vào một link "xem thêm".
MAX_CERT_ROWS = 200


def certificates_on_day(day: str, limit: int = MAX_CERT_ROWS,
                        db_path=None) -> dict:
    """Danh sách chứng chỉ đã xử lý trong MỘT ngày, mới nhất trước.

    Lấy theo ngày lịch nên thư gửi lúc 18h chứa mọi ca từ 00:00 tới 18h của
    chính ngày đó.

    Khác các hàm khác trong file: KHÔNG loại ca hỏng kỹ thuật. Người đọc cần
    thấy cả ca máy chưa kết luận được, còn các con số tổng hợp thì vẫn loại
    chúng ra như cũ.

    Trả về {"day", "rows", "total", "truncated"}. `total` là số thật, `rows`
    có thể ngắn hơn khi vượt `limit`.
    """
    init_db(db_path)
    conn = _connect(db_path)
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM process_log WHERE substr(created_at,1,10)=?",
            (day,)).fetchone()[0]
        rows = _rows(conn, """
            SELECT substr(created_at, 12, 5) AS gio,
                   employee_code, employee_id, name_on_image,
                   course_name, certificate_name,
                   verdict, reason, stage, elis_sent_ok
            FROM process_log
            WHERE substr(created_at,1,10)=?
            ORDER BY id DESC
            LIMIT ?
        """, (day, limit))
    finally:
        conn.close()

    return {
        "day": day,
        "total": total,
        "truncated": max(0, total - len(rows)),
        "rows": [{
            "time": r[0],
            "employee": r[1] or r[2] or "?",
            "name_on_image": r[3] or "",
            # Tên khóa eLIS đăng ký là cái người đọc tra được; tên AI đọc từ
            # ảnh chỉ dùng khi cột kia trống (dòng log cũ).
            "course": r[4] or r[5] or "?",
            "verdict": r[6] or "",
            "reason": r[7] or "",
            "stage": r[8] or "",
            "elis_sent_ok": r[9],
        } for r in rows],
    }


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

        # PHẠM VI BÁO CÁO = chứng chỉ AI thực sự phán đoán được. Ca hỏng kỹ
        # thuật bị loại khỏi mọi con số vì trộn vào thì "tỷ lệ duyệt" mất
        # nghĩa. Số bị loại vẫn trả về ở excluded_technical để ghi chú thích.
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
        # Ca bị LOẠI vì hỏng kỹ thuật; chỉ hiện thành một dòng chú thích ở
        # chân báo cáo.
        "excluded_technical": failed,
        "elis": {"accepted": sent_ok, "rejected_by_elis": send_failed,
                 "unsent": unsent},
        "warnings": _warning_block(total, failed, send_failed, unsent, rejected),
        "rejection_causes": rejection_causes(from_day, to_day, db_path),
        "by_provider": by_provider(from_day, to_day, db_path),
        "trend": trend(from_day, to_day, bucket, db_path),
        # Danh sách chi tiết của ĐÚNG NGÀY GỬI, tính tới giờ gửi. Các con số
        # phía trên gom cả kỳ (7 ngày với lịch daily), phần này chỉ một ngày.
        "certificates": certificates_on_day(to_day, db_path=db_path),
    }
