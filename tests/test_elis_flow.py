"""Test luồng tích hợp ELIS với API GIẢ LẬP (không gọi mạng thật).

Kiểm tra phần ghép nối giữa client.py / run.py / database.py:
phân trang, chia batch đúng giới hạn spec, đọc manifest, xử lý soft-fail,
dựng payload đúng hình dạng, và chống xử lý trùng.

Không cần .env thật, không tốn tiền LLM/Azure.

Chạy: pytest tests/test_elis_flow.py -v
"""

import io
import json
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

GOC = Path(__file__).parent.parent
sys.path.insert(0, str(GOC / "src"))
sys.path.insert(0, str(GOC / "database"))

import client
import database
import run

UC_OK = "76ac79da-79f8-428d-bbd5-d93d4d74f0c5"
UC_LOI = "b2c3d4e5-f6a7-8901-bcde-f12345678901"
CERT_OK = "7544d3bf-90f2-47f3-9c9d-532ee19e0245"
CERT_LOI = "66666666-7777-8888-9999-000000000000"

ITEMS = [
    {
        "id": UC_OK, "certificate_id": CERT_OK,
        "courseId": "e0986fc2-3541-4f12-aa0d-823a8c4af47d",
        "employeeId": "00332383",           # có số 0 ở đầu — không được mất
        "employeeName": "Nguyen Van A", "courseName": "Azure Fundamentals",
        "submitStatus": "WAITING",
    },
    {
        "id": UC_LOI, "certificate_id": CERT_LOI,
        "courseId": "ffffffff-aaaa-bbbb-cccc-dddddddddddd",
        "employeeId": "00445566",
        "employeeName": "Tran Thi B", "courseName": "Python co ban",
        "submitStatus": "WAITING",
    },
]


@pytest.fixture(autouse=True)
def db_tam(tmp_path):
    """Mỗi test dùng file SQLite riêng, không đụng dữ liệu thật."""
    goc = database.DB_PATH
    database.DB_PATH = tmp_path / "test.db"
    yield
    database.DB_PATH = goc


