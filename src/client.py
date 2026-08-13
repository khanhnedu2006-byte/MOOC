"""Client gọi 3 API partner của ELIS (client).

Theo tài liệu "Partner Integration Guide — AI Certificate Scan (3 APIs —
Full Spec)" v1.1 (2026-08-03):

    ① GET  /api/v1/UserCourse/elearning/getCert       — danh sách chờ duyệt
    ② POST /api/v1/files/download-certificates-zip    — tải ZIP (tối đa 20 cặp)
    ③ POST /api/v1/UserCourse/ProcessUserCourseStatus — nộp kết quả (tối đa 500)

Module này CHỈ lo việc nói chuyện với ELIS: gửi request, đọc response, dịch
lỗi. Không chứa logic nghiệp vụ (việc quyết định APPROVED/REJECTED nằm ở
pipeline.py, việc điều phối nằm ở run.py).

Hai điểm quan trọng khi đọc response của ELIS:

1. LUÔN đọc field `isError` trong body, không chỉ nhìn HTTP status. Một số
   lỗi nghiệp vụ (vd "vượt quá 500 bản ghi") trả về HTTP 200 nhưng
   isError=true.

2. API ② có HAI loại lỗi khác hẳn nhau, không được xử lý giống nhau:
   - fail-fast (file_101 / file_102 / file_105): cả request bị từ chối,
     KHÔNG có ZIP nào. Hàm ném ElisApiError.
   - soft-fail (file_103 / file_104): VẪN có ZIP, chỉ riêng file đó lỗi,
     đánh dấu success=false trong manifest.json. Hàm KHÔNG ném lỗi — nơi
     giải nén (run.py) tự xử lý từng entry.
"""

import io
import json
import logging
import zipfile

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from config import settings

logger = logging.getLogger(__name__)

# Giới hạn batch theo mục 7 "Giới hạn batch" của tài liệu.
MAX_SIZE_GET_CERT = 1000        # size tối đa mỗi trang getCert
MAX_ITEMS_DOWNLOAD_ZIP = 20     # số cặp tối đa mỗi request download zip
MAX_DTO_PROCESS_STATUS = 500    # số dto tối đa mỗi request nộp kết quả

TIMEOUT_NGAN = 30    # giây, cho request JSON thường
TIMEOUT_DAI = 120    # giây, cho tải ZIP (có thể nặng)


class ElisApiError(Exception):
    """Lỗi request-level: không lấy được dữ liệu hợp lệ từ ELIS.

    Dùng cho các trường hợp fail-fast. KHÔNG dùng cho lỗi từng item
    (item lỗi nằm trong manifest.json hoặc failList, không phải exception).
    """


def _co_phai_zip(noi_dung: bytes) -> bool:
    """Nội dung này có phải file ZIP không.

    KHÔNG dựa vào Content-Type (gateway hay khai sai), cũng không chỉ dựa
    vào magic bytes "PK" ở đầu — một số server chèn thêm byte ở đầu stream,
    khi đó "PK" không nằm ở offset 0 nữa.

    Cách chắc nhất: bảo thư viện zipfile thử mở. ZIP lưu bảng mục lục
    (central directory) ở CUỐI file, nên zipfile vẫn đọc được kể cả khi
    đầu file có rác. Mở được = đúng là ZIP.
    """
    if len(noi_dung) < 22:  # nhỏ hơn kích thước tối thiểu của một ZIP rỗng
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(noi_dung)) as zf:
            zf.namelist()
        return True
    except (zipfile.BadZipFile, OSError, EOFError):
        return False


def _giai_ma_zip_boc_json(chuoi: str) -> bytes | None:
    """Khôi phục file ZIP bị ELIS bọc thành chuỗi JSON. None nếu không phải.

    ELIS trả về API ② với Content-Type: application/json, và thân là MỘT
    CHUỖI JSON chứa toàn bộ byte của file ZIP:

        "PK\\u0003\\u0004\\u0014\\u0000..."

    Nguyên nhân: phía server đem mảng byte[] serialize qua JSON. Bộ
    serialize coi mỗi byte là một ký tự (latin-1: byte 0x50 -> ký tự U+0050),
    escape các ký tự điều khiển thành \\uXXXX, rồi đóng gói thành chuỗi JSON.

    Giải mã ngược: encode chuỗi trở lại latin-1, mỗi ký tự U+0000..U+00FF
    thành đúng một byte 0x00..0xFF như ban đầu. Không mất mát dữ liệu.

    ĐÂY LÀ CÁCH ĐI VÒNG cho một lỗi phía ELIS — đúng ra API phải trả
    Content-Type: application/zip với thân nhị phân thuần như tài liệu mô
    tả (mục 4.3). Nên báo lại đội eLIS; khi nào họ sửa thì nhánh này tự
    không dùng tới nữa, vì hàm đã thử đọc ZIP nhị phân trước rồi.
    """
    if not chuoi.startswith("PK"):
        return None
    try:
        # latin-1: ánh xạ 1-1 giữa code point 0..255 và byte 0x00..0xFF.
        du_lieu = chuoi.encode("latin-1")
    except UnicodeEncodeError:
        # Có ký tự > U+00FF -> không phải kiểu đóng gói này.
        return None
    return du_lieu if _co_phai_zip(du_lieu) else None


