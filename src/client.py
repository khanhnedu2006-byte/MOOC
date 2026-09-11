"""Client gọi 3 API của ELIS (client).

  ① getCert         -> danh sách chờ duyệt (WAITING), kèm thông tin đối chiếu
  ② download        -> tải file chứng chỉ (JSON, nội dung dạng base64)
  ③ ProcessStatus   -> gửi kết quả APPROVED/REJECTED

Việc nối 3 bước nằm ở run.py.
"""

import base64
import binascii
import json
import logging

import requests

from config import settings

logger = logging.getLogger(__name__)

# Header chung. API key gửi trong 'apikey' theo tài liệu.
def _headers(json_body: bool = True) -> dict:
    h = {"apikey": settings.elis_api_key, "Accept": "application/json"}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


class ElisError(Exception):
    """Lỗi khi gọi API ELIS."""


# ===== API ① — Lấy danh sách chờ duyệt =====

# Trần size của API: gửi size=5000 vẫn chỉ nhận về 1000. Dùng để chia trang
# khi kéo toàn bộ lịch sử.
MAX_PAGE_SIZE = 1000


def _get_cert(params: dict) -> list[dict]:
    """Gọi API ① getCert với bộ tham số tùy ý, trả về data[]."""
    url = f"{settings.elis_base_url}/api/v1/UserCourse/elearning/getCert"

    try:
        resp = requests.get(url, headers=_headers(json_body=False),
                            params=params, timeout=30)
    except requests.RequestException as e:
        raise ElisError(f"Lỗi kết nối getCert: {e}") from e

    if resp.status_code != 200:
        raise ElisError(f"getCert HTTP {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    if data.get("isError"):
        raise ElisError(f"getCert lỗi: {data.get('message')}")

    return data.get("data", [])


def get_by_status(status: str, page: int = 1, size: int = 100) -> list[dict]:
    """GET getCert?status=... — status nhận WAITING / APPROVED / REJECTED."""
    return _get_cert({"status": status, "page": page, "size": size})


def get_by_email(employee_email: str, page: int = 1,
                 size: int = MAX_PAGE_SIZE) -> list[dict]:
    """GET getCert?employeeEmail=... — MỌI bản ghi của một nhân viên.

    KHÔNG gửi kèm `status`: bản ghi trả về đã mang sẵn `submitStatus`. Tên
    tham số phải là `employeeEmail`; employeeId / employeeCode bị API bỏ qua
    trong IM LẶNG và trả về nguyên toàn bộ bản ghi kèm mã 200, nên người gọi
    PHẢI tự kiểm lại email từng bản ghi (run.py::completed_courses).
    """
    return _get_cert({"employeeEmail": employee_email,
                      "page": page, "size": size})


def get_pending_list(page: int = 1, size: int = 100) -> list[dict]:
    """GET getCert?status=WAITING — hàng đợi chờ duyệt; mỗi item chứa id,
    certificate_id, courseId, employeeId, employeeName, employeeEmail,
    courseName... (tài liệu mục 3.5).
    """
    return get_by_status("WAITING", page, size)


def get_all_by_status(status: str) -> list[dict]:
    """Kéo HẾT mọi trang của một trạng thái.

    Dừng khi gặp trang chưa đầy. Chặn số trang để lỗi phía API (luôn trả
    trang đầy) không thành vòng lặp vô tận.
    """
    out, page = [], 1
    while page <= 1000:
        items = get_by_status(status, page=page, size=MAX_PAGE_SIZE)
        out.extend(items)
        if len(items) < MAX_PAGE_SIZE:
            return out
        page += 1
    logger.warning("get_all_by_status(%s) chạm trần 1000 trang, dừng ở %d bản ghi.",
                   status, len(out))
    return out


# ===== API ② — Tải file chứng chỉ =====

def download_certificates(pairs: list[dict]) -> list[dict]:
    """POST download-certificates — tải file chứng chỉ.

    pairs: list dict {"UserCourseId": ..., "certificate_id": ...}, tối đa 20.
    Trả list dict {"userCourseId", "certificate_id", "anh_bytes", "ten_file"};
    item lỗi bị bỏ qua (run.py đã ghi log). KHÔNG hardcode tên trường base64:
    eLIS từng đổi hợp đồng API nên _find_base64_in_item() dò theo NỘI DUNG.
    """
    if not pairs:
        return []
    if len(pairs) > 20:
        raise ElisError("Tối đa 20 cặp mỗi request (giới hạn API ②).")

    url = f"{settings.elis_file_base_url}/api/v1/files/download-certificates"

    try:
        resp = requests.post(
            url,
            headers=_headers(),
            json=pairs,
            timeout=60,
        )
    except requests.RequestException as e:
        raise ElisError(f"Lỗi kết nối download: {e}") from e

    if resp.status_code != 200:
        raise ElisError(
            f"Download HTTP {resp.status_code}: {_escape_html(resp.text)}")

    try:
        body = resp.json()
    except ValueError:
        raise ElisError(
            f"Download: response không phải JSON. "
            f"Content-Type={resp.headers.get('Content-Type')!r}, "
            f"{len(resp.content)} byte, bắt đầu bằng "
            f"{_escape_html(resp.content, 60)}"
        ) from None

    body = _unwrap_loose_json(body)

    # Envelope báo lỗi thật (file_101 / file_102 / file_105).
    if isinstance(body, dict) and body.get("isError"):
        raise ElisError(f"Download lỗi: {_describe_error(body)}")

    return _attach_ids(_read_files_from_json(body), pairs)


def _attach_ids(verdict: list[dict], pairs: list[dict]) -> list[dict]:
    """Bù lại userCourseId cho những item eLIS trả về mà thiếu trường này.

    Response API ② không đảm bảo có userCourseId; thiếu nó thì run.py không
    nối được file với bản ghi ban đầu. Đối chiếu ngược từ request:
    certificate_id trước (không phụ thuộc thứ tự), rồi mới tới vị trí trong
    danh sách (chỉ khi số lượng khớp nhau).
    """
    by_cert = {
        str(c.get("certificate_id")): c.get("UserCourseId")
        for c in pairs if c.get("certificate_id")
    }

    for position, item in enumerate(verdict):
        if item.get("userCourseId"):
            continue

        cert = item.get("certificate_id")
        if cert and str(cert) in by_cert:
            item["userCourseId"] = by_cert[str(cert)]
            continue

        if len(verdict) == len(pairs):
            item["userCourseId"] = pairs[position].get("UserCourseId")
            logger.warning(
                "Item thứ %d không có userCourseId lẫn certificate_id — ghép "
                "theo vị trí. Các trường eLIS trả về: %s",
                position + 1, sorted(item.get("_cac_truong") or []))
            continue

        logger.error(
            "Không ghép được userCourseId cho item thứ %d và không suy ra "
            "được (nhận %d item cho %d yêu cầu). Các trường eLIS trả về: %s",
            position + 1, len(verdict), len(pairs),
            sorted(item.get("_cac_truong") or []))

    for item in verdict:
        item.pop("_cac_truong", None)
    return verdict


# --- Đọc định dạng JSON + base64 ---------------------------------------

# Chữ ký loại file, xác nhận base64 giải ra đúng là file.
_FILE_SIGNATURES = (
    b"%PDF",            # PDF
    b"\x89PNG",         # PNG
    b"\xff\xd8\xff",    # JPEG
    b"BM",              # BMP
    b"II*\x00",         # TIFF little-endian
    b"MM\x00*",         # TIFF big-endian
    b"PK\x03\x04",      # ZIP/DOCX (một số chứng chỉ nộp dưới dạng này)
)

# Tên trường có thể chứa id, thử theo thứ tự. So không phân biệt hoa/thường
# và bỏ dấu gạch dưới: "UserCourseId" = "usercourseid" = "user_course_id".
_UC_ID_KEYS = ("usercourseid", "id")
_CERT_ID_KEYS = ("certificateid", "certificateid", "certid")
_FILENAME_KEYS = ("originalfilename", "filename", "name", "entryname")


def _normalize_key(name: str) -> str:
    return name.replace("_", "").replace("-", "").lower()


def _get_by_key(item: dict, keys: tuple[str, ...]):
    """Lấy giá trị theo tên khóa, bỏ qua hoa/thường và dấu gạch dưới.

    eLIS trộn quy ước đặt tên: UserCourseId gửi lên, userCourseId nhận về.
    """
    mapping = {_normalize_key(k): v for k, v in item.items()}
    for key in keys:
        value = mapping.get(_normalize_key(key))
        if value:
            return value
    return None


def _decode_base64(value: str) -> bytes | None:
    """Giải base64 thành bytes, None nếu không phải file hợp lệ.

    Chấp nhận cả data URL ("data:application/pdf;base64,..."). Chỉ nhận khi
    byte đầu khớp một chữ ký file đã biết, tránh nhận nhầm chuỗi văn bản dài.
    """
    if not isinstance(value, str) or len(value) < 64:
        return None

    text_value = value.strip()
    if text_value.startswith("data:") and "," in text_value:
        text_value = text_value.split(",", 1)[1]

    try:
        data_bytes = base64.b64decode(text_value, validate=False)
    except (ValueError, binascii.Error):
        return None

    return data_bytes if data_bytes.startswith(_FILE_SIGNATURES) else None


def _find_base64_in_item(item: dict) -> bytes | None:
    """Tìm trường chứa nội dung file trong một item, KHÔNG dựa vào tên trường.

    Dò theo NỘI DUNG (giải base64 có đúng chữ ký file không) chắc hơn đoán tên
    fileBase64 / base64 / fileContent. Ưu tiên tên gợi ý base64/file/content.
    """
    suggestion, remaining = [], []
    for name, value in item.items():
        if not isinstance(value, str):
            continue
        t = _normalize_key(name)
        (suggestion if any(x in t for x in ("base64", "file", "content", "data"))
         else remaining).append(value)

    for value in suggestion + remaining:
        data_bytes = _decode_base64(value)
        if data_bytes is not None:
            return data_bytes
    return None


def _find_item_list(body) -> list[dict]:
    """Tìm mảng item trong response, dù nó nằm ở gốc hay trong 'data'/'items'."""
    if isinstance(body, list):
        return [x for x in body if isinstance(x, dict)]
    if isinstance(body, dict):
        for key in ("data", "items", "result", "files", "certificates"):
            value = _get_by_key(body, (key,))
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        # Response chỉ chứa MỘT file, không bọc mảng.
        if _find_base64_in_item(body) is not None:
            return [body]
    return []


def _read_files_from_json(body) -> list[dict]:
    """Rút danh sách file từ response JSON định dạng mới."""
    items = _find_item_list(body)
    if not items:
        raise ElisError(
            "Download: response JSON không chứa item nào đọc được. "
            f"Cấu trúc nhận được: {_describe_structure(body)}")

    verdict = []
    for item in items:
        # success=false -> eLIS không có file (soft-fail); run.py ghi log.
        if item.get("success") is False:
            continue

        content = _find_base64_in_item(item)
        if content is None:
            continue

        verdict.append({
            "userCourseId": _get_by_key(item, _UC_ID_KEYS),
            "certificate_id": _get_by_key(item, _CERT_ID_KEYS),
            "anh_bytes": content,
            "ten_file": _get_by_key(item, _FILENAME_KEYS),
            # Cho _attach_ids() ghi log khi phải đoán id; bị xoá ngay sau đó.
            "_cac_truong": list(item.keys()),
        })

    if not verdict:
        raise ElisError(
            "Download: không giải được base64 của file nào. "
            f"Cấu trúc nhận được: {_describe_structure(body)}")
    return verdict


def _describe_structure(value, after: int = 0) -> str:
    """Tóm tắt cấu trúc JSON (tên trường + kiểu) để báo lỗi cho dễ lần.

    In tên trường chứ KHÔNG in giá trị: base64 dài hàng trăm KB.
    """
    if after > 3:
        return "..."
    if isinstance(value, dict):
        part = [f"{k}: {_describe_structure(v, after + 1)}" for k, v in list(value.items())[:12]]
        return "{" + ", ".join(part) + "}"
    if isinstance(value, list):
        if not value:
            return "[]"
        return f"[{len(value)} phần tử: {_describe_structure(value[0], after + 1)}]"
    if isinstance(value, str):
        return f"str({len(value)} ký tự)"
    return type(value).__name__


def _escape_html(data_bytes, limit: int = 200) -> str:
    """Chuỗi AN TOÀN để in ra terminal.

    Dữ liệu nhị phân chứa ký tự điều khiển (\\r, \\b, ANSI) làm loạn màn hình;
    repr() bọc chúng thành dạng \\xNN nhìn được.
    """
    if isinstance(data_bytes, bytes):
        return repr(data_bytes[:limit])
    return repr((data_bytes if isinstance(data_bytes, str) else str(data_bytes))[:limit])


def _unwrap_loose_json(value, max_items: int = 4):
    """Bóc JSON bị đóng gói nhiều lớp chuỗi lồng nhau.

    ELIS trả lỗi API ② là chuỗi JSON, bên trong 'message' cũng là chuỗi JSON.
    """
    for _ in range(max_items):
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except ValueError:
            break
    return value


def _describe_error(body: dict) -> str:
    """Diễn giải envelope lỗi của ELIS thành câu đọc được.

    file_102 kèm danh sách 'items' cho biết CẶP NÀO không khớp, phải nêu ra.
    """
    message = _unwrap_loose_json(body.get("message"))

    if not isinstance(message, dict):
        return f"(code={body.get('code')}) {message}"

    error_code = message.get("errorCode") or body.get("code")
    explanation = {
        "file_101": "gửi quá 20 cặp trong một request",
        "file_102": "cặp UserCourseId + certificate_id KHÔNG khớp dữ liệu "
                    "trên eLIS, hoặc bản ghi không còn ở trạng thái WAITING",
        "file_105": "danh sách gửi lên bị rỗng",
    }.get(str(error_code), "")

    lines = [f"{error_code}" + (f" — {explanation}" if explanation else "")]

    items = message.get("items")
    if isinstance(items, list) and items:
        lines.append(f"  {len(items)} cặp bị từ chối:")
        for it in items[:5]:
            if not isinstance(it, dict):
                continue
            lines.append(
                f"    UserCourseId  : {it.get('userCourseId')}\n"
                f"    certificateId : {it.get('certificateId')}\n"
                f"    success={it.get('success')} errorCode={it.get('errorCode')}"
            )
        if len(items) > 5:
            lines.append(f"    ... và {len(items) - 5} cặp nữa")
    return "\n".join(lines)


def update_status(results: list[dict]) -> dict:
    """POST ProcessUserCourseStatus — gửi kết quả APPROVED/REJECTED.

    results: tối đa 500 dict đủ field id, certificate_id, status, courseId,
    employeeId, comment (bắt buộc), comment_cer (tùy chọn).
    """
    if not results:
        raise ElisError("Danh sách kết quả rỗng.")
    if len(results) > 500:
        raise ElisError("Tối đa 500 bản ghi mỗi request (giới hạn API ③).")

    url = f"{settings.elis_base_url}/api/v1/UserCourse/ProcessUserCourseStatus"

    try:
        resp = requests.post(url, headers=_headers(), json=results, timeout=60)
    except requests.RequestException as e:
        raise ElisError(f"Lỗi kết nối ProcessStatus: {e}") from e

    if resp.status_code != 200:
        raise ElisError(f"ProcessStatus HTTP {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    if data.get("isError"):
        raise ElisError(f"ProcessStatus lỗi: {data.get('message')}")

    return data.get("data", {})
