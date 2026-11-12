"""Transform: staging (Olist) -> các bảng Dimension của DW.

Toàn bộ là SQL set-based (INSERT ... SELECT / ON CONFLICT) nên chạy lại nhiều lần vẫn cho cùng kết quả.
Fact được build ở etl/load/load_dw.py sau khi các dimension đã sẵn sàng.
"""
from etl.common import transaction


def build_dim_date():
    """Sinh Dim_Date phủ trọn các năm có ngày mua hoặc ngày hẹn giao.

    Olist: 2016-09 -> 2018-10, nên dim_date = 2016-01-01 -> 2018-12-31.
    """
    with transaction() as cur:
        cur.execute("""
            WITH bounds AS (
                SELECT MIN(ts) AS min_ts, MAX(ts) AS max_ts FROM (
                    SELECT order_purchase_timestamp AS ts FROM staging.olist_orders
                    UNION ALL SELECT order_estimated_delivery_date FROM staging.olist_orders
                ) t
            )
            INSERT INTO dw.dim_date (date_sk, full_date, day_of_week, month, year)
            SELECT
                TO_CHAR(d, 'YYYYMMDD')::INT,
                d::DATE,
                EXTRACT(ISODOW FROM d)::INT,
                EXTRACT(MONTH FROM d)::INT,
                EXTRACT(YEAR FROM d)::INT
            FROM bounds,
                 generate_series(DATE_TRUNC('year', bounds.min_ts),
                                 DATE_TRUNC('year', bounds.max_ts) + INTERVAL '1 year - 1 day',
                                 INTERVAL '1 day') AS d
            ON CONFLICT (date_sk) DO NOTHING
        """)
        print(f"Dim_Date: +{cur.rowcount} days")


def build_ref_zip_geo():
    """Toạ độ TB theo zip prefix. Bỏ các điểm nằm ngoài khung Brazil (dataset có vài toạ độ lỗi)."""
    with transaction() as cur:
        cur.execute("""
            INSERT INTO dw.ref_zip_geo (zip_code_prefix, state, lat, lng)
            SELECT geolocation_zip_code_prefix, MODE() WITHIN GROUP (ORDER BY geolocation_state),
                   AVG(geolocation_lat), AVG(geolocation_lng)
            FROM staging.olist_geolocation
            WHERE geolocation_lat BETWEEN -34 AND 5.5 AND geolocation_lng BETWEEN -74 AND -34
            GROUP BY geolocation_zip_code_prefix
            ON CONFLICT (zip_code_prefix) DO UPDATE SET
                state = EXCLUDED.state, lat = EXCLUDED.lat, lng = EXCLUDED.lng
        """)
        print(f"Ref_Zip_Geo upserted: {cur.rowcount} zip prefixes")


def upsert_dim_location():
    with transaction() as cur:
        cur.execute("""
            INSERT INTO dw.dim_location (city, state, region)
            SELECT DISTINCT c.customer_city, c.customer_state, s.region
            FROM staging.olist_customers c
            LEFT JOIN dw.ref_br_state s ON s.state_code = c.customer_state
            WHERE c.customer_city IS NOT NULL AND c.customer_state IS NOT NULL
            ON CONFLICT (city, state) DO UPDATE SET region = EXCLUDED.region
        """)
        print(f"Dim_Location upserted: {cur.rowcount} rows")


def upsert_dim_seller():
    with transaction() as cur:
        cur.execute("""
            INSERT INTO dw.dim_seller (seller_id, state)
            SELECT DISTINCT ON (s.seller_id) s.seller_id, s.seller_state
            FROM staging.olist_sellers s
            ORDER BY s.seller_id
            ON CONFLICT (seller_id) DO UPDATE SET state = EXCLUDED.state
        """)
        print(f"Dim_Seller upserted: {cur.rowcount} rows")


def upsert_dim_payment_type():
    with transaction() as cur:
        cur.execute("""
            INSERT INTO dw.dim_payment_type (payment_type)
            SELECT DISTINCT payment_type FROM staging.olist_order_payments WHERE payment_type IS NOT NULL
            ON CONFLICT (payment_type) DO NOTHING
        """)
        print(f"Dim_Payment_Type: +{cur.rowcount} rows")


