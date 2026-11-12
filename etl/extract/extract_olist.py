"""Extract: nạp 9 file CSV Olist (data/raw/) vào schema `staging`.

Chỉ đổi tên/chọn cột, không transform nghiệp vụ. Mỗi lần chạy TRUNCATE bảng staging rồi COPY lại
(full reload) -> chạy lại nhiều lần không bị nhân đôi dữ liệu.
"""
import io

import pandas as pd

from etl.common import DATA_DIR, transaction

# staging table -> (đường dẫn tương đối trong data/, danh sách cột cần lấy)
SOURCES: dict[str, tuple[str, list[str]]] = {
    "olist_orders": ("raw/olist_orders_dataset.csv", [
        "order_id", "customer_id", "order_status", "order_purchase_timestamp", "order_approved_at",
        "order_delivered_carrier_date", "order_delivered_customer_date", "order_estimated_delivery_date",
    ]),
    "olist_customers": ("raw/olist_customers_dataset.csv", [
        "customer_id", "customer_unique_id", "customer_zip_code_prefix", "customer_city", "customer_state",
    ]),
    "olist_order_items": ("raw/olist_order_items_dataset.csv", [
        "order_id", "order_item_id", "product_id", "seller_id", "shipping_limit_date", "price", "freight_value",
    ]),
    "olist_order_payments": ("raw/olist_order_payments_dataset.csv", [
        "order_id", "payment_sequential", "payment_type", "payment_installments", "payment_value",
    ]),
    "olist_order_reviews": ("raw/olist_order_reviews_dataset.csv", [
        "review_id", "order_id", "review_score", "review_creation_date",
    ]),
    "olist_products": ("raw/olist_products_dataset.csv", [
        "product_id", "product_category_name", "product_name_lenght", "product_description_lenght",
        "product_photos_qty", "product_weight_g", "product_length_cm", "product_height_cm", "product_width_cm",
    ]),
    "olist_sellers": ("raw/olist_sellers_dataset.csv", [
        "seller_id", "seller_zip_code_prefix", "seller_city", "seller_state",
    ]),
    "olist_geolocation": ("raw/olist_geolocation_dataset.csv", [
        "geolocation_zip_code_prefix", "geolocation_lat", "geolocation_lng", "geolocation_city", "geolocation_state",
    ]),
    "product_category_translation": ("raw/product_category_name_translation.csv", [
        "product_category_name", "product_category_name_english",
    ]),
}


def load_table(table: str):
    """TRUNCATE + COPY 1 file CSV vào staging.<table>.

    Đọc mọi cột dạng chuỗi (dtype=str) để giữ nguyên số 0 đầu của zip code và để Postgres tự ép kiểu.
    Ô trống -> NULL (COPY ... FORMAT csv).
    """
    rel_path, columns = SOURCES[table]
    csv_path = DATA_DIR / rel_path
    # utf-8-sig: file translation của Olist có BOM ở header
    df = pd.read_csv(csv_path, dtype=str, usecols=columns, encoding="utf-8-sig")[columns]

    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False)
    buf.seek(0)

    with transaction() as cur:
        cur.execute(f"TRUNCATE staging.{table}")
        cur.copy_expert(f"COPY staging.{table} ({', '.join(columns)}) FROM STDIN WITH (FORMAT csv)", buf)
    print(f"Loaded {len(df):>8} rows into staging.{table} from {rel_path}")


def load_all():
    for table in SOURCES:
        load_table(table)


if __name__ == "__main__":
    load_all()
