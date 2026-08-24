"""Kho lưu chứng chỉ thật (archive).

Giữ lại ảnh chứng chỉ + thông tin getCert ngay lúc job tải về, để sau này
chạy lại bộ đánh giá mà không cần eLIS.

VÌ SAO CẦN: sau khi job nộp APPROVED/REJECTED qua API ③, bản ghi rời trạng
thái WAITING nên vòng getCert sau không trả về nó nữa. Data thật đi qua hệ
thống đúng MỘT lần. Trước đây ảnh còn bị ghi ra file tạm rồi xóa ngay sau khi
đọc — chạy xong là mất sạch, trong DB chỉ còn CHỮ model đọc được chứ không
còn ẢNH. Mà đánh giá bước trích xuất thì bắt buộc phải có ảnh gốc.

TÊN FILE THEO NỘI CORRECT: {user_course_id}_{8 ký tự băm nội dung}.{đuôi}
user_course_id một mình KHÔNG đủ để phân biệt — trong dữ liệu UAT thực tế đã
thấy cùng một user_course_id gắn với nhiều chứng chỉ khác nhau. Băm nội dung
thì hai file khác nhau chắc chắn nằm ở hai tên khác nhau, còn cùng một file
tải lại lần nữa sẽ tự đè lên chính nó thay vì sinh bản sao.

MỌI LỖI Ở ĐÂY ĐỀU BỊ NUỐT: lưu trữ là việc phụ. Đĩa đầy hay không có quyền
ghi thì tuyệt đối không được làm hỏng việc chính là duyệt chứng chỉ.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Chữ ký byte đầu file -> đuôi file. Nhận dạng theo NỘI CORRECT thật, không tin
# tên file eLIS gửi kèm (đã có tiền lệ tên file không khớp nội dung).
_EXTENSION_SIGNATURES = (
    (b"%PDF",             ".pdf"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff",     ".jpg"),
    (b"BM",               ".bmp"),
    (b"II*\x00",          ".tif"),
    (b"MM\x00*",          ".tif"),
)

# Các trường lấy từ getCert cần cho bộ đánh giá. employeeEmail nằm trong đây
# vì mã dùng để đối chiếu được trích từ nó (phần trước @).
_GETCERT_FIELDS = (
    "id", "certificate_id", "courseId",
    "employeeId", "employeeName", "employeeEmail", "courseName",
    "providerName",
)


def _file_extension(data_bytes: bytes) -> str:
    for signature, extension in _EXTENSION_SIGNATURES:
        if data_bytes.startswith(signature):
            return extension
    return ".bin"


def _day_folder(goc: Path) -> Path:
    """Chia theo ngày để thư mục không phình thành hàng nghìn file phẳng."""
    return goc / date.today().isoformat()


def save(image_bytes: bytes, info: dict, archive_root: str | Path) -> Path | None:
    """Lưu một chứng chỉ + metadata. Trả về đường dẫn ảnh, hoặc None nếu hỏng.

    Gọi NGAY sau khi tải được file, TRƯỚC khi chạy pipeline — để nếu pipeline
    chết giữa chừng thì ảnh vẫn còn, đó lại chính là ca đáng nghiên cứu nhất.
    """
    try:
        if not image_bytes:
            return None

        folder = _day_folder(Path(archive_root))
        folder.mkdir(parents=True, exist_ok=True)

        uc_id = str(info.get("id") or "khong-ro")
        digest = hashlib.sha256(image_bytes).hexdigest()[:8]
        base_name = f"{uc_id}_{digest}"

        image_path = folder / f"{base_name}{_file_extension(image_bytes)}"
        image_path.write_bytes(image_bytes)

        meta = {k: info.get(k) for k in _GETCERT_FIELDS}
        meta["case_id"] = base_name
        meta["thoi_diem_luu"] = datetime.now().isoformat(timespec="seconds")
        meta["ten_file_anh"] = image_path.name
        meta["so_byte"] = len(image_bytes)
        (folder / f"{base_name}.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        return image_path
    except Exception as e:
        logger.warning("Không lưu được chứng chỉ vào kho (bỏ qua): %s", e)
        return None


def write_verdict(image_path: Path | None, verdict) -> None:
    """Ghi thêm kết luận của hệ thống vào file metadata.

    ĐÂY KHÔNG PHẢI NHÃN CHUẨN. Nó là câu trả lời của chính hệ thống đang cần
    đo, lưu lại chỉ để tra cứu và để đối chiếu xem lần chạy sau có đổi kết quả
    không. Bộ sinh file nhãn cố ý KHÔNG điền giá trị này vào cột gt_verdict:
    lấy đáp án của model làm đáp án chuẩn thì model luôn đúng 100%, và mọi con
    số đo được sau đó đều vô nghĩa.
    """
    if image_path is None:
        return
    try:
        json_path = image_path.with_suffix(".json")
        if not json_path.is_file():
            return
        meta = json.loads(json_path.read_text(encoding="utf-8"))
        kq = getattr(verdict, "verdict", None)
        meta["he_thong_ket_luan"] = getattr(kq, "value", None) or str(kq)
        meta["he_thong_ly_do"] = getattr(verdict, "reason", None)
        meta["he_thong_tang"] = getattr(verdict, "stage", None)
        json_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Không ghi được kết luận vào kho (bỏ qua): %s", e)


def read_archive(archive_root: str | Path) -> list[dict]:
    """Đọc toàn bộ metadata trong kho. Trả về list dict, cũ trước mới sau."""
    goc = Path(archive_root)
    if not goc.is_dir():
        return []

    metas = []
    for f in sorted(goc.rglob("*.json")):
        try:
            meta = json.loads(f.read_text(encoding="utf-8"))
            image_name = meta.get("ten_file_anh")
            if not image_name:
                continue
            image_path = f.parent / image_name
            if not image_path.is_file():
                logger.warning("Metadata %s trỏ tới ảnh không tồn tại", f.name)
                continue
            meta["_duong_dan_anh"] = image_path
            metas.append(meta)
        except Exception as e:
            logger.warning("Bỏ qua metadata hỏng %s: %s", f.name, e)
    return metas
