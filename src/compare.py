"""Module so sánh (compare).

Chứa các luật khớp đã chốt. Mọi chuỗi đều được đưa qua process_data.chuan_hoa()
trước khi so, nên ở đây không lặp lại việc chuẩn hóa.

Hai kiểu so, dùng cho hai mục đích khác nhau:

1. So với input người nhập (khop_ten, khop_khoa_hoc): tách chuỗi thành tập hợp
   TỪ rồi so tuyệt đối, bỏ qua thứ tự. Xử lý được đảo thứ tự surname/given name
   ("A NGUYEN VAN" = "Nguyen Van A"). So chặt từng từ, không chịu lỗi OCR trong
   từ — đây là lựa chọn ưu tiên độ chặt.

2. hai_ket_qua_giong_nhau(): so kết quả LLM1 với LLM2.
   So CHẶT (== sau chuẩn hóa) vì cả hai đều là MÁY đọc cùng một ảnh — không có
   yếu tố gõ tay. Hai máy đọc ra y hệt nhau mới là bằng chứng đồng thuận đáng
   tin. Nới lỏng ở đây sẽ làm hỏng chính mục đích của bước này.

Riêng khop_ma(): so mã NV tuyệt đối, tách từng từ (chứng chỉ in username/ID).
"""

from process_data import chuan_hoa

def giong_tap_hop_tu(a: str | None, b: str | None) -> bool:
    """So hai chuỗi bằng cách tách thành tập hợp TỪ, so tuyệt đối, bỏ qua thứ tự.

    Xử lý ca đảo thứ tự tên do khác cách dịch surname/given name:
      "A NGUYEN VAN" vs "NGUYEN VAN A" -> cùng tập {a, nguyen, van} -> khớp.

    So CHẶT (==) từng từ: mỗi từ phải đúng tuyệt đối sau chuẩn hóa. Không chịu
    lỗi OCR trong từ ("nguyen" khác "nguyeen"). Đây là lựa chọn ưu tiên độ chặt.

    Chuỗi rỗng không khớp (tránh khớp giả khi cả hai cùng rỗng).
    """
    ta = chuan_hoa(a).split()
    tb = chuan_hoa(b).split()
    if not ta or not tb:
        return False
    # So tap hop: cung cac tu, bat ke thu tu va so lan lap.
    return set(ta) == set(tb)


def hai_ket_qua_giong_nhau(gia_tri_1: str | None, gia_tri_2: str | None) -> bool:
    """So kết quả LLM1 với LLM2. So CHẶT: bằng nhau tuyệt đối sau chuẩn hóa.

    Hai chuỗi rỗng KHÔNG được coi là giống nhau — nếu cả hai model đều không
    đọc ra gì thì đó là 'cùng không biết', không phải 'cùng đồng thuận'.
    """
    a, b = chuan_hoa(gia_tri_1), chuan_hoa(gia_tri_2)
    if not a or not b:
        return False
    return a == b


def khop_ma(ten_tren_anh, ma_nhan_vien):
    """So MÃ nhân viên với tên trên ảnh — CHẶT tuyệt đối, so từng từ.

    Một số chứng chỉ in username/ID thay cho tên thật (vd "hungnt97 hungnt97").
    Mã NV là chuỗi máy nên phải khớp CHÍNH XÁC: "hungnt97" khác "hungnt98" là
    hai người. Không dùng fuzzy.

    So từng từ vì ID hay bị lặp ("hungnt97 hungnt97") hoặc lẫn với chữ khác.
    Chỉ cần MỘT từ trên ảnh trùng khớp tuyệt đối mã NV là đủ.
    """
    ma = chuan_hoa(ma_nhan_vien)
    anh = chuan_hoa(ten_tren_anh)
    if not ma or not anh:
        return False
    # Mã có thể là một từ (hungnt97) hoặc nhiều từ sau chuẩn hóa (nv-001 -> "nv 001").
    # Kiểm tra mã có xuất hiện như một CỤM TỪ liên tiếp trong tên trên ảnh không.
    # Bọc khoảng trắng hai đầu để khớp trọn từ, tránh khớp một phần
    # (vd mã "nv" không khớp nhầm với "nvidia").
    tu_ma = ma.split()
    tu_anh = anh.split()
    n = len(tu_ma)
    for i in range(len(tu_anh) - n + 1):
        if tu_anh[i:i + n] == tu_ma:
            return True
    return False


