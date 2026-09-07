import re

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
    """So tên khóa học với input, chấp nhận cả hai ngôn ngữ VÀ bản ghép hai nửa.

    Ảnh in tên khóa song ngữ -> LLM tách làm hai phần (primary/secondary).
    Nhưng eLIS lưu tên khóa ở CẢ BA dạng khác nhau, tùy khóa:

        chỉ tiếng Việt : "Bộ Quy định chính sách cần biết FPT"
        chỉ tiếng Anh  : "FPT Key Regulations and Policies"
        CẢ HAI nối lại : "Bộ Quy định chính sách cần biết FPT - FPT Key
                          Regulations and Policies (English version)"

    Nên phải thử ĐỦ BA cách, khớp một cách là đủ:
      1. nửa thứ nhất  -> bắt ca eLIS lưu một ngôn ngữ
      2. nửa thứ hai   -> bắt ca eLIS lưu ngôn ngữ kia
      3. GHÉP hai nửa  -> bắt ca eLIS lưu cả hai

    Thiếu bước 3 là lỗi đã xảy ra thật: chứng chỉ TIENLX6 in đúng nguyên chuỗi
    song ngữ mà eLIS lưu, model tách làm hai theo đúng yêu cầu, rồi KHÔNG nửa
    nào bằng chuỗi eLIS nữa -> từ chối oan một chứng chỉ hợp lệ, đọc đúng.

    Chỉ NỚI THÊM đường khớp, không bỏ đường nào: một ca đang APPROVED không
    thể vì thay đổi này mà thành REJECTED.
    """
    if match_course(primary, input_value, mode):
        return True
    if match_course(secondary, input_value, mode):
        return True
    # Ghép hai nửa. Thứ tự không quan trọng vì cả hai chế độ so đều làm việc
    # trên TẬP HỢP TỪ, không theo vị trí.
    if primary and secondary:
        if match_course(f"{primary} {secondary}", input_value, mode):
            return True
    return False


# ===== Chứng chỉ ghi email thay cho tên người nhận =====
#
# Nhân viên học khóa ngoài (Coursera, Udemy...) thường đăng ký bằng email cá
# nhân, nên chứng chỉ in "minhnt4487@gmail.com" thay vì tên. Không có cách nào
# nối chuỗi đó với nhân viên nào cả -> không xác minh được danh tính.
#
# Email CÔNG TY thì ngược lại, tự khớp: normalize() cắt "@" và "." thành khoảng
# trắng nên "doannv19@fpt.com" thành "doannv19 fpt com", và match_code tìm thấy
# mã nhân viên nằm trong đó. Vì vậy chỉ đuôi NGOÀI công ty mới cần xử lý riêng.
COMPANY_EMAIL_DOMAIN = "fpt.com"

_EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def external_email(name_on_image: str | None) -> str | None:
    """Trả về ĐUÔI email ngoài công ty in trên ảnh, hoặc None.

    Chạy trên chuỗi GỐC, trước normalize(): normalize cắt mất dấu "@" nên sau
    đó không còn phân biệt được email với tên người nữa.

    None có hai nghĩa khác nhau — "không có email nào" và "có email công ty" —
    và người gọi đối xử với hai ca đó giống nhau: cứ so tên/mã như bình thường.
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

    "Lê Tiến" so với "Lê Xuân Tiến": mọi từ trên ảnh đều có trong tên eLIS,
    nhưng ảnh thiếu "xuân". Nhà cấp chứng chỉ ngoài thường in tên người học tự
    gõ lúc đăng ký, mà người Việt hay bỏ tên đệm khi gõ.

    Dùng tập con THỰC SỰ (<) chứ không phải <=: hai tập bằng nhau thì match_name
    đã bắt từ trước, tới được đây nghĩa là chắc chắn có chênh lệch.

    KHÔNG bắt chiều ngược lại (ảnh thừa từ so với eLIS). Ảnh thừa từ có thể là
    chức danh, có thể là tên người khác in kèm — hai thứ rất khác nhau, không
    quy về một luật được.
    """
    image = set(normalize(name_on_image).split())
    given = set(normalize(employee_name).split())
    if not image or not given:
        return False
    return image < given
