"""Module so sánh (compare).

Chứa các luật khớp đã chốt. Mọi chuỗi đều đi qua process_data.normalize()
trước khi so, nên ở đây không chuẩn hóa lại.

1. So với input người nhập (match_name, match_course): tập hợp TỪ, so tuyệt
   đối, bỏ qua thứ tự nên đảo surname/given name vẫn khớp.
2. identical_after_normalize(): so LLM1 với LLM2, CHẶT (== sau chuẩn hóa) vì
   cả hai đều là máy đọc cùng một ảnh, không có yếu tố gõ tay.
3. match_code(): so mã NV tuyệt đối, tách từng từ (chứng chỉ in username/ID).
"""

import re

from process_data import normalize

def same_word_set(a: str | None, b: str | None) -> bool:
    """So hai chuỗi theo tập hợp TỪ, tuyệt đối, bỏ qua thứ tự.

    "A NGUYEN VAN" vs "NGUYEN VAN A" -> cùng tập {a, nguyen, van} -> khớp.

    Mỗi từ phải đúng tuyệt đối sau chuẩn hóa; không chịu lỗi OCR trong từ.
    Chuỗi rỗng không khớp, tránh khớp giả khi cả hai cùng rỗng.
    """
    words_a = normalize(a).split()
    words_b = normalize(b).split()
    if not words_a or not words_b:
        return False
    # So tap hop: bo qua thu tu va so lan lap.
    return set(words_a) == set(words_b)


def identical_after_normalize(value_1: str | None, value_2: str | None) -> bool:
    """So LLM1 với LLM2: bằng nhau tuyệt đối sau chuẩn hóa.

    Hai chuỗi rỗng KHÔNG tính là giống nhau — đó là 'cùng không biết'.
    """
    a, b = normalize(value_1), normalize(value_2)
    if not a or not b:
        return False
    return a == b


def match_code(name_on_image, employee_code):
    """So MÃ nhân viên với tên trên ảnh — tuyệt đối, so từng từ.

    Một số chứng chỉ in username/ID thay cho tên thật. Mã NV là chuỗi máy nên
    phải khớp CHÍNH XÁC ("hungnt97" khác "hungnt98"); không dùng fuzzy. So
    từng từ vì ID hay bị lặp hoặc lẫn với chữ khác.
    """
    code = normalize(employee_code)
    image = normalize(name_on_image)
    if not code or not image:
        return False
    # Mã có thể là một từ (hungnt97) hoặc nhiều từ sau chuẩn hóa (nv-001 ->
    # "nv 001"), nên tìm mã như một CỤM TỪ liên tiếp. So trọn từ để mã "nv"
    # không khớp nhầm "nvidia".
    code_words = code.split()
    image_words = image.split()
    n = len(code_words)
    for i in range(len(image_words) - n + 1):
        if image_words[i:i + n] == code_words:
            return True
    return False


def match_name(llm_value, input_value):
    """So TÊN NGƯỜI với input: tập hợp từ, bỏ qua thứ tự, chặt từng từ."""
    return same_word_set(llm_value, input_value)


def match_name_or_code(name_on_image, employee_name, employee_code):
    """Tên trên ảnh khớp nếu khớp TÊN nhân viên HOẶC MÃ nhân viên."""
    if match_name(name_on_image, employee_name):
        return True
    if match_code(name_on_image, employee_code):
        return True
    return False


def input_is_subset(input_value, image_value):
    """True khi MỌI từ người nhập đều có trong tên trên ảnh (ảnh được phép thừa).

    Dùng cho chế độ "loose". An toàn hơn substring: nhập "Python nâng cao"
    KHÔNG khớp ảnh "Python". Chỉ chấp nhận ảnh thừa, không chấp nhận nhập thừa.
    """
    input_words = set(normalize(input_value).split())
    image_words = set(normalize(image_value).split())
    if not input_words or not image_words:
        return False
    return input_words.issubset(image_words)


def match_course(image_value, input_value, mode="strict"):
    """So TÊN KHÓA HỌC với input.

    "strict": trùng khớp hoàn toàn. "loose": nhập là TẬP CON của tên trên ảnh.
    """
    if mode == "loose":
        return input_is_subset(input_value, image_value)
    return same_word_set(image_value, input_value)


def match_course_bilingual(primary, secondary, input_value, mode="strict"):
    """So tên khóa học với input, chấp nhận cả hai ngôn ngữ VÀ bản ghép hai nửa.

    Ảnh in tên khóa song ngữ -> LLM tách làm hai phần (primary/secondary),
    nhưng eLIS lưu tên khóa ở cả ba dạng: chỉ tiếng Việt, chỉ tiếng Anh, hoặc
    cả hai nối lại. Nên thử đủ ba cách — nửa đầu, nửa sau, GHÉP hai nửa —
    khớp một cách là đủ. Chỉ nới thêm đường khớp, không bỏ đường nào.
    """
    if match_course(primary, input_value, mode):
        return True
    if match_course(secondary, input_value, mode):
        return True
    # Ghép hai nửa; thứ tự không quan trọng vì cả hai chế độ so đều dùng TẬP
    # HỢP TỪ.
    if primary and secondary:
        if match_course(f"{primary} {secondary}", input_value, mode):
            return True
    return False


# ===== Chứng chỉ ghi email thay cho tên người nhận =====
# Khóa ngoài (Coursera, Udemy...) hay đăng ký bằng email cá nhân -> không nối
# được với nhân viên nào. Email CÔNG TY thì tự khớp: normalize() cắt "@" và
# "." thành khoảng trắng nên "doannv19@fpt.com" thành "doannv19 fpt com" và
# match_code tìm ra mã. Vì vậy chỉ đuôi NGOÀI công ty cần xử lý riêng.
COMPANY_EMAIL_DOMAIN = "fpt.com"

_EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def external_email(name_on_image: str | None) -> str | None:
    """Trả về ĐUÔI email ngoài công ty in trên ảnh, hoặc None.

    Chạy trên chuỗi GỐC, trước normalize(): normalize cắt mất dấu "@". None
    mang hai nghĩa — "không có email" và "email công ty" — người gọi xử lý
    như nhau.
    """
    if not name_on_image:
        return None
    found = _EMAIL_PATTERN.search(str(name_on_image))
    if not found:
        return None
    domain = found.group(0).rsplit("@", 1)[-1].lower()
    return None if domain == COMPANY_EMAIL_DOMAIN else domain


def name_missing_words(name_on_image: str | None,
                       employee_name: str | None) -> bool:
    """True khi tên trên ảnh là TẬP CON THỰC SỰ của tên eLIS — thiếu từ.

    "Lê Tiến" so với "Lê Xuân Tiến": ảnh thiếu "xuân", do nhà cấp chứng chỉ
    ngoài in tên người học tự gõ mà người Việt hay bỏ tên đệm.

    Tập con THỰC SỰ (<) chứ không phải <=: hai tập bằng nhau đã được
    match_name bắt từ trước. KHÔNG bắt chiều ngược lại (ảnh thừa từ) vì từ
    thừa có thể là chức danh hoặc tên người khác in kèm.
    """
    image = set(normalize(name_on_image).split())
    given = set(normalize(employee_name).split())
    if not image or not given:
        return False
    return image < given