def upsert_dim_product():
    """SCD Type 1. Dịch category sang tiếng Anh; thiếu bản dịch -> giữ tên gốc; NULL -> 'unknown'."""
    with transaction() as cur:
        cur.execute("""
            INSERT INTO dw.dim_product (product_id, category)
            SELECT DISTINCT ON (p.product_id)
                p.product_id,
                COALESCE(t.product_category_name_english, p.product_category_name, 'unknown')
            FROM staging.olist_products p
            LEFT JOIN staging.product_category_translation t
                   ON t.product_category_name = p.product_category_name
            ORDER BY p.product_id
            ON CONFLICT (product_id) DO UPDATE SET category = EXCLUDED.category
        """)
        print(f"Dim_Product upserted (Type 1): {cur.rowcount} rows")


def upsert_dim_customer_scd2():
    """Dim_Customer SCD Type 2 theo địa chỉ, business key = customer_unique_id.

    Olist sinh customer_id mới cho MỖI đơn và ghi địa chỉ giao hàng theo đơn. Duyệt các đơn của 1 khách
    theo thời gian: mỗi lần (zip, city, state) khác đơn trước -> 1 version mới, effective_from = THỜI ĐIỂM mua
    (không lấy ngày: khách có thể đặt 2 đơn tới 2 địa chỉ trong cùng một ngày).
    Version đầu có effective_from = 1900-01-01 để mọi đơn lịch sử đều khớp. Chạy lại không tạo trùng
    (unique (customer_id, effective_from)); đơn mới ở địa chỉ mới sẽ thêm version và đóng version cũ.
    """
    with transaction() as cur:
        cur.execute("""
            WITH addr AS (
                SELECT c.customer_unique_id AS customer_id, c.customer_zip_code_prefix AS zip,
                       c.customer_city AS city, c.customer_state AS state,
                       o.order_purchase_timestamp AS ts,
                       LAG((c.customer_zip_code_prefix, c.customer_city, c.customer_state)::TEXT)
                           OVER w AS prev_addr,
                       ROW_NUMBER() OVER w AS rn
                FROM staging.olist_customers c
                JOIN staging.olist_orders o ON o.customer_id = c.customer_id
                WINDOW w AS (PARTITION BY c.customer_unique_id ORDER BY o.order_purchase_timestamp, o.order_id)
            )
            INSERT INTO dw.dim_customer (customer_id, zip_code_prefix, city, state,
                                         effective_from, effective_to, is_current)
            SELECT customer_id, zip, city, state,
                   CASE WHEN rn = 1 THEN TIMESTAMP '1900-01-01' ELSE ts END, NULL, FALSE
            FROM addr
            WHERE rn = 1 OR prev_addr IS DISTINCT FROM (zip, city, state)::TEXT
            ON CONFLICT (customer_id, effective_from) DO NOTHING
        """)
        inserted = cur.rowcount
        # Đóng các version đã có version sau (đóng trước, mở sau để không vi phạm unique "1 version hiện hành")
        cur.execute("""
            WITH v AS (
                SELECT customer_sk,
                       LEAD(effective_from) OVER (PARTITION BY customer_id ORDER BY effective_from) AS next_from
                FROM dw.dim_customer
            )
            UPDATE dw.dim_customer d SET effective_to = v.next_from, is_current = FALSE
            FROM v
            WHERE d.customer_sk = v.customer_sk AND v.next_from IS NOT NULL
              AND (d.effective_to IS DISTINCT FROM v.next_from OR d.is_current)
        """)
        closed = cur.rowcount
        cur.execute("""
            WITH v AS (
                SELECT customer_sk,
                       LEAD(effective_from) OVER (PARTITION BY customer_id ORDER BY effective_from) AS next_from
                FROM dw.dim_customer
            )
            UPDATE dw.dim_customer d SET effective_to = NULL, is_current = TRUE
            FROM v
            WHERE d.customer_sk = v.customer_sk AND v.next_from IS NULL AND NOT d.is_current
        """)
        print(f"Dim_Customer (SCD2 địa chỉ): +{inserted} versions, {closed} versions closed")


def run_all():
    build_dim_date()
    build_ref_zip_geo()
    upsert_dim_location()
    upsert_dim_seller()
    upsert_dim_payment_type()
    upsert_dim_product()
    upsert_dim_customer_scd2()


if __name__ == "__main__":
    run_all()
