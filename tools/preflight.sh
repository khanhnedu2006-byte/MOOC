#!/usr/bin/env bash
# Kiểm mọi thứ TRƯỚC khi bật job chạy nền trên server.
#
#   bash tools/preflight.sh
#
# Bật systemd rồi mới phát hiện thiếu .env hoặc IP chưa allowlist thì job
# restart vô tận trong nền, phải đi đọc journalctl mới biết.
#
# Không in ra giá trị key nào, chỉ nói có hay không.

set -u
cd "$(dirname "$0")/.." || exit 1

DAT=0
LOI=0
CANH_BAO=0

ok()   { printf '  \033[32m[OK]\033[0m   %s\n' "$1"; DAT=$((DAT+1)); }
loi()  { printf '  \033[31m[LỖI]\033[0m  %s\n' "$1"; LOI=$((LOI+1)); }
canh() { printf '  \033[33m[LƯU Ý]\033[0m %s\n' "$1"; CANH_BAO=$((CANH_BAO+1)); }

PY="${PY:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3

echo
echo "=== 1. Môi trường Python ==="
if [ -x .venv/bin/python ]; then
    ok "Có .venv riêng: $(.venv/bin/python -V 2>&1)"
else
    canh "Chưa có .venv — đang dùng $($PY -V 2>&1) của hệ thống.
           Nên tạo: python3 -m venv .venv && .venv/bin/pip install -r requirements-job.txt"
fi

if $PY -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    ok "Phiên bản Python đủ mới (cần >= 3.10)"
else
    loi "Python quá cũ. Code dùng cú pháp 'str | None' nên cần >= 3.10"
fi

echo
echo "=== 2. Thư viện ==="
for M in pydantic_settings langchain_openai azure.ai.documentintelligence \
         requests dateparser unidecode pypdfium2 PIL; do
    if $PY -c "import $M" 2>/dev/null; then
        ok "import $M"
    else
        loi "thiếu $M — chạy: $PY -m pip install -r requirements-job.txt"
    fi
done

# python-magic cần thư viện hệ thống libmagic, không có trong pip. Thiếu nó
# thì src/file_utils.py chết lúc import.
if $PY -c "import magic" 2>/dev/null; then
    ok "import magic (libmagic có sẵn)"
else
    loi "python-magic không nạp được. Cần thư viện hệ thống:
           sudo apt install -y libmagic1
           (không có quyền sudo thì phải nhờ quản trị server)"
fi

echo
echo "=== 3. File cấu hình và file trạng thái ==="
if [ -f .env ]; then
    ok ".env có mặt"
    QUYEN=$(stat -c '%a' .env)
    if [ "$QUYEN" = "600" ] || [ "$QUYEN" = "400" ]; then
        ok ".env chỉ mình bạn đọc được (quyền $QUYEN)"
    else
        canh ".env đang để quyền $QUYEN — người dùng khác trên server đọc được key.
           Sửa: chmod 600 .env"
    fi
else
    loi "KHÔNG có .env. Chép từ máy bạn sang (đừng commit vào git):
           scp .env $(whoami)@$(hostname):$(pwd)/"
fi

# Ba file này phải tồn tại trước khi chạy. Thiếu .alert_state.json thì mỗi
# lần systemd dựng lại job là gửi thêm một thư cảnh báo.
for F in mooc_log.db .report_state.json .alert_state.json; do
    if [ -e "$F" ]; then
        ok "$F có mặt"
    else
        case "$F" in
            mooc_log.db) canh "$F chưa có — sẽ tự tạo lúc chạy lần đầu" ;;
            *) canh "$F chưa có — tạo trước cho chắc:  echo '{}' > $F" ;;
        esac
    fi
done

echo
echo "=== 4. Múi giờ ==="
TZ_HT=$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null)
if [ "$TZ_HT" = "Asia/Ho_Chi_Minh" ]; then
    ok "Server đang ở giờ Việt Nam"
else
    canh "Server đang ở múi giờ '$TZ_HT'.
           Log sẽ lệch giờ và báo cáo định kỳ gửi sai so với REPORT_TIME.
           File tools/mooc.service đã đặt TZ=Asia/Ho_Chi_Minh cho riêng job."
fi

echo
echo "=== 5. Gọi thử eLIS (chỉ ĐỌC, không xử lý, không tốn tiền LLM) ==="
echo "    eLIS chặn theo IP, và IP server thường khác IP máy bạn."
echo
# Nuốt traceback: run.py in nguyên vết gọi khi lỗi mạng, che mất mã lỗi.
RA=$(mktemp)
if KMP_DUPLICATE_LIB_OK=TRUE timeout 60 "$PY" run.py status >"$RA" 2>&1; then
    head -20 "$RA" | sed 's/^/    /'
    ok "Gọi được API ① từ server này"
    rm -f "$RA"
else
    echo "    Lỗi:"
    grep -E "ElisError|Error|error" "$RA" | tail -2 | cut -c1-200 | sed 's/^/      /'
    rm -f "$RA"
    loi "Không gọi được API ① từ server này.
           403  -> IP server chưa nằm trong allowlist của eLIS. Phải xin bên
                   eLIS thêm IP này vào, không có cách nào vòng qua.
           401  -> ELIS_API_KEY sai hoặc hết hạn.
           timeout/DNS -> server không ra được Internet, hoặc cần proxy."
fi

echo
echo "==================================================================="
printf 'Đạt: %d   |   Lưu ý: %d   |   LỖI: %d\n' "$DAT" "$CANH_BAO" "$LOI"
if [ "$LOI" -gt 0 ]; then
    echo "CÒN LỖI — sửa hết rồi hãy bật systemd. Bật khi còn lỗi thì job chỉ"
    echo "restart vô tận trong nền và bạn phải đi đọc journalctl mới biết."
    exit 1
fi
echo "Sẵn sàng. Bước tiếp: xem mục 17 trong README."