def _zip_gia() -> bytes:
    """ZIP giả lập: một entry đọc được, một entry soft-fail file_103."""
    manifest = [
        {"userCourseId": UC_OK, "certificateId": CERT_OK,
         "entryName": f"{UC_OK}_cert.png", "originalFileName": "cert.png",
         "success": True, "errorCode": None},
        {"userCourseId": UC_LOI, "certificateId": CERT_LOI,
         "entryName": "", "originalFileName": None,
         "success": False, "errorCode": "file_103"},
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr(f"{UC_OK}_cert.png", b"\x89PNG\r\n\x1a\ngia lap")
    return buf.getvalue()


class _KetQuaGia:
    """Giả lập KetQuaXuLy trả về từ pipeline.xu_ly()."""
    class _KQ:
        value = "APPROVED"
    ket_qua = _KQ()
    ly_do = "Ten, khoa hoc va thoi gian deu khop (LLM1)"
    tang_xu_ly = "llm1"


# ===== Phân trang =====

class TestPhanTrang:
    def test_gom_du_nhieu_trang(self):
        """totalPage luôn = 0 nên phải dựa vào totalRecords để biết khi nào dừng."""
        trang = [
            {"isError": False, "data": ITEMS[:1], "totalRecords": 2},
            {"isError": False, "data": ITEMS[1:], "totalRecords": 2},
        ]
        with patch.object(client, "lay_mot_trang_cho_duyet", side_effect=trang):
            assert len(client.lay_toan_bo_cho_duyet()) == 2

    def test_trang_rong_thi_dung(self):
        with patch.object(client, "lay_mot_trang_cho_duyet",
                          return_value={"isError": False, "data": [], "totalRecords": 0}):
            assert client.lay_toan_bo_cho_duyet() == []


# ===== Giới hạn batch (mục 7 của tài liệu) =====

class TestChiaBatch:
    def test_download_zip_toi_da_20(self):
        assert [len(b) for b in client.chia_batch(list(range(45)), 20)] == [20, 20, 5]

    def test_process_status_toi_da_500(self):
        assert [len(b) for b in client.chia_batch(list(range(1200)), 500)] == [500, 500, 200]

    def test_tu_choi_qua_gioi_han(self):
        """Gọi trực tiếp quá giới hạn phải báo lỗi ngay, không gửi lên ELIS."""
        with pytest.raises(ValueError, match="vượt giới hạn"):
            client.tai_zip_chung_chi([{"UserCourseId": "x", "certificate_id": "y"}] * 21)
        with pytest.raises(ValueError, match="vượt giới hạn"):
            client.cap_nhat_trang_thai([{"id": "x"}] * 501)


# ===== Toàn bộ vòng chạy =====

class TestVongChay:
    @staticmethod
    def _chay(thu_thap: dict):
        def ghi_lai(dtos):
            thu_thap["payload"] = dtos
            return {"successList": dtos, "failList": []}

        with patch.object(client, "lay_mot_trang_cho_duyet",
                          return_value={"isError": False, "data": ITEMS, "totalRecords": 2}), \
             patch.object(client, "tai_zip_chung_chi", return_value=_zip_gia()), \
             patch.object(client, "cap_nhat_trang_thai", side_effect=ghi_lai), \
             patch.object(run.ocr_azure, "tao_client", return_value=None), \
             patch.object(run.file_utils, "doc_thanh_anh", return_value=[b"anh"]), \
             patch.object(run.pipeline, "xu_ly", return_value=_KetQuaGia()):
            return run.chay_mot_vong()

    def test_xu_ly_du_ca_hai(self):
        thu = {}
        # chay_mot_vong() trả (số tìm thấy, số xử lý xong)
        assert self._chay(thu) == (2, 2)

    def test_file_doc_duoc_thi_chay_ai(self):
        thu = {}
        self._chay(thu)
        dto = {p["id"]: p for p in thu["payload"]}[UC_OK]
        assert dto["status"] == "APPROVED"

    def test_soft_fail_khong_goi_ai(self):
        """file_103: ELIS không có file -> REJECTED luôn, không tốn tiền LLM."""
        thu = {}
        self._chay(thu)
        dto = {p["id"]: p for p in thu["payload"]}[UC_LOI]
        assert dto["status"] == "REJECTED"
        assert "file_103" in dto["comment_cer"]

    def test_ma_nv_giu_so_0_dau(self):
        """Ép sang số sẽ thành 332383 -> ELIS trả failList 'không khớp mã NV'."""
        thu = {}
        self._chay(thu)
        dto = {p["id"]: p for p in thu["payload"]}[UC_OK]
        assert dto["employeeId"] == "00332383"
        assert isinstance(dto["employeeId"], str)

    def test_payload_dung_7_field(self):
        thu = {}
        self._chay(thu)
        for dto in thu["payload"]:
            assert set(dto) == {
                "id", "certificate_id", "status", "courseId",
                "employeeId", "comment", "comment_cer",
            }

    def test_comment_luon_co_noi_dung(self):
        """comment là field BẮT BUỘC theo spec mục 5.2."""
        thu = {}
        self._chay(thu)
        assert all(dto["comment"] for dto in thu["payload"])

    def test_status_chi_approved_hoac_rejected(self):
        thu = {}
        self._chay(thu)
        assert all(dto["status"] in {"APPROVED", "REJECTED"} for dto in thu["payload"])


# ===== Chống xử lý trùng =====

class TestNhanDienZip:
    """Nhận diện ZIP bằng magic bytes, không tin Content-Type.

    Gateway/proxy hay khai sai Content-Type. Tin nội dung thật chắc hơn
    tin nhãn. Mọi file ZIP đều bắt đầu bằng b"PK".
    """

    PAYLOAD = [{"UserCourseId": "a", "certificate_id": "b"}]

    @staticmethod
    def _resp(noi_dung: bytes, content_type: str, status: int = 200):
        from unittest.mock import MagicMock
        r = MagicMock()
        r.content = noi_dung
        r.headers = {"Content-Type": content_type}
        r.status_code = status
        r.text = noi_dung.decode("utf-8", "replace")
        r.json = lambda: json.loads(r.text)
        r.raise_for_status = lambda: None
        return r

    @staticmethod
    def _zip_bytes(rac_dau: bytes = b"") -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", "[]")
        return rac_dau + buf.getvalue()

    @pytest.mark.parametrize("content_type", [
        "application/zip",
        "application/octet-stream",
        "application/x-zip-compressed",
        "text/plain",
        "",
    ])
    def test_nhan_dien_zip_moi_content_type(self, content_type):
        resp = self._resp(self._zip_bytes(), content_type)
        with patch.object(client.requests, "post", return_value=resp):
            kq = client.tai_zip_chung_chi(self.PAYLOAD)
        assert client._co_phai_zip(kq)

    @pytest.mark.parametrize("rac_dau,mo_ta", [
        (b"", "không có rác"),
        (b"]", "1 byte rác đầu stream"),
        (b"\xef\xbb\xbf", "BOM UTF-8"),
        (b"\x5d\x00\x00\x80" * 16, "64 byte rác"),
    ])
    def test_zip_co_rac_o_dau_van_nhan_dien_duoc(self, rac_dau, mo_ta):
        """ZIP lưu mục lục ở CUỐI file nên zipfile đọc được dù đầu có rác.
        Chỉ kiểm tra 2 byte 'PK' đầu là trượt những ca này."""
        resp = self._resp(self._zip_bytes(rac_dau), "application/octet-stream")
        with patch.object(client.requests, "post", return_value=resp):
            assert client._co_phai_zip(client.tai_zip_chung_chi(self.PAYLOAD))

    @staticmethod
    def _resp_zip_boc_json(zip_bytes: bytes):
        """Mô phỏng response THẬT của ELIS: ZIP bọc trong chuỗi JSON."""
        than = json.dumps(zip_bytes.decode("latin-1")).encode("utf-8")
        from unittest.mock import MagicMock
        r = MagicMock()
        r.content = than
        r.headers = {"Content-Type": "application/json; charset=utf-8"}
        r.status_code = 200
        r.text = than.decode("utf-8")
        r.json = lambda: json.loads(r.text)
        r.raise_for_status = lambda: None
        return r

    def test_zip_boc_trong_chuoi_json(self):
        """ELIS trả Content-Type: application/json với thân là chuỗi
        "PK\\u0003\\u0004..." — phải giải mã latin-1 để lấy lại ZIP."""
        goc = self._zip_bytes()
        with patch.object(client.requests, "post",
                          return_value=self._resp_zip_boc_json(goc)):
            kq = client.tai_zip_chung_chi(self.PAYLOAD)
        assert kq == goc, "ZIP khôi phục phải giống hệt bản gốc"
        assert client._co_phai_zip(kq)

    def test_zip_boc_json_giu_nguyen_ven_byte_nhi_phan(self):
        """Kiểm tra không mất mát với đủ 256 giá trị byte."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", "[]")
            zf.writestr("cert.pdf", bytes(range(256)) * 100)
        goc = buf.getvalue()
        with patch.object(client.requests, "post",
                          return_value=self._resp_zip_boc_json(goc)):
            kq = client.tai_zip_chung_chi(self.PAYLOAD)
        with zipfile.ZipFile(io.BytesIO(kq)) as zf:
            assert zf.read("cert.pdf") == bytes(range(256)) * 100

    def test_chuoi_json_khong_phai_zip_thi_van_bao_loi(self):
        """Chuỗi JSON bình thường (thông báo lỗi) không được nhận nhầm là ZIP."""
        from unittest.mock import MagicMock
        r = MagicMock()
        than = json.dumps("Danh sách không được rỗng").encode("utf-8")
        r.content = than
        r.headers = {"Content-Type": "application/json"}
        r.status_code = 200
        r.text = than.decode("utf-8")
        r.json = lambda: json.loads(r.text)
        r.raise_for_status = lambda: None
        with patch.object(client.requests, "post", return_value=r):
            with pytest.raises(client.ElisApiError):
                client.tai_zip_chung_chi(self.PAYLOAD)

    def test_thong_bao_loi_khong_chua_ky_tu_dieu_khien(self):
        """Dữ liệu nhị phân in thẳng ra terminal sẽ đè mất chính thông báo
        lỗi mình cần đọc (\\r về đầu dòng, \\x1b[2J xóa màn hình)."""
        rac = b"\x5d\x00\r\r\rDE MAT DONG\x08\x08\x1b[2J"
        resp = self._resp(rac, "application/octet-stream")
        with patch.object(client.requests, "post", return_value=resp):
            with pytest.raises(client.ElisApiError) as e:
                client.tai_zip_chung_chi(self.PAYLOAD)
        thong_bao = str(e.value)
        assert not any(c in thong_bao for c in ("\r", "\x08", "\x1b"))

    @pytest.mark.parametrize("than_body,mo_ta", [
        (b'"Danh sach khong duoc rong"', "JSON là chuỗi"),
        (b'[1,2,3]', "JSON là mảng"),
        (b'{"isError":true,"code":400,"message":"file_102"}', "envelope báo lỗi"),
        (b'<html>403 Forbidden</html>', "HTML từ gateway"),
    ])
    def test_body_la_khong_phai_zip_thi_bao_loi_ro(self, than_body, mo_ta):
        """Phải ném ElisApiError có nội dung đọc được, KHÔNG được để lọt
        AttributeError kiểu 'str object has no attribute get'."""
        resp = self._resp(than_body, "application/json")
        with patch.object(client.requests, "post", return_value=resp):
            with pytest.raises(client.ElisApiError):
                client.tai_zip_chung_chi(self.PAYLOAD)


class TestCauHinhUrl:
    """URL code ghép ra phải khớp đúng curl mentor gửi."""

    def test_url_khop_curl_mentor(self):
        from config import settings
        goc = settings.api_base_url, settings.kong_api_prefix
        settings.api_base_url = "https://apitest.fpt.com"
        settings.kong_api_prefix = "/uat-elis-gw"
        try:
            assert settings.url_api("/api/v1/UserCourse/elearning/getCert") == (
                "https://apitest.fpt.com/uat-elis-gw"
                "/api/v1/UserCourse/elearning/getCert"
            )
            assert settings.url_api("/api/v1/UserCourse/ProcessUserCourseStatus") == (
                "https://apitest.fpt.com/uat-elis-gw"
                "/api/v1/UserCourse/ProcessUserCourseStatus"
            )
        finally:
            settings.api_base_url, settings.kong_api_prefix = goc

    def test_key_file_dung_chung_khi_de_trong(self):
        """KONG_API_KEY rỗng -> tự dùng API_KEY, khỏi phải dán key hai lần."""
        from config import settings
        goc = settings.kong_api_key
        settings.kong_api_key = ""
        try:
            assert settings.khoa_file == settings.api_key
        finally:
            settings.kong_api_key = goc

    def test_key_file_rieng_thi_uu_tien(self):
        """Nếu eLIS cấp key riêng thì phải dùng key riêng, không lấy api_key."""
        from config import settings
        goc = settings.kong_api_key
        settings.kong_api_key = "key_rieng_cua_fileservice"
        try:
            assert settings.khoa_file == "key_rieng_cua_fileservice"
        finally:
            settings.kong_api_key = goc

    def test_thieu_cau_hinh_thi_bao_loi_ro(self):
        from config import settings
        goc = settings.kong_base_url
        settings.kong_base_url = ""
        try:
            with pytest.raises(ValueError, match="KONG_BASE_URL"):
                settings.url_file("/api/v1/files/download-certificates-zip")
        finally:
            settings.kong_base_url = goc


class TestNhipVongLap:
    """Vòng lặp nghỉ ngắn khi có việc, nghỉ dài khi rảnh."""

    @staticmethod
    def _do_nhip(ket_qua_tung_vong, ngan=1, dai=3):
        """Chạy vòng lặp giả lập, trả list số giây đã nghỉ giữa các vòng."""
        from config import settings
        goc = settings.poll_interval_giay, settings.poll_interval_rong_giay
        settings.poll_interval_giay, settings.poll_interval_rong_giay = ngan, dai

        con_lai = list(ket_qua_tung_vong)
        da_nghi = []

        def vong():
            if not con_lai:
                raise KeyboardInterrupt
            kq = con_lai.pop(0)
            if isinstance(kq, Exception):
                raise kq
            return kq

        try:
            with patch.object(run, "chay_mot_vong", side_effect=vong), \
                 patch.object(run, "_ngu", side_effect=da_nghi.append):
                run.vong_lap_lien_tuc()
        except KeyboardInterrupt:
            pass
        finally:
            settings.poll_interval_giay, settings.poll_interval_rong_giay = goc
        return da_nghi

    def test_co_viec_thi_nghi_ngan(self):
        assert self._do_nhip([(2, 2)]) == [1]

    def test_ranh_thi_nghi_dai(self):
        assert self._do_nhip([(0, 0)]) == [3]

    def test_xen_ke_dung_nhip(self):
        assert self._do_nhip([(2, 2), (0, 0), (0, 0), (1, 1)]) == [1, 3, 3, 1]

    def test_tai_zip_hong_van_coi_la_co_viec(self):
        """Tìm thấy 5 nhưng tải hỏng hết -> vẫn nghỉ NGẮN để thử lại sớm,
        không được ngủ dài như lúc thật sự rảnh."""
        assert self._do_nhip([(5, 0)]) == [1]

    def test_loi_thi_nghi_dai_va_khong_chet(self):
        """Vòng lỗi không được làm chết job — nghỉ dài rồi chạy tiếp."""
        assert self._do_nhip([RuntimeError("mất mạng"), (1, 1)]) == [3, 1]


class TestChongTrung:
    def test_da_nop_ok_thi_bo_qua(self):
        TestVongChay._chay({})
        with database.ket_noi() as conn:
            con_lai = [it for it in ITEMS if not database.da_xu_ly(conn, it["id"])]
        assert con_lai == []

    def test_nop_that_bai_thi_van_thu_lai(self):
        """Nộp hụt do mạng -> vòng sau PHẢI thử lại, không được bỏ quên."""
        with database.ket_noi() as conn:
            database.ghi_ket_qua_xu_ly(
                conn, UC_OK, CERT_OK, "course-1", "00332383",
                "Nguyen Van A", "Azure Fundamentals", "APPROVED", "ok", "llm1",
            )
            database.ghi_ket_qua_nop_elis(conn, UC_OK, thanh_cong=False, message="loi mang")
            assert database.da_xu_ly(conn, UC_OK) is False
