"""Module so sánh (compare).

Chứa các luật khớp đã chốt. Mọi chuỗi đều được đưa qua process_data.normalize()
trước khi so, nên ở đây không lặp lại việc chuẩn hóa.

Hai kiểu so, dùng cho hai mục đích khác nhau:

1. So với input người nhập (match_name, match_course): tách chuỗi thành tập hợp
   TỪ rồi so tuyệt đối, bỏ qua thứ tự. Xử lý được đảo thứ tự surname/given name
   ("A NGUYEN VAN" = "Nguyen Van A"). So chặt từng từ, không chịu lỗi OCR trong
   từ — đây là lựa chọn ưu tiên độ chặt.

2. identical_after_normalize(): so kết quả LLM1 với LLM2.
   So CHẶT (== sau chuẩn hóa) vì cả hai đều là MÁY đọc cùng một ảnh — không có
   yếu tố gõ tay. Hai máy đọc ra y hệt nhau mới là bằng chứng đồng thuận đáng
   tin. Nới lỏng ở đây sẽ làm hỏng chính mục đích của bước này.

Riêng match_code(): so mã NV tuyệt đối, tách từng từ (chứng chỉ in username/ID).
"""

from process_data import normalize

def same_word_set(a: str | None, b: str | None) -> bool:
    """So hai chuỗi bằng cách tách thành tập hợp TỪ, so tuyệt đối, bỏ qua thứ tự.

    Xử lý ca đảo thứ tự tên do khác cách dịch surname/given name:
      "A NGUYEN VAN" vs "NGUYEN VAN A" -> cùng tập {a, nguyen, van} -> khớp.

    So CHẶT (==) từng từ: mỗi từ phải đúng tuyệt đối sau chuẩn hóa. Không chịu
    lỗi OCR trong từ ("nguyen" khác "nguyeen"). Đây là lựa chọn ưu tiên độ chặt.

    Chuỗi rỗng không khớp (tránh khớp giả khi cả hai cùng rỗng).
    """
    words_a = normalize(a).split()
    words_b = normalize(b).split()
    if not words_a or not words_b:
        return False
    # So tap hop: cung cac tu, bat ke thu tu va so lan lap.
    return set(words_a) == set(words_b)


def identical_after_normalize(value_1: str | None, value_2: str | None) -> bool:
    """So kết quả LLM1 với LLM2. So CHẶT: bằng nhau tuyệt đối sau chuẩn hóa.

    Hai chuỗi rỗng KHÔNG được coi là giống nhau — nếu cả hai model đều không
    đọc ra gì thì đó là 'cùng không biết', không phải 'cùng đồng thuận'.
    """
    a, b = normalize(value_1), normalize(value_2)
    if not a or not b:
        return False
    return a == b


def match_code(name_on_image, employee_code):
    """So MÃ nhân viên với tên trên ảnh — CHẶT tuyệt đối, so từng từ.

    Một số chứng chỉ in username/ID thay cho tên thật (vd "hungnt97 hungnt97").
    Mã NV là chuỗi máy nên phải khớp CHÍNH XÁC: "hungnt97" khác "hungnt98" là
    hai người. Không dùng fuzzy.

    So từng từ vì ID hay bị lặp ("hungnt97 hungnt97") hoặc lẫn với chữ khác.
    Chỉ cần MỘT từ trên ảnh trùng khớp tuyệt đối mã NV là đủ.
    """
    code = normalize(employee_code)
    image = normalize(name_on_image)
    if not code or not image:
        return False
    # Mã có thể là một từ (hungnt97) hoặc nhiều từ sau chuẩn hóa (nv-001 -> "nv 001").
    # Kiểm tra mã có xuất hiện như một CỤM TỪ liên tiếp trong tên trên ảnh không.
    # Bọc khoảng trắng hai đầu để khớp trọn từ, tránh khớp một phần
    # (vd mã "nv" không khớp nhầm với "nvidia").
    code_words = code.split()
    image_words = image.split()
    n = len(code_words)
    for i in range(len(image_words) - n + 1):
        if image_words[i:i + n] == code_words:
            return True
    return False


def match_name(llm_value, input_value):
    """So trường TÊN NGƯỜI với input.

    Dùng so tập hợp từ (bỏ qua thứ tự) để xử lý đảo surname/given name.
    So chặt tuyệt đối từng từ.
    """
    return same_word_set(llm_value, input_value)


def match_name_or_code(name_on_image, employee_name, employee_code):
    """Tên trên ảnh khớp nếu khớp TÊN nhân viên HOẶC MÃ nhân viên.

    - Khớp tên: so tập hợp từ (bỏ qua thứ tự, chặt từng từ).
    - Khớp mã: tuyệt đối, so từng từ (ID là chuỗi máy).
    Chỉ cần một trong hai đúng là tính khớp.
    """
    if match_name(name_on_image, employee_name):
        return True
    if match_code(name_on_image, employee_code):
        return True
    return False


def input_is_subset(input_value, image_value):
    """True khi MỌI từ người nhập đều có trong tên trên ảnh (ảnh được phép thừa).

    Dùng cho chế độ "loose": nhập "khóa học code online" khớp ảnh "khóa học code
    online (code-bc-06)" vì mọi từ nhập đều nằm trong ảnh.

    An toàn hơn substring: "Python nâng cao" (nhập) KHÔNG khớp "Python" (ảnh)
    vì "nâng"/"cao" không có trong ảnh. Chỉ chấp nhận ảnh thừa, không nhập thừa.
    """
    input_words = set(normalize(input_value).split())
    image_words = set(normalize(image_value).split())
    if not input_words or not image_words:
        return False
    return input_words.issubset(image_words)


def match_course(image_value, input_value, mode="strict"):
    """So trường TÊN KHÓA HỌC với input.

    mode="strict": trùng khớp hoàn toàn (cùng tập từ).
    mode="loose": người nhập chỉ cần là TẬP CON của tên trên ảnh (ảnh thừa OK).
    """
    if mode == "loose":
        return input_is_subset(input_value, image_value)
    return same_word_set(image_value, input_value)


def match_course_bilingual(primary, secondary, input_value, mode="strict"):
    """So tên khóa học với input, chấp nhận cả hai ngôn ngữ.

    Dùng khi ảnh in tên khóa song ngữ: LLM tách thành 'chinh' và 'phu'
    (hai ngôn ngữ). Người nhập chỉ một ngôn ngữ, nên khớp với BẤT KỲ phần nào
    cũng tính là khớp.

    Ví dụ: ảnh "An toàn thông tin / Information Security"
      chinh = "An toàn thông tin", phu = "Information Security"
      - Người nhập "An toàn thông tin" -> khớp phần chính -> True
      - Người nhập "Information Security" -> khớp phần phụ -> True
    """
    if match_course(primary, input_value, mode):
        return True
    if match_course(secondary, input_value, mode):
        return True
    return False