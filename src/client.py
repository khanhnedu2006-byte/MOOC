"""Client gọi 3 API của ELIS (client).

Luồng:
  ① getCert          -> lấy danh sách chứng chỉ chờ duyệt (WAITING)
  ② download ZIP     -> tải file chứng chỉ theo id, giải nén lấy ảnh/PDF
  ③ ProcessStatus    -> gửi kết quả APPROVED/REJECTED

Mỗi API là một hàm riêng, test được độc lập. Việc nối 3 bước + chạy pipeline
nằm ở run.py.

Thông tin để đối chiếu (tên NV, khóa học, mã NV) lấy từ API ①.
Ảnh chứng chỉ lấy từ API ②.
"""

import io
import json
import zipfile

import requests

from config import settings

# Header chung. API key gửi trong 'apikey' theo tài liệu.
def _headers(json_body: bool = True) -> dict:
    h = {"apikey": settings.elis_api_key, "Accept": "application/json"}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


class ElisError(Exception):
    """Lỗi khi gọi API ELIS."""


# ===== API ① — Lấy danh sách chờ duyệt =====

def lay_danh_sach_cho_duyet(page: int = 1, size: int = 100) -> list[dict]:
    """GET getCert?status=WAITING — trả về list item chờ duyệt.

    Mỗi item chứa: id, certificate_id, courseId, employeeId, employeeName,
    courseName... (xem tài liệu mục 3.5).
    """
    url = f"{settings.elis_base_url}/api/v1/UserCourse/elearning/getCert"
    params = {"status": "WAITING", "page": page, "size": size}

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


# ===== API ② — Download ZIP chứng chỉ =====

def tai_zip_chung_chi(cac_cap: list[dict]) -> list[dict]:
    """POST download-certificates-zip — tải ZIP, giải nén.

    cac_cap: list dict {"UserCourseId": ..., "certificate_id": ...}, tối đa 20.

    Trả về list dict: {"userCourseId", "certificate_id", "anh_bytes", "ten_file"}
    cho các item scan được (success=true trong manifest). Item lỗi bị bỏ qua.
    """
    if not cac_cap:
        return []
    if len(cac_cap) > 20:
        raise ElisError("Tối đa 20 cặp mỗi request (giới hạn API ②).")

    url = f"{settings.elis_file_base_url}/api/v1/files/download-certificates-zip"

    try:
        resp = requests.post(
            url,
            headers={**_headers(), "Accept": "application/zip, application/json"},
            json=cac_cap,
            timeout=60,
        )
    except requests.RequestException as e:
        raise ElisError(f"Lỗi kết nối download ZIP: {e}") from e

    zip_bytes = _lay_zip_tu_response(resp)
    return _giai_nen_zip(zip_bytes)


def _co_phai_zip(noi_dung: bytes) -> bool:
    """Nội dung này có phải file ZIP không.

    Thử mở bằng zipfile thay vì kiểm tra 2 byte "PK" ở đầu: ZIP lưu bảng
    mục lục ở CUỐI file nên vẫn mở được kể cả khi đầu stream có byte lạ.
    """
    if len(noi_dung) < 22:  # nhỏ hơn kích thước tối thiểu của ZIP rỗng
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(noi_dung)) as zf:
            zf.namelist()
        return True
    except (zipfile.BadZipFile, OSError, EOFError):
        return False


