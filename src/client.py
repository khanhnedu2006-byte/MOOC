"""Client gọi 3 API của ELIS (client).

Luồng:
  ① getCert          -> lấy danh sách chứng chỉ chờ duyệt (WAITING)
  ② download          -> tải file chứng chỉ (JSON, nội dung dạng base64)
  ③ ProcessStatus    -> gửi kết quả APPROVED/REJECTED

Mỗi API là một hàm riêng, test được độc lập. Việc nối 3 bước + chạy pipeline
nằm ở run.py.

Thông tin để đối chiếu (tên NV, khóa học, mã NV) lấy từ API ①.
Nội dung file chứng chỉ lấy từ API ② dưới dạng base64.
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


# ===== API ② — Tải file chứng chỉ =====

def tai_chung_chi(cac_cap: list[dict]) -> list[dict]:
    """POST download-certificates — tải file chứng chỉ.

    cac_cap: list dict {"UserCourseId": ..., "certificate_id": ...}, tối đa 20.

    Trả về list dict: {"userCourseId", "certificate_id", "anh_bytes", "ten_file"}
    cho các item lấy được file. Item lỗi bị bỏ qua (đã ghi log ở run.py).

    Response là JSON, mỗi item có một trường chứa nội dung file dạng base64.

    KHÔNG hardcode tên trường base64: tài liệu chưa mô tả định dạng này, và
    eLIS đã đổi hợp đồng API hai lần rồi. _tim_base64_trong_item() dò theo
    NỘI DUNG — giải base64 ra rồi kiểm chữ ký file — nên đổi tên trường cũng
    không vỡ.
    """
    if not cac_cap:
        return []
    if len(cac_cap) > 20:
        raise ElisError("Tối đa 20 cặp mỗi request (giới hạn API ②).")

    url = f"{settings.elis_file_base_url}/api/v1/files/download-certificates"

    try:
        resp = requests.post(
            url,
            headers=_headers(),
            json=cac_cap,
            timeout=60,
        )
    except requests.RequestException as e:
        raise ElisError(f"Lỗi kết nối download: {e}") from e

    if resp.status_code != 200:
        raise ElisError(
            f"Download HTTP {resp.status_code}: {_an_toan_de_in(resp.text)}")

    try:
        body = resp.json()
    except ValueError:
        raise ElisError(
            f"Download: response không phải JSON. "
            f"Content-Type={resp.headers.get('Content-Type')!r}, "
            f"{len(resp.content)} byte, bắt đầu bằng "
            f"{_an_toan_de_in(resp.content, 60)}"
        ) from None

    body = _boc_json_long(body)

    # Envelope báo lỗi thật (file_101 / file_102 / file_105).
    if isinstance(body, dict) and body.get("isError"):
        raise ElisError(f"Download lỗi: {_mo_ta_loi(body)}")

    return _ghep_id(_doc_file_tu_json(body), cac_cap)


def _ghep_id(ket_qua: list[dict], cac_cap: list[dict]) -> list[dict]:
    """Bù lại userCourseId cho những item eLIS trả về mà thiếu trường này.

    Vì sao cần: response API ② không đảm bảo có userCourseId. Thiếu nó thì
    run.py không nối được file với bản ghi ban đầu, và chứng chỉ bị bỏ qua
    dù đã tải về thành công — tốn công tải mà không xử lý được.

    Đối chiếu ngược từ request theo thứ tự ưu tiên:
      1. certificate_id — chắc chắn nhất, không phụ thuộc thứ tự
      2. Vị trí trong danh sách — chỉ dùng khi số lượng khớp nhau, vì lúc
         đó gần như chắc chắn eLIS trả về theo đúng thứ tự nhận vào

    Khi phải đoán, ghi log tên trường thật của item để lần sau biết đường.
    """
    theo_cert = {
        str(c.get("certificate_id")): c.get("UserCourseId")
        for c in cac_cap if c.get("certificate_id")
    }

    for vi_tri, item in enumerate(ket_qua):
        if item.get("userCourseId"):
            continue

        cert = item.get("certificate_id")
        if cert and str(cert) in theo_cert:
            item["userCourseId"] = theo_cert[str(cert)]
            continue

        if len(ket_qua) == len(cac_cap):
            item["userCourseId"] = cac_cap[vi_tri].get("UserCourseId")
            logger.warning(
                "Item thứ %d không có userCourseId lẫn certificate_id — ghép "
                "theo vị trí. Các trường eLIS trả về: %s",
                vi_tri + 1, sorted(item.get("_cac_truong") or []))
            continue

        logger.error(
            "Không ghép được userCourseId cho item thứ %d và không suy ra "
            "được (nhận %d item cho %d yêu cầu). Các trường eLIS trả về: %s",
            vi_tri + 1, len(ket_qua), len(cac_cap),
            sorted(item.get("_cac_truong") or []))

    for item in ket_qua:
        item.pop("_cac_truong", None)
    return ket_qua


# --- Đọc định dạng JSON + base64 ---------------------------------------

# Chữ ký nhận dạng loại file, dùng để xác nhận chuỗi base64 giải ra đúng là
# file chứ không phải chuỗi văn bản dài ngẫu nhiên nào đó.
_CHU_KY_FILE = (
    b"%PDF",            # PDF
    b"\x89PNG",         # PNG
    b"\xff\xd8\xff",    # JPEG
    b"BM",              # BMP
    b"II*\x00",         # TIFF little-endian
    b"MM\x00*",         # TIFF big-endian
    b"PK\x03\x04",      # ZIP/DOCX (một số chứng chỉ nộp dưới dạng này)
)

# Tên trường có thể chứa id, thử theo thứ tự. Không phân biệt hoa/thường và
# bỏ qua dấu gạch dưới khi so, nên "UserCourseId" = "usercourseid" =
# "user_course_id".
_KHOA_UC_ID = ("usercourseid", "id")
_KHOA_CERT_ID = ("certificateid", "certificateid", "certid")
_KHOA_TEN_FILE = ("originalfilename", "filename", "name", "entryname")


def _chuan_khoa(ten: str) -> str:
    return ten.replace("_", "").replace("-", "").lower()


def _lay_theo_khoa(item: dict, cac_khoa: tuple[str, ...]):
    """Lấy giá trị theo tên khóa, bỏ qua hoa/thường và dấu gạch dưới.

    eLIS trộn lẫn nhiều quy ước đặt tên giữa các API (UserCourseId khi gửi
    lên, userCourseId khi nhận về, certificate_id ở chỗ khác), nên tra cứu
    linh hoạt sẽ đỡ vỡ khi họ đổi tiếp.
    """
    ban_do = {_chuan_khoa(k): v for k, v in item.items()}
    for khoa in cac_khoa:
        gia_tri = ban_do.get(_chuan_khoa(khoa))
        if gia_tri:
            return gia_tri
    return None


def _giai_base64(gia_tri: str) -> bytes | None:
    """Giải base64 thành bytes, None nếu không phải file hợp lệ.

    Chấp nhận cả dạng data URL ("data:application/pdf;base64,JVBER...") vì
    một số backend trả kèm tiền tố đó.

    Chỉ nhận kết quả khi byte đầu khớp một chữ ký file đã biết — tránh nhận
    nhầm một chuỗi văn bản dài (vd tên khóa học) thành nội dung file.
    """
    if not isinstance(gia_tri, str) or len(gia_tri) < 64:
        return None

    chuoi = gia_tri.strip()
    if chuoi.startswith("data:") and "," in chuoi:
        chuoi = chuoi.split(",", 1)[1]

    try:
        du_lieu = base64.b64decode(chuoi, validate=False)
    except (ValueError, binascii.Error):
        return None

    return du_lieu if du_lieu.startswith(_CHU_KY_FILE) else None


def _tim_base64_trong_item(item: dict) -> bytes | None:
    """Tìm trường chứa nội dung file trong một item, KHÔNG dựa vào tên trường.

    Vì sao không hardcode tên: tài liệu chưa mô tả định dạng mới, và eLIS đã
    đổi hợp đồng API hai lần. Dò theo NỘI DUNG (giải base64 ra có đúng chữ ký
    file không) chắc chắn hơn là đoán tên trường là fileBase64 hay base64 hay
    fileContent.

    Ưu tiên trường có tên gợi ý base64/file/content trước, để không phải giải
    mã thử mọi trường khi item có nhiều chuỗi dài.
    """
    goi_y, con_lai = [], []
    for ten, gia_tri in item.items():
        if not isinstance(gia_tri, str):
            continue
        t = _chuan_khoa(ten)
        (goi_y if any(x in t for x in ("base64", "file", "content", "data"))
         else con_lai).append(gia_tri)

    for gia_tri in goi_y + con_lai:
        du_lieu = _giai_base64(gia_tri)
        if du_lieu is not None:
            return du_lieu
    return None


def _tim_danh_sach_item(body) -> list[dict]:
    """Tìm mảng item trong response, dù nó nằm ở gốc hay trong 'data'/'items'."""
    if isinstance(body, list):
        return [x for x in body if isinstance(x, dict)]
    if isinstance(body, dict):
        for khoa in ("data", "items", "result", "files", "certificates"):
            gia_tri = _lay_theo_khoa(body, (khoa,))
            if isinstance(gia_tri, list):
                return [x for x in gia_tri if isinstance(x, dict)]
        # Response chỉ chứa MỘT file, không bọc mảng.
        if _tim_base64_trong_item(body) is not None:
            return [body]
    return []


def _doc_file_tu_json(body) -> list[dict]:
    """Rút danh sách file từ response JSON định dạng mới."""
    items = _tim_danh_sach_item(body)
    if not items:
        raise ElisError(
            "Download: response JSON không chứa item nào đọc được. "
            f"Cấu trúc nhận được: {_mo_ta_cau_truc(body)}")

    ket_qua = []
    for item in items:
        # success=false -> eLIS không có file (soft-fail). Bỏ qua, run.py đã
        # ghi log những item gửi lên mà không nhận về.
        if item.get("success") is False:
            continue

        noi_dung = _tim_base64_trong_item(item)
        if noi_dung is None:
            continue

        ket_qua.append({
            "userCourseId": _lay_theo_khoa(item, _KHOA_UC_ID),
            "certificate_id": _lay_theo_khoa(item, _KHOA_CERT_ID),
            "anh_bytes": noi_dung,
            "ten_file": _lay_theo_khoa(item, _KHOA_TEN_FILE),
            # Giữ tạm tên các trường để _ghep_id() ghi log khi phải đoán id.
            # Bị xoá ngay sau đó, không lọt ra ngoài client.py.
            "_cac_truong": list(item.keys()),
        })

    if not ket_qua:
        raise ElisError(
            "Download: không giải được base64 của file nào. "
            f"Cấu trúc nhận được: {_mo_ta_cau_truc(body)}")
    return ket_qua


def _mo_ta_cau_truc(gia_tri, sau: int = 0) -> str:
    """Tóm tắt cấu trúc JSON (tên trường + kiểu) để báo lỗi cho dễ lần.

    In tên trường chứ KHÔNG in giá trị: giá trị base64 dài hàng trăm KB, in
    ra chỉ làm ngập log mà không giúp gì.
    """
    if sau > 3:
        return "..."
    if isinstance(gia_tri, dict):
        phan = [f"{k}: {_mo_ta_cau_truc(v, sau + 1)}" for k, v in list(gia_tri.items())[:12]]
        return "{" + ", ".join(phan) + "}"
    if isinstance(gia_tri, list):
        if not gia_tri:
            return "[]"
        return f"[{len(gia_tri)} phần tử: {_mo_ta_cau_truc(gia_tri[0], sau + 1)}]"
    if isinstance(gia_tri, str):
        return f"str({len(gia_tri)} ký tự)"
    return type(gia_tri).__name__


def _an_toan_de_in(du_lieu, gioi_han: int = 200) -> str:
    """Chuỗi AN TOÀN để in ra terminal.

    Dữ liệu nhị phân in thẳng ra console chứa ký tự điều khiển (\\r, \\b, mã
    ANSI) làm loạn màn hình, đè mất chính thông báo lỗi đang cần đọc.
    repr() bọc chúng thành dạng \\xNN nhìn được.
    """
    if isinstance(du_lieu, bytes):
        return repr(du_lieu[:gioi_han])
    return repr((du_lieu if isinstance(du_lieu, str) else str(du_lieu))[:gioi_han])


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