def khop_ten(gia_tri_llm, gia_tri_nhap):
    """So trường TÊN NGƯỜI với input.

    Dùng so tập hợp từ (bỏ qua thứ tự) để xử lý đảo surname/given name.
    So chặt tuyệt đối từng từ.
    """
    return giong_tap_hop_tu(gia_tri_llm, gia_tri_nhap)


def khop_ten_hoac_ma(ten_tren_anh, ten_nhan_vien, ma_nhan_vien):
    """Tên trên ảnh khớp nếu khớp TÊN nhân viên HOẶC MÃ nhân viên.

    - Khớp tên: so tập hợp từ (bỏ qua thứ tự, chặt từng từ).
    - Khớp mã: tuyệt đối, so từng từ (ID là chuỗi máy).
    Chỉ cần một trong hai đúng là tính khớp.
    """
    if khop_ten(ten_tren_anh, ten_nhan_vien):
        return True
    if khop_ma(ten_tren_anh, ma_nhan_vien):
        return True
    return False


def input_la_tap_con(gia_tri_nhap, gia_tri_anh):
    """True khi MỌI từ người nhập đều có trong tên trên ảnh (ảnh được phép thừa).

    Dùng cho chế độ "long": nhập "khóa học code online" khớp ảnh "khóa học code
    online (code-bc-06)" vì mọi từ nhập đều nằm trong ảnh.

    An toàn hơn substring: "Python nâng cao" (nhập) KHÔNG khớp "Python" (ảnh)
    vì "nâng"/"cao" không có trong ảnh. Chỉ chấp nhận ảnh thừa, không nhập thừa.
    """
    tu_nhap = set(chuan_hoa(gia_tri_nhap).split())
    tu_anh = set(chuan_hoa(gia_tri_anh).split())
    if not tu_nhap or not tu_anh:
        return False
    return tu_nhap.issubset(tu_anh)


def khop_khoa_hoc(gia_tri_anh, gia_tri_nhap, che_do="chat"):
    """So trường TÊN KHÓA HỌC với input.

    che_do="chat": trùng khớp hoàn toàn (cùng tập từ).
    che_do="long": người nhập chỉ cần là TẬP CON của tên trên ảnh (ảnh thừa OK).
    """
    if che_do == "long":
        return input_la_tap_con(gia_tri_nhap, gia_tri_anh)
    return giong_tap_hop_tu(gia_tri_anh, gia_tri_nhap)


def khop_khoa_hoc_song_ngu(chinh, phu, gia_tri_nhap, che_do="chat"):
    """So tên khóa học với input, chấp nhận cả hai ngôn ngữ.

    Dùng khi ảnh in tên khóa song ngữ: LLM tách thành 'chinh' và 'phu'
    (hai ngôn ngữ). Người nhập chỉ một ngôn ngữ, nên khớp với BẤT KỲ phần nào
    cũng tính là khớp.

    Ví dụ: ảnh "An toàn thông tin / Information Security"
      chinh = "An toàn thông tin", phu = "Information Security"
      - Người nhập "An toàn thông tin" -> khớp phần chính -> True
      - Người nhập "Information Security" -> khớp phần phụ -> True
    """
    if khop_khoa_hoc(chinh, gia_tri_nhap, che_do):
        return True
    if khop_khoa_hoc(phu, gia_tri_nhap, che_do):
        return True
    return False