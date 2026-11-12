"""Load: build Fact_Order_Item / Fact_Order_Fulfillment từ staging + các Dim đã
có surrogate key, rồi tạo/refresh OLAP materialized views.

Idempotent: fact có khoá duy nhất (order_id, order_item_id) / order_id + ON CONFLICT.
Fact_Order_Fulfillment là accumulating snapshot nên ON CONFLICT DO UPDATE (đơn đi tới mốc mới -> cập nhật).
"""
from etl.common import WAREHOUSE_DIR, run_sql_file, transaction

OLAP_VIEWS = ["mv_revenue_by_month_category", "mv_top_products", "mv_delivery_performance"]

# Point-in-time join với Dim_Customer (SCD2): lấy version có hiệu lực tại THỜI ĐIỂM phát sinh sự kiện.
CUSTOMER_VERSION_JOIN = """
    JOIN dw.dim_customer dc
      ON dc.customer_id = {bk}
     AND {ts} >= dc.effective_from
     AND ({ts} < dc.effective_to OR dc.effective_to IS NULL)
"""

# CTE dùng chung cho 2 fact: 1 đơn nhiều dòng thanh toán -> loại có giá trị lớn nhất; nhiều review -> điểm TB
ORDER_CTES = """
    main_payment AS (
        SELECT DISTINCT ON (order_id) order_id, payment_type, payment_installments
        FROM staging.olist_order_payments
        ORDER BY order_id, payment_value DESC, payment_sequential
    ),
    order_review AS (
        SELECT order_id, AVG(review_score)::NUMERIC(3,2) AS review_score
        FROM staging.olist_order_reviews
        GROUP BY order_id
    )
"""


def _date_sk(ts: str) -> str:
    return f"TO_CHAR({ts}, 'YYYYMMDD')::INT"


def _days(later: str, earlier: str, unit_seconds: int = 86400) -> str:
    """Khoảng cách giữa 2 timestamp theo ngày (hoặc giờ với unit_seconds=3600), làm tròn 2 chữ số."""
    return f"ROUND((EXTRACT(EPOCH FROM ({later} - {earlier})) / {unit_seconds})::NUMERIC, 2)"


def _haversine_km(lat1: str, lng1: str, lat2: str, lng2: str) -> str:
    """Khoảng cách đường chim bay (km) giữa 2 toạ độ."""
    return f"""ROUND((2 * 6371 * ASIN(SQRT(
        POWER(SIN(RADIANS({lat2} - {lat1}) / 2), 2)
        + COS(RADIANS({lat1})) * COS(RADIANS({lat2})) * POWER(SIN(RADIANS({lng2} - {lng1}) / 2), 2)
    )))::NUMERIC, 1)"""


# Giao trễ = ngày giao thực tế sau ngày hẹn (so theo NGÀY, bỏ giờ); NULL khi chưa giao
IS_LATE = "o.order_delivered_customer_date::DATE > o.order_estimated_delivery_date::DATE"


def load_fact_order_item():
    with transaction() as cur:
        cur.execute("WITH " + ORDER_CTES + """
            INSERT INTO dw.fact_order_item
                (order_id, order_item_id, order_status, date_sk, customer_sk, product_sk, seller_sk,
                 location_sk, payment_type_sk, quantity, price, freight_value, total_value,
                 delivery_days, is_late_delivery, review_score)
            SELECT
                o.order_id,
                oi.order_item_id,
                o.order_status,
        """ + _date_sk("o.order_purchase_timestamp") + """,
                dc.customer_sk,
                dp.product_sk,
                ds.seller_sk,
                dl.location_sk,
                dpt.payment_type_sk,
                1,                                              -- mỗi dòng order_items = 1 đơn vị
                oi.price,
                COALESCE(oi.freight_value, 0),
                oi.price + COALESCE(oi.freight_value, 0),
        """ + _days("o.order_delivered_customer_date", "o.order_purchase_timestamp") + ",\n" + IS_LATE + """,
                r.review_score
            FROM staging.olist_orders o
            JOIN staging.olist_order_items oi ON oi.order_id = o.order_id
            JOIN staging.olist_customers sc   ON sc.customer_id = o.customer_id
        """ + CUSTOMER_VERSION_JOIN.format(bk="sc.customer_unique_id", ts="o.order_purchase_timestamp") + """
            JOIN dw.dim_product dp            ON dp.product_id = oi.product_id
            LEFT JOIN dw.dim_seller ds        ON ds.seller_id = oi.seller_id
            LEFT JOIN dw.dim_location dl      ON dl.city = sc.customer_city AND dl.state = sc.customer_state
            LEFT JOIN main_payment mp         ON mp.order_id = o.order_id
            LEFT JOIN dw.dim_payment_type dpt ON dpt.payment_type = mp.payment_type
            LEFT JOIN order_review r          ON r.order_id = o.order_id
            -- chạy lại: cập nhật các cột đổi theo vòng đời đơn (giao hàng, review) và version khách SCD2
            ON CONFLICT (order_id, order_item_id) DO UPDATE SET
                order_status = EXCLUDED.order_status, customer_sk = EXCLUDED.customer_sk,
                delivery_days = EXCLUDED.delivery_days, is_late_delivery = EXCLUDED.is_late_delivery,
                review_score = EXCLUDED.review_score
            WHERE (dw.fact_order_item.order_status, dw.fact_order_item.customer_sk, dw.fact_order_item.delivery_days,
                   dw.fact_order_item.is_late_delivery, dw.fact_order_item.review_score)
                  IS DISTINCT FROM
                  (EXCLUDED.order_status, EXCLUDED.customer_sk, EXCLUDED.delivery_days, EXCLUDED.is_late_delivery,
                   EXCLUDED.review_score)
        """)
        print(f"Fact_Order_Item: {cur.rowcount} rows inserted/updated")