def _giai_ma_zip_boc_json(than: bytes) -> bytes | None:
    """Khôi phục ZIP bị ELIS bọc thành chuỗi JSON. None nếu không phải kiểu đó.

    ELIS hiện trả API ② với Content-Type: application/json và thân là MỘT
    CHUỖI JSON chứa toàn bộ byte của file ZIP:

        "PK\\u0003\\u0004\\u0014\\u0000..."

    Nguyên nhân: phía server đem mảng byte[] serialize qua JSON. Bộ serialize
    coi mỗi byte là một ký tự, escape ký tự điều khiển thành \\uXXXX rồi
    đóng gói thành chuỗi.

    PHẢI nhận BYTE THÔ (resp.content), TUYỆT ĐỐI KHÔNG dùng resp.json().
    Lý do: resp.json() giải mã thân theo UTF-8 với errors="replace". Thân
    response chứa byte 0x80-0xFF ghi thô, không phải UTF-8 hợp lệ, nên hàng
    nghìn byte bị thay bằng ký tự thay thế U+FFFD. Dữ liệu mất TRƯỚC KHI
    code kịp xử lý, và không cách nào khôi phục. Đo thực tế trên một file
    22KB: 6.408 byte bị nuốt.

    Cách đúng: decode latin-1 (ánh xạ 1-1 byte <-> ký tự, không bao giờ mất),
    parse JSON, rồi encode lại latin-1 để lấy đúng byte ban đầu.

    Thử vài kiểu đóng gói vì không biết server dùng bộ serialize nào:
      1. Byte ghi thô hoặc escape hết về ASCII -> latin-1 một lần là ra.
      2. Ký tự non-ASCII ghi dưới dạng UTF-8   -> cần bóc thêm một lớp.

    ĐÂY LÀ CÁCH ĐI VÒNG cho lỗi phía ELIS — đúng ra API phải trả
    Content-Type: application/zip với thân nhị phân (tài liệu mục 4.3).
    Khi họ sửa, hàm này tự động không được dùng tới nữa vì
    _lay_zip_tu_response() thử đọc ZIP nhị phân TRƯỚC.
    """
    if not than[:3] in (b'"PK', b"'PK") and not than.lstrip()[:3] == b'"PK':
        return None

    try:
        # latin-1: mỗi byte thành đúng một ký tự, không bao giờ lỗi.
        chuoi = json.loads(than.decode("latin-1"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(chuoi, str):
        return None

    try:
        ung_vien = chuoi.encode("latin-1")
    except UnicodeEncodeError:
        return None

    # Kiểu 1: ra ngay.
    if _co_phai_zip(ung_vien):
        return ung_vien

    # Kiểu 2: server ghi non-ASCII dưới dạng UTF-8 -> bóc thêm một lớp.
    try:
        ung_vien_2 = ung_vien.decode("utf-8").encode("latin-1")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return None
    return ung_vien_2 if _co_phai_zip(ung_vien_2) else None


def _an_toan_de_in(du_lieu, gioi_han: int = 200) -> str:
    """Chuỗi AN TOÀN để in ra terminal.

    Dữ liệu nhị phân in thẳng ra console chứa ký tự điều khiển (\\r, \\b, mã
    ANSI) làm loạn màn hình, đè mất chính thông báo lỗi đang cần đọc.
    repr() bọc chúng thành dạng \\xNN nhìn được.
    """
    if isinstance(du_lieu, bytes):
        return repr(du_lieu[:gioi_han])
    return repr((du_lieu if isinstance(du_lieu, str) else str(du_lieu))[:gioi_han])


def _lay_zip_tu_response(resp) -> bytes:
    """Rút file ZIP ra khỏi response. Ném ElisError nếu không có ZIP.

    Thử theo thứ tự, KHÔNG tin Content-Type (gateway hay khai sai):

      1. Thân là ZIP nhị phân       -> dùng luôn (đường chuẩn theo tài liệu)
      2. Thân là chuỗi JSON "PK..." -> gỡ ra (lỗi hiện tại của ELIS)
      3. Thân là object JSON        -> đúng là báo lỗi, đọc message
    """
    # 1. Đường chuẩn: thân là ZIP nhị phân.
    if _co_phai_zip(resp.content):
        return resp.content

    # 2. ZIP bị bọc thành chuỗi JSON.
    #    Đọc từ resp.content (BYTE THÔ) — xem giải thích trong
    #    _giai_ma_zip_boc_json về việc resp.json() làm mất dữ liệu.
    zip_bytes = _giai_ma_zip_boc_json(resp.content)
    if zip_bytes is not None:
        return zip_bytes

    if resp.status_code != 200:
        raise ElisError(
            f"Download ZIP HTTP {resp.status_code}: "
            f"{_an_toan_de_in(resp.text)}"
        )

    try:
        data = resp.json()
    except ValueError:
        raise ElisError(
            f"Download ZIP: response không phải ZIP cũng không phải JSON. "
            f"Content-Type={resp.headers.get('Content-Type')!r}, "
            f"{len(resp.content)} byte, bắt đầu bằng "
            f"{_an_toan_de_in(resp.content, 60)}"
        ) from None

    # Envelope lỗi có thể bị bọc thêm một hoặc vài lớp chuỗi JSON.
    data = _boc_json_long(data)

    # 3. Báo lỗi thật (file_101 / file_102 / file_105 — tài liệu mục 4.4).
    if isinstance(data, dict):
        raise ElisError(f"Download ZIP lỗi: {_mo_ta_loi(data)}")

    raise ElisError(
        f"Download ZIP: response lạ ({type(data).__name__}): "
        f"{_an_toan_de_in(data)}"
    )


def _boc_json_long(gia_tri, toi_da: int = 4):
    """Bóc JSON bị đóng gói nhiều lớp chuỗi lồng nhau.

    ELIS trả lỗi API ② dạng chuỗi JSON, mà bên trong chuỗi đó lại có field
    'message' cũng là chuỗi JSON nữa. Hàm này bóc dần cho tới khi ra được
    object thật, tối đa toi_da lớp để không lặp vô hạn.
    """
    for _ in range(toi_da):
        if not isinstance(gia_tri, str):
            break
        try:
            gia_tri = json.loads(gia_tri)
        except ValueError:
            break
    return gia_tri


def _mo_ta_loi(body: dict) -> str:
    """Diễn giải envelope lỗi của ELIS thành câu đọc được.

    file_102 kèm danh sách 'items' cho biết CẶP NÀO không khớp — thông tin
    quan trọng nhất để sửa, nên phải nêu ra thay vì cắt cụt.
    """
    message = _boc_json_long(body.get("message"))

    if not isinstance(message, dict):
        return f"(code={body.get('code')}) {message}"

    ma_loi = message.get("errorCode") or body.get("code")
    giai_thich = {
        "file_101": "gửi quá 20 cặp trong một request",
        "file_102": "cặp UserCourseId + certificate_id KHÔNG khớp dữ liệu "
                    "trên eLIS, hoặc bản ghi không còn ở trạng thái WAITING",
        "file_105": "danh sách gửi lên bị rỗng",
    }.get(str(ma_loi), "")

    dong = [f"{ma_loi}" + (f" — {giai_thich}" if giai_thich else "")]

    items = message.get("items")
    if isinstance(items, list) and items:
        dong.append(f"  {len(items)} cặp bị từ chối:")
        for it in items[:5]:
            if not isinstance(it, dict):
                continue
            dong.append(
                f"    UserCourseId  : {it.get('userCourseId')}\n"
                f"    certificateId : {it.get('certificateId')}\n"
                f"    success={it.get('success')} errorCode={it.get('errorCode')}"
            )
        if len(items) > 5:
            dong.append(f"    ... và {len(items) - 5} cặp nữa")
    return "\n".join(dong)


def _giai_nen_zip(zip_bytes: bytes) -> list[dict]:
    """Giải nén ZIP, đọc manifest.json, trả về ảnh cho item success=true."""
    ket_qua = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        # Đọc manifest để biết entry nào ứng với item nào.
        try:
            manifest_raw = zf.read("manifest.json").decode("utf-8")
            manifest = json.loads(manifest_raw)
        except (KeyError, json.JSONDecodeError) as e:
            raise ElisError(f"ZIP thiếu/lỗi manifest.json: {e}") from e

        for item in manifest:
            # Chỉ scan item có file thật (xem tài liệu 4.3).
            if not item.get("success"):
                continue
            entry = item.get("entryName", "")
            if not entry:
                continue
            try:
                anh_bytes = zf.read(entry)
            except KeyError:
                continue
            ket_qua.append({
                "userCourseId": item.get("userCourseId"),
                "certificate_id": item.get("certificateId"),
                "anh_bytes": anh_bytes,
                "ten_file": item.get("originalFileName"),
            })
    return ket_qua


# ===== API ③ — Cập nhật trạng thái duyệt =====

def cap_nhat_trang_thai(cac_ket_qua: list[dict]) -> dict:
    """POST ProcessUserCourseStatus — gửi kết quả APPROVED/REJECTED.

    cac_ket_qua: list dict, mỗi cái đủ field:
      id, certificate_id, status (APPROVED/REJECTED), courseId, employeeId,
      comment (bắt buộc), comment_cer (tùy chọn). Tối đa 500.

    Trả về dict data chứa successList / failList.
    """
    if not cac_ket_qua:
        raise ElisError("Danh sách kết quả rỗng.")
    if len(cac_ket_qua) > 500:
        raise ElisError("Tối đa 500 bản ghi mỗi request (giới hạn API ③).")

    url = f"{settings.elis_base_url}/api/v1/UserCourse/ProcessUserCourseStatus"

    try:
        resp = requests.post(url, headers=_headers(), json=cac_ket_qua, timeout=60)
    except requests.RequestException as e:
        raise ElisError(f"Lỗi kết nối ProcessStatus: {e}") from e

    if resp.status_code != 200:
        raise ElisError(f"ProcessStatus HTTP {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    if data.get("isError"):
        raise ElisError(f"ProcessStatus lỗi: {data.get('message')}")

    return data.get("data", {})