def _an_toan_de_in(du_lieu, gioi_han: int = 300) -> str:
    """Biến dữ liệu bất kỳ thành chuỗi AN TOÀN để in ra terminal.

    Dữ liệu nhị phân in thẳng ra console sẽ chứa ký tự điều khiển (\\r, \\b,
    mã ANSI...) làm loạn màn hình, đè mất chính thông báo lỗi mình đang cần
    đọc. repr() bọc chúng lại thành dạng \\xNN nhìn được.
    """
    if isinstance(du_lieu, bytes):
        return repr(du_lieu[:gioi_han])
    chuoi = du_lieu if isinstance(du_lieu, str) else str(du_lieu)
    # repr() để ký tự điều khiển hiện dạng escape thay vì tác động lên terminal.
    return repr(chuoi[:gioi_han])


def _headers(api_key: str) -> dict:
    """Header chuẩn cho mọi request. Tên header lấy từ config (thường 'apikey')."""
    return {
        settings.api_key_header: api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _kiem_tra_loi(body, ngu_canh: str) -> None:
    """Ném ElisApiError nếu body báo isError=true.

    Không giả định body luôn là dict: có lúc ELIS trả về JSON dạng chuỗi
    hoặc mảng. Gọi thẳng .get() lên chúng sẽ ném AttributeError khó hiểu,
    che mất thông báo lỗi thật.
    """
    if isinstance(body, dict):
        if body.get("isError"):
            raise ElisApiError(
                f"[{ngu_canh}] ELIS báo lỗi (code={body.get('code')}): {body.get('message')}"
            )
        return

    # Không phải dict -> in ra để còn lần được, nhưng phải qua _an_toan_de_in.
    raise ElisApiError(
        f"[{ngu_canh}] ELIS trả về {type(body).__name__} thay vì object JSON: "
        f"{_an_toan_de_in(body)}"
    )


def chia_batch(danh_sach: list, kich_thuoc: int) -> list[list]:
    """Chia list thành các batch con, mỗi batch tối đa kich_thuoc phần tử."""
    return [danh_sach[i:i + kich_thuoc] for i in range(0, len(danh_sach), kich_thuoc)]


# =====================================================================
# API ① — Lấy danh sách chờ duyệt
# =====================================================================

@retry(
    stop=stop_after_attempt(settings.so_lan_retry),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
def lay_mot_trang_cho_duyet(page: int = 1, size: int = 100, **loc_them) -> dict:
    """GET getCert?status=WAITING — trả nguyên response envelope.

    loc_them: các filter tùy chọn của tài liệu mục 3.2 — startSubmitDate,
    endSubmitDate, courseCode, courseName, employeeEmail.
    """
    size = min(size, MAX_SIZE_GET_CERT)
    params = {"status": "WAITING", "page": page, "size": size}
    params.update({k: v for k, v in loc_them.items() if v})

    url = settings.url_api("/api/v1/UserCourse/elearning/getCert")
    resp = requests.get(
        url, headers=_headers(settings.api_key), params=params, timeout=TIMEOUT_NGAN
    )
    resp.raise_for_status()
    body = resp.json()
    _kiem_tra_loi(body, "getCert")
    return body


def lay_toan_bo_cho_duyet(size: int = 100, **loc_them) -> list[dict]:
    """Lặp hết các trang, trả về TOÀN BỘ item đang WAITING.

    Không dựa vào totalPage vì tài liệu ghi rõ field này hiện luôn = 0.
    Dừng khi trang trả về rỗng, hoặc đã gom đủ totalRecords.
    """
    ket_qua: list[dict] = []
    page = 1
    while True:
        body = lay_mot_trang_cho_duyet(page=page, size=size, **loc_them)
        items = body.get("data") or []
        ket_qua.extend(items)

        tong = body.get("totalRecords") or 0
        if not items or len(ket_qua) >= tong:
            break
        page += 1

    logger.info("getCert: lấy được %d chứng chỉ WAITING.", len(ket_qua))
    return ket_qua


# =====================================================================
# API ② — Download ZIP chứng chỉ
# =====================================================================

@retry(
    stop=stop_after_attempt(settings.so_lan_retry),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
def tai_zip_chung_chi(danh_sach_cap: list[dict]) -> bytes:
    """POST download-certificates-zip — trả về nội dung file ZIP (bytes).

    danh_sach_cap: [{"UserCourseId": ..., "certificate_id": ...}, ...]
    Tối đa 20 phần tử — hàm KHÔNG tự chia batch, gọi chia_batch() trước.

    Tên field phải viết ĐÚNG hoa/thường như trên ("UserCourseId" chữ U hoa,
    "certificate_id" chữ thường có gạch dưới) — tài liệu ghi rõ là
    case-sensitive.
    """
    if not danh_sach_cap:
        raise ValueError("tai_zip_chung_chi: danh sách rỗng.")
    if len(danh_sach_cap) > MAX_ITEMS_DOWNLOAD_ZIP:
        raise ValueError(
            f"tai_zip_chung_chi: {len(danh_sach_cap)} cặp, vượt giới hạn "
            f"{MAX_ITEMS_DOWNLOAD_ZIP}/request — phải chia batch trước."
        )

    url = settings.url_file("/api/v1/files/download-certificates-zip")
    # khoa_file = kong_api_key nếu có, ngược lại dùng chung api_key. Trên UAT
    # hiện tại hai service chung một key nên chỉ cần dán một lần vào .env.
    headers = _headers(settings.khoa_file)
    headers["Accept"] = "application/zip, application/json"

    resp = requests.post(url, headers=headers, json=danh_sach_cap, timeout=TIMEOUT_DAI)

    # Thử mở như ZIP trước tiên — chắc chắn hơn mọi cách đoán qua header.
    if _co_phai_zip(resp.content):
        return resp.content

    # Không phải ZIP nhị phân -> đọc JSON.
    resp.raise_for_status()
    try:
        body = resp.json()
    except ValueError:
        raise ElisApiError(
            f"[download-zip] HTTP {resp.status_code}, response không phải ZIP "
            f"cũng không phải JSON. Content-Type="
            f"{resp.headers.get('Content-Type')!r}, "
            f"{len(resp.content)} byte, bắt đầu bằng "
            f"{_an_toan_de_in(resp.content, 80)}"
        ) from None

    # ELIS hiện bọc ZIP thành chuỗi JSON — gỡ ra nếu đúng kiểu đó.
    if isinstance(body, str):
        zip_khoi_phuc = _giai_ma_zip_boc_json(body)
        if zip_khoi_phuc is not None:
            logger.debug(
                "download-zip: ZIP bị bọc trong chuỗi JSON, đã giải mã "
                "(%d ký tự -> %d byte).", len(body), len(zip_khoi_phuc)
            )
            return zip_khoi_phuc

    # message của file_102 là một JSON string lồng — bóc ra cho dễ đọc.
    if isinstance(body, dict) and isinstance(body.get("message"), str):
        try:
            body = {**body, "message": json.loads(body["message"])}
        except (ValueError, TypeError):
            pass

    _kiem_tra_loi(body, "download-zip")
    raise ElisApiError(
        f"[download-zip] HTTP {resp.status_code}, không có ZIP trong response: "
        f"{_an_toan_de_in(body)}"
    )


# =====================================================================
# API ③ — Cập nhật trạng thái duyệt
# =====================================================================

@retry(
    stop=stop_after_attempt(settings.so_lan_retry),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
def cap_nhat_trang_thai(danh_sach_dto: list[dict]) -> dict:
    """POST ProcessUserCourseStatus — trả {"successList": [...], "failList": [...]}.

    Mỗi dto gồm: id, certificate_id, status (APPROVED/REJECTED), courseId,
    employeeId, comment (bắt buộc), comment_cer (tùy chọn).
    Tối đa 500 dto — hàm KHÔNG tự chia batch.

    Partial success: một item lỗi KHÔNG làm rollback các item khác, và cũng
    không phải lỗi hệ thống — nên hàm trả về cả hai list cho nơi gọi tự xử
    lý, không ném exception. Chỉ ném khi CẢ request bị từ chối.
    """
    if not danh_sach_dto:
        raise ValueError("cap_nhat_trang_thai: danh sách rỗng.")
    if len(danh_sach_dto) > MAX_DTO_PROCESS_STATUS:
        raise ValueError(
            f"cap_nhat_trang_thai: {len(danh_sach_dto)} dto, vượt giới hạn "
            f"{MAX_DTO_PROCESS_STATUS}/request — phải chia batch trước."
        )

    url = settings.url_api("/api/v1/UserCourse/ProcessUserCourseStatus")
    resp = requests.post(
        url, headers=_headers(settings.api_key), json=danh_sach_dto, timeout=TIMEOUT_DAI
    )
    resp.raise_for_status()
    body = resp.json()
    _kiem_tra_loi(body, "ProcessUserCourseStatus")  # body rỗng / vượt 500

    data = body.get("data") or {}
    return {
        "successList": data.get("successList") or [],
        "failList": data.get("failList") or [],
    }
