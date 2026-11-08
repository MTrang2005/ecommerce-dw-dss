import os

from sqlalchemy import create_engine

DB_URL = (
    f"postgresql+psycopg2://{os.getenv('DW_DB_USER', 'dw_user')}:{os.getenv('DW_DB_PASSWORD', 'change_me')}"
    f"@{os.getenv('DW_DB_HOST', 'localhost')}:{os.getenv('DW_DB_PORT', '5433')}/{os.getenv('DW_DB_NAME', 'ecommerce_dw')}"
)
DSS_BASE_URL = os.getenv("DSS_BASE_URL", "http://localhost:8001")

engine = create_engine(DB_URL, pool_pre_ping=True)
