"""Bộ đánh giá chất lượng hệ thống MOOC (evaluation).

Hai phần đo hai thứ khác nhau, cố ý tách rời:

  PHẦN 1 — extraction_score: model ĐỌC ảnh có đúng không (độ chính xác theo trường).
  PHẦN 2 — decision_score : model QUYẾT ĐỊNH có đúng không (precision/recall/F1).

Vì sao phải tách: hai lỗi khác hẳn nhau nhưng nhìn từ ngoài giống nhau.
Một chứng chỉ bị từ chối oan có thể vì model đọc sai tên (lỗi trích xuất),
hoặc vì đọc đúng nhưng luật so khớp quá chặt (lỗi phê duyệt). Gộp làm một
con số thì không biết phải sửa prompt hay sửa compare.py.

Dùng:
    python -m evaluation.run_eval mau     # tạo file nhãn rỗng
    python -m evaluation.run_eval chay    # chạy và chấm điểm
"""
