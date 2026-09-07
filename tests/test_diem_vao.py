"""Test mọi ĐIỂM VÀO đều đặt KMP_DUPLICATE_LIB_OK (test_diem_vao).

LỖI ĐÃ XẢY RA THẬT: evaluation/run_llm2.py thiếu dòng này và chết ngay lúc
khởi động trên máy Windows dùng conda:

    OMP: Error #15: Initializing libiomp5md.dll, but found libiomp5md.dll
    already initialized.

Hai bản OpenMP của Intel bị nạp cùng lúc — một từ MKL của conda, một đi kèm
gói cài bằng pip. Sáu điểm vào khác của dự án đều đã có dòng này từ lâu; chỉ
file mới thêm là sót. Không có test nào bắt được, vì trên Linux/CI không tái
hiện được — chương trình chạy bình thường và bug chỉ lộ trên máy người dùng.

Test này chặn ở mức MÃ NGUỒN nên chạy được ở mọi hệ điều hành: nó đọc file và
kiểm hai điều — có đặt biến, và đặt TRƯỚC import nặng (đặt sau là vô tác dụng
vì DLL đã nạp xong).
"""

import ast
import pathlib
import sys

import pytest

GOC = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GOC))

BIEN = "KMP_DUPLICATE_LIB_OK"

# Import kéo theo thư viện tính toán/DLL. Phải nạp SAU khi đặt biến.
IMPORT_NANG = {
    "file_utils", "llm_vision", "llm_text", "ocr_azure", "pipeline",
    "client", "archive", "scheduler", "charts", "gradio", "PIL",
    "pypdfium2", "openpyxl",
}


def _diem_vao() -> list[pathlib.Path]:
    """File chạy trực tiếp được (có khối __main__)."""
    ra = []
    for p in list(GOC.glob("*.py")) + list(GOC.glob("evaluation/*.py")):
        if '__name__ == "__main__"' in p.read_text(encoding="utf-8"):
            ra.append(p)
    return sorted(ra)


def test_tim_duoc_cac_diem_vao():
    """Chính test này phải có gì để kiểm — rỗng là nó đang không bảo vệ gì."""
    name = {p.name for p in _diem_vao()}
    assert "run.py" in name and "run_eval.py" in name
    assert len(name) >= 6


@pytest.mark.parametrize("path", _diem_vao(), ids=lambda p: p.name)
def test_diem_vao_dat_bien_TRUOC_import_nang(path):
    nguon = path.read_text(encoding="utf-8")
    assert BIEN in nguon, (
        f"{path.name} thiếu os.environ[{BIEN!r}] — sẽ chết với 'OMP: Error #15' "
        f"trên Windows dùng conda")

    cay = ast.parse(nguon)
    dong_dat_bien = min(
        (n.lineno for n in ast.walk(cay)
         if isinstance(n, ast.Constant) and n.value == BIEN),
        default=None)
    assert dong_dat_bien is not None

    dong_import_nang = [
        n.lineno for n in ast.walk(cay)
        if isinstance(n, ast.Import)
        and any(a.name.split(".")[0] in IMPORT_NANG for a in n.names)
        or isinstance(n, ast.ImportFrom)
        and (n.module or "").split(".")[0] in IMPORT_NANG
    ]
    # Chỉ xét import ở MỨC MODULE; import bên trong hàm chạy sau nên vô hại.
    muc_module = {n.lineno for n in cay.body
                  if isinstance(n, (ast.Import, ast.ImportFrom))}
    dong_import_nang = [d for d in dong_import_nang if d in muc_module]

    if dong_import_nang:
        assert dong_dat_bien < min(dong_import_nang), (
            f"{path.name}: đặt {BIEN} ở dòng {dong_dat_bien}, SAU import nặng ở "
            f"dòng {min(dong_import_nang)} — lúc đó DLL đã nạp xong, đặt biến "
            f"không còn tác dụng")
