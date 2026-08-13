"""Web demo cho core (web_demo).

Giao diện web bằng Gradio: upload 1 ảnh/PDF chứng chỉ, nhập tên/mã/khóa học,
xem ảnh hiển thị và kết quả APPROVED/REJECTED với lý do chi tiết.

Bên trong gọi đúng pipeline như run_local. KHÔNG liên quan ELIS.

Chạy:
    python web_demo.py
Link nội bộ (http://127.0.0.1:7860) và link công khai tạm thời (share) sẽ hiện ra.

Cần file .env với FPT_API_KEY, AZURE_ENDPOINT, AZURE_KEY.
"""

import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import gradio as gr
from PIL import Image

import file_utils
import llm_text
import llm_vision
import ocr_azure
import pipeline
import process_data
from config import settings
from schemas import ThongTinNhap

_azure_client = None


def _get_azure():
    global _azure_client
    if _azure_client is None:
        _azure_client = ocr_azure.tao_client()
    return _azure_client


def _anh_de_hien_thi(anh_path):
    """Đọc file thành danh sách ảnh PIL để hiển thị (PDF -> nhiều trang ảnh)."""
    try:
        anh_bytes_list = file_utils.doc_thanh_anh(anh_path)
    except file_utils.FileKhongHopLe:
        return []
    anh_pil = []
    for b in anh_bytes_list:
        try:
            anh_pil.append(Image.open(io.BytesIO(b)))
        except Exception:
            pass
    return anh_pil


def xu_ly_demo(anh_path, ten, ma, khoa_hoc):
    """Nhận input, chạy pipeline, trả về (ảnh hiển thị, text kết quả)."""
    if not anh_path:
        return [], "Vui lòng tải lên một ảnh chứng chỉ."
    if not ten or not ma or not khoa_hoc:
        return [], "Vui lòng nhập đủ: tên, mã nhân viên, tên khóa học."

    anh_hien_thi = _anh_de_hien_thi(anh_path)

    try:
        anh_list = file_utils.doc_thanh_anh(anh_path)
    except file_utils.FileKhongHopLe as e:
        return anh_hien_thi, f"Lỗi file: {e}"

    nhap = ThongTinNhap(ten_nhan_vien=ten, ten_khoa_hoc=khoa_hoc, ma_nhan_vien=ma)

    try:
        kq = pipeline.xu_ly(
            anh_list=anh_list,
            nhap=nhap,
            trich_tu_anh=llm_vision.trich_tu_anh,
            ocr_nhieu_anh=ocr_azure.ocr_nhieu_anh,
            trich_tu_text=llm_text.trich_tu_text,
            azure_client=_get_azure(),
        )
    except Exception as e:
        return anh_hien_thi, f"Lỗi khi xử lý: {e}"

    icon = "✅" if kq.ket_qua.value == "APPROVED" else "❌"
    dong = [
        f"## {icon} {kq.ket_qua.value}",
        "",
        f"**Lý do:** {kq.ly_do}",
        f"**Tầng xử lý:** {kq.tang_xu_ly}",
        "",
        "### Thông tin trích được từ ảnh",
    ]
    if kq.trich_xuat:
        t = kq.trich_xuat
        dong.append(f"- Tên người nhận: **{t.ten_nguoi_nhan}**")
        dong.append(f"- Tên chứng chỉ: **{t.ten_chung_chi}**")
        if t.ten_chung_chi_phu:
            dong.append(f"- Tên (ngôn ngữ 2): **{t.ten_chung_chi_phu}**")
        dong.append(f"- Ngày nhận: **{t.ngay_nhan}**")
        trong = process_data.ngay_hop_le(
            t.ngay_nhan, settings.thoi_gian_hop_le_tu, settings.thoi_gian_hop_le_den
        )
        dong.append(
            f"- Thời gian: {'trong khoảng ✅' if trong else 'ngoài khoảng ❌'} "
            f"(khoảng hợp lệ: {settings.thoi_gian_hop_le_tu} .. {settings.thoi_gian_hop_le_den})"
        )

    dong += [
        "",
        "### Thông tin đã nhập",
        f"- Tên: {ten}",
        f"- Mã: {ma}",
        f"- Khóa học: {khoa_hoc}",
    ]
    return anh_hien_thi, "\n".join(dong)


def tao_giao_dien():
    with gr.Blocks(title="MOOC - Demo xác minh chứng chỉ") as demo:
        gr.Markdown("# Demo xác minh chứng chỉ MOOC")
        gr.Markdown("Tải ảnh/PDF chứng chỉ và nhập thông tin để kiểm tra.")

        with gr.Row():
            with gr.Column():
                anh = gr.File(
                    label="Ảnh chứng chỉ (JPG, PNG, PDF)",
                    file_types=["image", ".pdf"],
                    type="filepath",
                )
                ten = gr.Textbox(label="Tên nhân viên")
                ma = gr.Textbox(label="Mã nhân viên")
                khoa_hoc = gr.Textbox(label="Tên khóa học")
                nut = gr.Button("Kiểm tra", variant="primary")
            with gr.Column():
                anh_xem = gr.Gallery(
                    label="click to see full image",
                    columns=1,
                    height=500,
                    object_fit="contain",  # thu nhỏ trọn ảnh cho lọt khuôn, không cắt góc
                    preview=True,           # click để xem ảnh phóng to
                )
                ket_qua = gr.Markdown(label="Kết quả")

        # Hiện ảnh NGAY khi vừa chọn file, không chờ bấm nút.
        anh.change(
            fn=_anh_de_hien_thi,
            inputs=anh,
            outputs=anh_xem,
        )

        nut.click(
            fn=xu_ly_demo,
            inputs=[anh, ten, ma, khoa_hoc],
            outputs=[anh_xem, ket_qua],
        )
    return demo


if __name__ == "__main__":
    # share=True: tạo link công khai tạm thời (~72h) để chia sẻ qua internet.
    tao_giao_dien().launch(share=True)