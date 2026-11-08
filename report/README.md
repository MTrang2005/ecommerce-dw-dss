# Báo cáo LaTeX — CO4031 Bài tập lớn

Format theo mẫu báo cáo của khoa (bìa có logo BK, header/footer fancyhdr, font tiếng Việt qua
`vietnam.sty`, biên dịch bằng `pdflatex`).

## Cấu trúc

```
report/
  main.tex                       # File chính: bìa, mục lục, header/footer, gộp các \input
  logoBK.png                     # Logo trường
  contents/
    Tong_quan.tex                 # Tổng quan đề tài + đối chiếu CĐR
    Yeu_cau_nghiep_vu.tex         # Phân tích yêu cầu nghiệp vụ
    Thiet_ke_du_lieu.tex          # Thiết kế kho dữ liệu (star schema, ETL, OLAP)
    Hien_thuc_he_thong.tex        # Hiện thực hệ thống (backend, frontend, infra)
    He_ho_tro_quyet_dinh.tex      # Hệ hỗ trợ quyết định (rủi ro giao trễ / rủi ro review thấp)
    Danh_gia_ket_qua.tex          # Kết quả thực nghiệm, số liệu đánh giá
    Phan_cong.tex                 # [CẦN ĐIỀN tên + %] Phân công công việc theo thành viên
  Ket_luan.tex                    # Kết luận + hướng phát triển
  Picture/                        # Biểu đồ (PDF, sinh bằng scripts/make_figures.py) + ảnh chụp UI
  scripts/make_figures.py         # Vẽ lại biểu đồ chương 6 từ DB thật (cổng 5433)
  Makefile
```

## Biên dịch

```bash
cd report
make            # pdflatex 2 lần để dựng mục lục/tham chiếu đúng
```

hoặc thủ công: `pdflatex main.tex && pdflatex main.tex`

Dọn file tạm: `make clean`

## Việc còn cần làm

- Trang bìa (`main.tex`): số nhóm, MSSV, danh sách thành viên; link video nếu nộp qua video.
- `contents/Phan_cong.tex`: tên thành viên + % đóng góp thực tế.

## Vẽ lại biểu đồ

Sau khi train lại mô hình (DAG `dss_batch`), chạy `python3 report/scripts/make_figures.py` từ thư mục gốc
rồi `make` để cập nhật các hình ở chương 6 (cần matplotlib, scikit-learn, psycopg2).

Nội dung các phần còn lại (tổng quan, yêu cầu nghiệp vụ, thiết kế kho dữ liệu, hiện thực hệ
thống, DSS, đánh giá, kết luận) được viết dựa trên mã nguồn thực tế của dự án (`docs/`, `warehouse/`, `etl/`,
`dss/`, `backend-api/`, `frontend/`) — không phải nội dung mẫu.
