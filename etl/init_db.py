"""Tạo schema staging + dw (DDL idempotent, CREATE ... IF NOT EXISTS).

Docker Postgres tự chạy warehouse/ddl/ khi volume còn trống; script này dùng khi DB đã tồn tại.
Lưu ý: IF NOT EXISTS không sửa bảng cũ — nếu đổi DDL thì `docker compose down -v` để tạo lại.
"""
from etl.common import WAREHOUSE_DIR, run_sql_file


def create_schema():
    for path in sorted((WAREHOUSE_DIR / "ddl").glob("*.sql")):
        run_sql_file(path)


if __name__ == "__main__":
    create_schema()