def load_fact_order_fulfillment():
    """1 dòng / đơn. Các mốc thời gian + độ đo cấp đơn + nhãn (is_late, is_low_review) cho DSS."""
    with transaction() as cur:
        cur.execute("""
            WITH items AS (
                SELECT oi.order_id,
                       COUNT(*)                                   AS n_items,
                       COUNT(DISTINCT oi.seller_id)               AS n_sellers,
                       SUM(oi.price)                              AS total_price,
                       SUM(COALESCE(oi.freight_value, 0))         AS total_freight,
                       SUM(p.product_weight_g)                    AS total_weight_g,
                       SUM(p.product_length_cm::BIGINT * p.product_height_cm * p.product_width_cm) AS total_volume_cm3,
                       MAX(oi.shipping_limit_date)                AS shipping_limit
                FROM staging.olist_order_items oi
                LEFT JOIN staging.olist_products p ON p.product_id = oi.product_id
                GROUP BY oi.order_id
            ),
            first_item AS (                 -- đơn nhiều người bán (~1%) -> lấy item đầu tiên làm đại diện
                SELECT DISTINCT ON (order_id) order_id, product_id, seller_id
                FROM staging.olist_order_items
                ORDER BY order_id, order_item_id
            ),
        """ + ORDER_CTES + """,
            state_geo AS (
                SELECT state, AVG(lat) AS lat, AVG(lng) AS lng FROM dw.ref_zip_geo GROUP BY state
            ),
            src AS (
                SELECT o.*, sc.customer_unique_id, sc.customer_city, sc.customer_state, sc.customer_zip_code_prefix,
                       i.n_items, i.n_sellers, i.total_price, i.total_freight, i.total_weight_g, i.total_volume_cm3,
                       i.shipping_limit, fi.product_id, fi.seller_id, ss.seller_state,
                       mp.payment_type, mp.payment_installments, r.review_score,
                       COALESCE(cz.lat, cs.lat) AS c_lat, COALESCE(cz.lng, cs.lng) AS c_lng,
                       COALESCE(sz.lat, sst.lat) AS s_lat, COALESCE(sz.lng, sst.lng) AS s_lng
                FROM staging.olist_orders o
                JOIN items i                    ON i.order_id = o.order_id
                JOIN first_item fi              ON fi.order_id = o.order_id
                JOIN staging.olist_customers sc ON sc.customer_id = o.customer_id
                LEFT JOIN staging.olist_sellers ss ON ss.seller_id = fi.seller_id
                LEFT JOIN main_payment mp       ON mp.order_id = o.order_id
                LEFT JOIN order_review r        ON r.order_id = o.order_id
                LEFT JOIN dw.ref_zip_geo cz     ON cz.zip_code_prefix = sc.customer_zip_code_prefix
                LEFT JOIN state_geo cs          ON cs.state = sc.customer_state
                LEFT JOIN dw.ref_zip_geo sz     ON sz.zip_code_prefix = ss.seller_zip_code_prefix
                LEFT JOIN state_geo sst         ON sst.state = ss.seller_state
            )
            INSERT INTO dw.fact_order_fulfillment
                (order_id, order_status, customer_sk, location_sk, seller_sk, product_sk, payment_type_sk,
                 purchase_date_sk, approved_date_sk, carrier_date_sk, delivered_date_sk, estimated_date_sk,
                 purchase_ts, approved_ts, carrier_ts, delivered_ts, estimated_date,
                 n_items, n_sellers, total_price, total_freight, total_weight_g, total_volume_cm3,
                 payment_installments, distance_km, same_state,
                 estimated_days, shipping_limit_days, approval_hours, carrier_days, actual_days, delay_days,
                 is_late, review_score, is_low_review)
            SELECT
                o.order_id, o.order_status, dc.customer_sk, dl.location_sk, ds.seller_sk, dp.product_sk,
                dpt.payment_type_sk,
        """ + ", ".join(_date_sk(c) for c in ["o.order_purchase_timestamp", "o.order_approved_at",
                                               "o.order_delivered_carrier_date", "o.order_delivered_customer_date",
                                               "o.order_estimated_delivery_date"]) + """,
                o.order_purchase_timestamp, o.order_approved_at, o.order_delivered_carrier_date,
                o.order_delivered_customer_date, o.order_estimated_delivery_date::DATE,
                o.n_items, o.n_sellers, o.total_price, o.total_freight, o.total_weight_g, o.total_volume_cm3,
                o.payment_installments,
        """ + _haversine_km("o.s_lat", "o.s_lng", "o.c_lat", "o.c_lng") + """,
                o.seller_state = o.customer_state,
                o.order_estimated_delivery_date::DATE - o.order_purchase_timestamp::DATE,
        """ + ",\n".join([_days("o.shipping_limit", "o.order_purchase_timestamp"),
                           _days("o.order_approved_at", "o.order_purchase_timestamp", unit_seconds=3600),
                           _days("o.order_delivered_carrier_date", "o.order_purchase_timestamp"),
                           _days("o.order_delivered_customer_date", "o.order_purchase_timestamp")]) + """,
                o.order_delivered_customer_date::DATE - o.order_estimated_delivery_date::DATE,
        """ + IS_LATE + """,
                o.review_score,
                o.review_score <= 2
            FROM src o
        """ + CUSTOMER_VERSION_JOIN.format(bk="o.customer_unique_id", ts="o.order_purchase_timestamp") + """
            LEFT JOIN dw.dim_location dl      ON dl.city = o.customer_city AND dl.state = o.customer_state
            LEFT JOIN dw.dim_seller ds        ON ds.seller_id = o.seller_id
            LEFT JOIN dw.dim_product dp       ON dp.product_id = o.product_id
            LEFT JOIN dw.dim_payment_type dpt ON dpt.payment_type = o.payment_type
            ON CONFLICT (order_id) DO UPDATE SET
                order_status = EXCLUDED.order_status, customer_sk = EXCLUDED.customer_sk,
                approved_date_sk = EXCLUDED.approved_date_sk, approved_ts = EXCLUDED.approved_ts,
                carrier_date_sk = EXCLUDED.carrier_date_sk, carrier_ts = EXCLUDED.carrier_ts,
                delivered_date_sk = EXCLUDED.delivered_date_sk, delivered_ts = EXCLUDED.delivered_ts,
                approval_hours = EXCLUDED.approval_hours, carrier_days = EXCLUDED.carrier_days,
                actual_days = EXCLUDED.actual_days, delay_days = EXCLUDED.delay_days, is_late = EXCLUDED.is_late,
                review_score = EXCLUDED.review_score, is_low_review = EXCLUDED.is_low_review,
                updated_at = now()
            WHERE (dw.fact_order_fulfillment.order_status, dw.fact_order_fulfillment.approved_ts,
                   dw.fact_order_fulfillment.carrier_ts, dw.fact_order_fulfillment.delivered_ts,
                   dw.fact_order_fulfillment.review_score, dw.fact_order_fulfillment.customer_sk)
                  IS DISTINCT FROM
                  (EXCLUDED.order_status, EXCLUDED.approved_ts, EXCLUDED.carrier_ts, EXCLUDED.delivered_ts,
                   EXCLUDED.review_score, EXCLUDED.customer_sk)
        """)
        print(f"Fact_Order_Fulfillment: {cur.rowcount} rows inserted/updated")


def create_olap_views():
    for view in OLAP_VIEWS:
        run_sql_file(WAREHOUSE_DIR / "olap_views" / f"{view}.sql")


def refresh_olap_views():
    with transaction() as cur:
        for view in OLAP_VIEWS:
            cur.execute(f"REFRESH MATERIALIZED VIEW dw.{view}")
    print("OLAP views refreshed")


def run_all():
    load_fact_order_item()
    load_fact_order_fulfillment()
    create_olap_views()
    refresh_olap_views()


if __name__ == "__main__":
    run_all()
