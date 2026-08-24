"""Kho lưu chứng chỉ thật (kho_luu).

Giữ lại ảnh chứng chỉ + thông tin getCert ngay lúc job tải về, để sau này
chạy lại bộ đánh giá mà không cần eLIS.

VÌ SAO CẦN: sau khi job nộp APPROVED/REJECTED qua API ③, bản ghi rời trạng
thái WAITING nên vòng getCert sau không trả về nó nữa. Data thật đi qua hệ
thống đúng MỘT lần. Trước đây ảnh còn bị ghi ra file tạm rồi xóa ngay sau khi
đọc — chạy xong là mất sạch, trong DB chỉ còn CHỮ model đọc được chứ không
còn ẢNH. Mà đánh giá bước trích xuất thì bắt buộc phải có ảnh gốc.

TÊN FILE THEO NỘI DUNG: {user_course_id}_{8 ký tự băm nội dung}.{đuôi}
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

# Chữ ký byte đầu file -> đuôi file. Nhận dạng theo NỘI DUNG thật, không tin
# tên file eLIS gửi kèm (đã có tiền lệ tên file không khớp nội dung).
_CHU_KY_DUOI = (
    (b"%PDF",             ".pdf"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff",     ".jpg"),
    (b"BM",               ".bmp"),
    (b"II*\x00",          ".tif"),
    (b"MM\x00*",          ".tif"),
)

# Các trường lấy từ getCert cần cho bộ đánh giá. employeeEmail nằm trong đây
# vì mã dùng để đối chiếu được trích từ nó (phần trước @).
_TRUONG_GETCERT = (
    "id", "certificate_id", "courseId",
    "employeeId", "employeeName", "employeeEmail", "courseName",
)


def _duoi_file(du_lieu: bytes) -> str:
    for chu_ky, duoi in _CHU_KY_DUOI:
        if du_lieu.startswith(chu_ky):
            return duoi
    return ".bin"


def _thu_muc_ngay(goc: Path) -> Path:
    """Chia theo ngày để thư mục không phình thành hàng nghìn file phẳng."""
    return goc / date.today().isoformat()


def luu(anh_bytes: bytes, thong_tin: dict, goc_kho: str | Path) -> Path | None:
    """Lưu một chứng chỉ + metadata. Trả về đường dẫn ảnh, hoặc None nếu hỏng.

    Gọi NGAY sau khi tải được file, TRƯỚC khi chạy pipeline — để nếu pipeline
    chết giữa chừng thì ảnh vẫn còn, đó lại chính là ca đáng nghiên cứu nhất.
    """
    try:
        if not anh_bytes:
            return None

        thu_muc = _thu_muc_ngay(Path(goc_kho))
        thu_muc.mkdir(parents=True, exist_ok=True)

        uc_id = str(thong_tin.get("id") or "khong-ro")
        bam = hashlib.sha256(anh_bytes).hexdigest()[:8]
        ten_goc = f"{uc_id}_{bam}"

        duong_dan_anh = thu_muc / f"{ten_goc}{_duoi_file(anh_bytes)}"
        duong_dan_anh.write_bytes(anh_bytes)

        meta = {k: thong_tin.get(k) for k in _TRUONG_GETCERT}
        meta["ma_ca"] = ten_goc
        meta["thoi_diem_luu"] = datetime.now().isoformat(timespec="seconds")
        meta["ten_file_anh"] = duong_dan_anh.name
        meta["so_byte"] = len(anh_bytes)
        (thu_muc / f"{ten_goc}.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        return duong_dan_anh
    except Exception as e:
        logger.warning("Không lưu được chứng chỉ vào kho (bỏ qua): %s", e)
        return None


def ghi_ket_luan(duong_dan_anh: Path | None, ket_qua) -> None:
    """Ghi thêm kết luận của hệ thống vào file metadata.

    ĐÂY KHÔNG PHẢI NHÃN CHUẨN. Nó là câu trả lời của chính hệ thống đang cần
    đo, lưu lại chỉ để tra cứu và để đối chiếu xem lần chạy sau có đổi kết quả
    không. Bộ sinh file nhãn cố ý KHÔNG điền giá trị này vào cột gt_ket_qua:
    lấy đáp án của model làm đáp án chuẩn thì model luôn đúng 100%, và mọi con
    số đo được sau đó đều vô nghĩa.
    """
    if duong_dan_anh is None:
        return
    try:
        duong_dan_json = duong_dan_anh.with_suffix(".json")
        if not duong_dan_json.is_file():
            return
        meta = json.loads(duong_dan_json.read_text(encoding="utf-8"))
        kq = getattr(ket_qua, "ket_qua", None)
        meta["he_thong_ket_luan"] = getattr(kq, "value", None) or str(kq)
        meta["he_thong_ly_do"] = getattr(ket_qua, "ly_do", None)
        meta["he_thong_tang"] = getattr(ket_qua, "tang_xu_ly", None)
        duong_dan_json.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Không ghi được kết luận vào kho (bỏ qua): %s", e)


def doc_kho(goc_kho: str | Path) -> list[dict]:
    """Đọc toàn bộ metadata trong kho. Trả về list dict, cũ trước mới sau."""
    goc = Path(goc_kho)
    if not goc.is_dir():
        return []

    cac_meta = []
    for f in sorted(goc.rglob("*.json")):
        try:
            meta = json.loads(f.read_text(encoding="utf-8"))
            ten_anh = meta.get("ten_file_anh")
            if not ten_anh:
                continue
            duong_dan_anh = f.parent / ten_anh
            if not duong_dan_anh.is_file():
                logger.warning("Metadata %s trỏ tới ảnh không tồn tại", f.name)
                continue
            meta["_duong_dan_anh"] = duong_dan_anh
            cac_meta.append(meta)
        except Exception as e:
            logger.warning("Bỏ qua metadata hỏng %s: %s", f.name, e)
    return cac_meta
