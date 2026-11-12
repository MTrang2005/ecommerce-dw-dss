#!/usr/bin/env bash
# Chạy toàn bộ pipeline lần đầu từ máy local: tạo schema -> staging -> DW -> kiểm tra DQ.
# Yêu cầu: `docker compose up -d db` (infra/), Python có pandas + psycopg2 (pip install -r etl/requirements.txt),
# và các file Olist đã giải nén trong data/raw/ (8 file olist_*.csv + product_category_name_translation.csv).
# Biến môi trường DW_DB_* mặc định khớp infra/.env.example (localhost:5433, dw_user/change_me, ecommerce_dw).
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> [1/5] Tạo schema staging + dw (idempotent)"
python -m etl.init_db

echo "==> [2/5] Load CSV Olist vào staging"
python -m etl.extract.extract_olist

echo "==> [3/5] Transform: Dim_Date, Zip geo, Location, Seller, Payment, Product, Customer (SCD2 địa chỉ)"
python -m etl.transform.transform_dw

echo "==> [4/5] Load Fact tables + tạo/refresh OLAP views"
python -m etl.load.load_dw

echo "==> [5/5] Kiểm tra chất lượng dữ liệu"
python -m pytest -q etl/tests

echo "Xong. Train mô hình rủi ro trong container dss: curl -X POST localhost:8001/risk/retrain"
