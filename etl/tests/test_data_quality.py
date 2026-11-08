"""Data quality checks cho staging (Olist) và DW. Chạy: `pytest etl/tests/` (cần DB đã nạp dữ liệu)."""
import pytest

from etl.common import get_conn


@pytest.fixture(scope="module")
def cur():
    conn = get_conn()
    with conn.cursor() as c:
        yield c
    conn.close()


def scalar(cur, sql: str):
    cur.execute(sql)
    return cur.fetchone()[0]


# ---------- staging ----------

def test_staging_not_empty(cur):
    for table in ["olist_orders", "olist_order_items", "olist_customers", "olist_products"]:
        assert scalar(cur, f"SELECT COUNT(*) FROM staging.{table}") > 0, f"staging.{table} rỗng"


def test_no_null_business_keys(cur):
    assert scalar(cur, """
        SELECT COUNT(*) FROM staging.olist_orders
        WHERE order_id IS NULL OR customer_id IS NULL OR order_purchase_timestamp IS NULL
    """) == 0
    assert scalar(cur, "SELECT COUNT(*) FROM staging.olist_order_items WHERE order_id IS NULL OR product_id IS NULL") == 0
    assert scalar(cur, "SELECT COUNT(*) FROM staging.olist_customers WHERE customer_unique_id IS NULL") == 0


def test_no_duplicate_order_items(cur):
    assert scalar(cur, """
        SELECT COUNT(*) FROM (
            SELECT order_id, order_item_id FROM staging.olist_order_items
            GROUP BY order_id, order_item_id HAVING COUNT(*) > 1
        ) d
    """) == 0


def test_order_items_reference_valid_orders(cur):
    assert scalar(cur, """
        SELECT COUNT(*) FROM staging.olist_order_items oi
        LEFT JOIN staging.olist_orders o ON o.order_id = oi.order_id
        WHERE o.order_id IS NULL
    """) == 0


def test_orders_reference_valid_customers(cur):
    assert scalar(cur, """
        SELECT COUNT(*) FROM staging.olist_orders o
        LEFT JOIN staging.olist_customers c ON c.customer_id = o.customer_id
        WHERE c.customer_id IS NULL
    """) == 0


def test_prices_non_negative(cur):
    assert scalar(cur, "SELECT COUNT(*) FROM staging.olist_order_items WHERE price < 0 OR freight_value < 0") == 0


def test_review_scores_in_range(cur):
    assert scalar(cur, "SELECT COUNT(*) FROM staging.olist_order_reviews WHERE review_score NOT BETWEEN 1 AND 5") == 0


# ---------- DW ----------

def test_all_order_items_loaded_into_fact(cur):
    staging_rows = scalar(cur, "SELECT COUNT(*) FROM staging.olist_order_items")
    fact_rows = scalar(cur, "SELECT COUNT(*) FROM dw.fact_order_item")
    assert fact_rows == staging_rows, f"fact={fact_rows} != staging={staging_rows} — có dòng bị rơi khi join dimension"


def test_fact_has_location_and_payment(cur):
    assert scalar(cur, "SELECT COUNT(*) FROM dw.fact_order_item WHERE location_sk IS NULL") == 0
    # 1 đơn Olist không có dòng thanh toán nào -> cho phép rất ít NULL
    assert scalar(cur, "SELECT COUNT(*) FROM dw.fact_order_item WHERE payment_type_sk IS NULL") <= 5


def test_one_current_version_per_customer(cur):
    assert scalar(cur, """
        SELECT COUNT(*) FROM (
            SELECT customer_id FROM dw.dim_customer GROUP BY customer_id HAVING SUM(is_current::INT) <> 1
        ) d
    """) == 0


def test_dim_customer_uses_unique_id(cur):
    assert scalar(cur, "SELECT COUNT(DISTINCT customer_id) FROM dw.dim_customer") == \
        scalar(cur, "SELECT COUNT(DISTINCT customer_unique_id) FROM staging.olist_customers")


def test_scd2_versions_do_not_overlap(cur):
    # version sau phải bắt đầu đúng thời điểm version trước kết thúc (không chồng lấn, không hở)
    assert scalar(cur, """
        SELECT COUNT(*) FROM (
            SELECT effective_to,
                   LEAD(effective_from) OVER (PARTITION BY customer_id ORDER BY effective_from) AS next_from
            FROM dw.dim_customer
        ) v WHERE next_from IS NOT NULL AND effective_to IS DISTINCT FROM next_from
    """) == 0


def test_fact_joins_customer_version_of_order_address(cur):
    # point-in-time join: version khách mà fact trỏ tới phải có đúng địa chỉ giao của đơn
    # (bắt lỗi khách đặt 2 đơn tới 2 địa chỉ trong cùng một ngày)
    for fact in ["fact_order_fulfillment", "fact_order_item"]:
        assert scalar(cur, f"""
            SELECT COUNT(*) FROM dw.{fact} f
            JOIN staging.olist_orders o ON o.order_id = f.order_id
            JOIN staging.olist_customers c ON c.customer_id = o.customer_id
            JOIN dw.dim_customer v ON v.customer_sk = f.customer_sk
            WHERE (c.customer_zip_code_prefix, c.customer_city, c.customer_state)
                  IS DISTINCT FROM (v.zip_code_prefix, v.city, v.state)
        """) == 0, fact


def test_fulfillment_one_row_per_order_with_items(cur):
    assert scalar(cur, "SELECT COUNT(*) FROM dw.fact_order_fulfillment") == \
        scalar(cur, "SELECT COUNT(DISTINCT order_id) FROM staging.olist_order_items")


def test_fulfillment_labels_consistent(cur):
    # is_late phải khớp delay_days; đơn chưa giao thì chưa có nhãn
    assert scalar(cur, """
        SELECT COUNT(*) FROM dw.fact_order_fulfillment
        WHERE (delivered_ts IS NULL AND is_late IS NOT NULL)
           OR (delivered_ts IS NOT NULL AND is_late IS DISTINCT FROM (delay_days > 0))
           OR (is_low_review IS DISTINCT FROM (review_score <= 2))
    """) == 0


def test_fulfillment_has_distance(cur):
    # thiếu zip trong geolocation -> dùng tâm bang, nên mọi đơn đều phải có khoảng cách
    assert scalar(cur, "SELECT COUNT(*) FROM dw.fact_order_fulfillment WHERE distance_km IS NULL") == 0
