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
from schemas import InputInfo

_azure_client = None


def _get_azure():
    global _azure_client
    if _azure_client is None:
        _azure_client = ocr_azure.create_client()
    return _azure_client


def _image_for_display(image_path):
    """Đọc file thành danh sách ảnh PIL để hiển thị (PDF -> nhiều trang ảnh)."""
    if not image_path:
        return []
    try:
        image_bytes_list = file_utils.read_as_images(image_path)
    except file_utils.InvalidFileError:
        return []
    pil_image = []
    for b in image_bytes_list:
        try:
            pil_image.append(Image.open(io.BytesIO(b)))
        except Exception:
            pass
    return pil_image


def process_demo(image_path, name, code, course):
    """Nhận input, chạy pipeline, trả về (ảnh hiển thị, text kết quả)."""
    if not image_path:
        return [], "Vui lòng tải lên một ảnh chứng chỉ."
    if not name or not code or not course:
        return [], "Vui lòng nhập đủ: tên, mã nhân viên, tên khóa học."

    display_image = _image_for_display(image_path)

    try:
        images = file_utils.read_as_images(image_path)
    except file_utils.InvalidFileError as e:
        return display_image, f"Lỗi file: {e}"

    given = InputInfo(employee_name=name, course_name=course, employee_code=code)

    try:
        result = pipeline.process(
            images=images,
            given=given,
            extract_from_image=llm_vision.extract_from_image,
            ocr_images=ocr_azure.ocr_images,
            extract_from_text=llm_text.extract_from_text,
            azure_client=_get_azure(),
        )
    except Exception as e:
        return display_image, f"Lỗi khi xử lý: {e}"

    icon = "✅" if result.verdict.value == "APPROVED" else "❌"
    lines = [
        f"## {icon} {result.verdict.value}",
        "",
        f"**Lý do:** {result.reason}",
        f"**Tầng xử lý:** {result.stage}",
        "",
        "### Thông tin trích được từ ảnh",
    ]
    if result.extracted:
        t = result.extracted
        lines.append(f"- Tên người nhận: **{t.recipient_name}**")
        lines.append(f"- Tên chứng chỉ: **{t.certificate_name}**")
        if t.certificate_name_alt:
            lines.append(f"- Tên (ngôn ngữ 2): **{t.certificate_name_alt}**")
        lines.append(f"- Ngày nhận: **{t.issue_date}**")
        in_range = process_data.date_in_range(
            t.issue_date, settings.valid_from, settings.valid_to
        )
        lines.append(
            f"- Thời gian: {'trong khoảng ✅' if in_range else 'ngoài khoảng ❌'} "
            f"(khoảng hợp lệ: {settings.valid_from} .. {settings.valid_to})"
        )

    lines += [
        "",
        "### Thông tin đã nhập",
        f"- Tên: {name}",
        f"- Mã: {code}",
        f"- Khóa học: {course}",
    ]
    return display_image, "\n".join(lines)


def build_ui():
    with gr.Blocks(title="MOOC - Demo xác minh chứng chỉ") as demo:
        gr.Markdown("# Demo xác minh chứng chỉ MOOC")
        gr.Markdown("Tải ảnh/PDF chứng chỉ và nhập thông tin để kiểm tra.")

        with gr.Row():
            with gr.Column():
                image = gr.File(
                    label="Ảnh chứng chỉ (JPG, PNG, PDF)",
                    file_types=["image", ".pdf"],
                    type="filepath",
                )
                # Ảnh hiện ngay dưới ô upload, cùng cột với các ô nhập.
                preview_image = gr.Gallery(
                    label="Ảnh chứng chỉ đã tải",
                    columns=1,
                    height=400,
                    object_fit="contain",  # thu nhỏ trọn ảnh cho lọt khuôn, không cắt góc
                    preview=True,           # click để xem ảnh phóng to
                )
                name = gr.Textbox(label="Tên nhân viên")
                code = gr.Textbox(label="Mã nhân viên")
                course = gr.Textbox(label="Tên khóa học")
                button = gr.Button("Kiểm tra", variant="primary")
            with gr.Column():
                verdict = gr.Markdown(label="Kết quả")

        # Hiện ảnh NGAY khi vừa chọn file, không chờ bấm nút.
        image.change(
            fn=_image_for_display,
            inputs=image,
            outputs=preview_image,
        )

        button.click(
            fn=process_demo,
            inputs=[image, name, code, course],
            outputs=[preview_image, verdict],
        )
    return demo


if __name__ == "__main__":
    # share=True: cố tạo link công khai tạm thời (~72h) để chia sẻ qua internet.
    # Nếu mạng chặn (không tạo được share link), Gradio tự chạy link nội bộ
    # http://127.0.0.1:7860 — vẫn dùng được trên máy này, chỉ không share ra ngoài.
    build_ui().launch(share=True)
