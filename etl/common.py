"""Kết nối DB + đường dẫn dùng chung cho ETL.

Dùng psycopg2 trực tiếp (không qua SQLAlchemy) để chạy được cả trong image Airflow 2.9
(SQLAlchemy 1.4) lẫn môi trường local.
"""
import os
from contextlib import contextmanager
from pathlib import Path

import psycopg2

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("DATA_DIR", REPO_ROOT / "data"))
WAREHOUSE_DIR = Path(os.getenv("WAREHOUSE_DIR", REPO_ROOT / "warehouse"))


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DW_DB_HOST", "localhost"),
        port=int(os.getenv("DW_DB_PORT", "5433")),
        dbname=os.getenv("DW_DB_NAME", "ecommerce_dw"),
        user=os.getenv("DW_DB_USER", "dw_user"),
        password=os.getenv("DW_DB_PASSWORD", "change_me"),
    )


@contextmanager
def transaction():
    """Mở connection, commit nếu thành công / rollback nếu lỗi, luôn đóng connection."""
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                yield cur
    finally:
        conn.close()


def run_sql_file(path: Path):
    with transaction() as cur:
        cur.execute(path.read_text(encoding="utf-8"))
    print(f"Executed {path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path}")
