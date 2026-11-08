"""Kết nối DW dùng chung cho các module DSS."""
import os
from functools import lru_cache

from psycopg2.extras import execute_values
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

DB_URL = (
    f"postgresql+psycopg2://{os.getenv('DW_DB_USER', 'dw_user')}:{os.getenv('DW_DB_PASSWORD', 'change_me')}"
    f"@{os.getenv('DW_DB_HOST', 'localhost')}:{os.getenv('DW_DB_PORT', '5433')}/{os.getenv('DW_DB_NAME', 'ecommerce_dw')}"
)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(DB_URL, pool_pre_ping=True)


def bulk_execute(statements: list[tuple[str, tuple | list[tuple]]]):
    """Chạy nhiều câu lệnh trong 1 transaction.
    (sql, tuple)       -> 1 câu lệnh với tham số, vd DELETE ... WHERE model_name = %s
    (sql, list[tuple]) -> INSERT ... VALUES %s bằng execute_values (nhanh hơn nhiều so với insert từng dòng)"""
    conn = get_engine().raw_connection()
    try:
        with conn.cursor() as cur:
            for sql, rows in statements:
                if isinstance(rows, tuple):
                    cur.execute(sql, rows)
                elif rows:
                    execute_values(cur, sql, rows, page_size=5000)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
