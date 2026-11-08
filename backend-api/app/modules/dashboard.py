"""API cho Manager Dashboard: doanh thu, hiệu suất sản phẩm, xu hướng mua sắm, hiệu suất giao hàng.
Query thẳng vào OLAP views (dw.mv_*) / fact — không gọi qua DSS service vì đây là số liệu thuần thống kê.
Tiền tệ: BRL (Olist). Chỉ tính đơn không bị huỷ (canceled/unavailable).
"""
import pandas as pd
from fastapi import APIRouter, Query
from sqlalchemy import text

from app.db import engine

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/revenue-trend")
def revenue_trend(year: int | None = None, category: str | None = None, region: str | None = None):
    query = """
        SELECT year, month, category, region, state, total_revenue, total_quantity, total_orders
        FROM dw.mv_revenue_by_month_category WHERE 1=1
    """
    params = {}
    if year:
        query += " AND year = :year"
        params["year"] = year
    if category:
        query += " AND category = :category"
        params["category"] = category
    if region:
        query += " AND region = :region"
        params["region"] = region
    query += " ORDER BY year, month"
    df = pd.read_sql(text(query), engine, params=params)
    return df.to_dict(orient="records")


@router.get("/top-products")
def top_products(limit: int = Query(10, le=100), ascending: bool = False):
    """ascending=true -> sản phẩm bán chậm nhất (theo doanh thu)."""
    order = "ASC" if ascending else "DESC"
    df = pd.read_sql(
        text(f"SELECT * FROM dw.mv_top_products ORDER BY total_revenue {order} LIMIT :limit"),
        engine, params={"limit": limit},
    )
    return df.to_dict(orient="records")


@router.get("/kpi-summary")
def kpi_summary():
    """Doanh thu / số đơn / AOV theo dòng sản phẩm (fact_order_item); giao trễ và review tính THEO ĐƠN
    (fact_order_fulfillment) để khớp phần Giao hàng của dashboard và mô hình DSS."""
    row = pd.read_sql(text("""
        SELECT r.total_revenue, r.total_orders, r.total_revenue / NULLIF(r.total_orders, 0) AS aov,
               o.avg_review_score, o.late_delivery_rate
        FROM (SELECT SUM(price) AS total_revenue, COUNT(DISTINCT order_id) AS total_orders
              FROM dw.fact_order_item WHERE order_status NOT IN ('canceled', 'unavailable')) r,
             (SELECT AVG(review_score) AS avg_review_score,
                     AVG(is_late::INT) FILTER (WHERE delivered_ts IS NOT NULL) AS late_delivery_rate
              FROM dw.fact_order_fulfillment WHERE order_status NOT IN ('canceled', 'unavailable')) o
    """), engine)
    return row.to_dict(orient="records")[0]


@router.get("/delivery-trend")
def delivery_trend(region: str | None = None):
    """Theo tháng mua: tỷ lệ giao trễ, số ngày giao TB, review TB, tỷ lệ review thấp (đơn đã giao)."""
    query = """
        SELECT year, month,
               SUM(delivered_orders)                                       AS delivered_orders,
               SUM(late_orders)::FLOAT / SUM(delivered_orders)             AS late_rate,
               SUM(sum_actual_days) / SUM(delivered_orders)                AS avg_delivery_days,
               SUM(sum_review_score) / NULLIF(SUM(reviewed_orders), 0)     AS avg_review_score,
               SUM(low_review_orders)::FLOAT / NULLIF(SUM(reviewed_orders), 0) AS low_review_rate
        FROM dw.mv_delivery_performance WHERE 1=1
    """
    params = {}
    if region:
        query += " AND customer_region = :region"
        params["region"] = region
    query += " GROUP BY year, month HAVING SUM(delivered_orders) >= 100 ORDER BY year, month"
    return pd.read_sql(text(query), engine, params=params).to_dict(orient="records")


@router.get("/delivery-routes")
def delivery_routes(min_orders: int = 200, limit: int = Query(10, le=50)):
    """Tuyến (bang người bán -> bang khách) có tỷ lệ giao trễ cao nhất, chỉ xét tuyến đủ nhiều đơn."""
    df = pd.read_sql(text("""
        SELECT seller_state, customer_state,
               SUM(delivered_orders)                                   AS delivered_orders,
               SUM(late_orders)::FLOAT / SUM(delivered_orders)         AS late_rate,
               SUM(sum_actual_days) / SUM(delivered_orders)            AS avg_delivery_days,
               SUM(sum_estimated_days) / SUM(delivered_orders)         AS avg_estimated_days,
               SUM(low_review_orders)::FLOAT / NULLIF(SUM(reviewed_orders), 0) AS low_review_rate
        FROM dw.mv_delivery_performance
        GROUP BY seller_state, customer_state
        HAVING SUM(delivered_orders) >= :min_orders
        ORDER BY late_rate DESC LIMIT :limit
    """), engine, params={"min_orders": min_orders, "limit": limit})
    return df.to_dict(orient="records")


@router.get("/review-by-delay")
def review_by_delay():
    """Điểm review theo mức trễ/sớm so với ngày hẹn — cho thấy giao trễ kéo điểm review xuống thế nào."""
    df = pd.read_sql(text("""
        SELECT CASE
                   WHEN delay_days <= -10 THEN 1 WHEN delay_days <= -1 THEN 2 WHEN delay_days = 0 THEN 3
                   WHEN delay_days <= 3 THEN 4 WHEN delay_days <= 7 THEN 5 ELSE 6
               END AS bucket_order,
               CASE
                   WHEN delay_days <= -10 THEN 'Sớm >= 10 ngày' WHEN delay_days <= -1 THEN 'Sớm 1-9 ngày'
                   WHEN delay_days = 0 THEN 'Đúng ngày hẹn' WHEN delay_days <= 3 THEN 'Trễ 1-3 ngày'
                   WHEN delay_days <= 7 THEN 'Trễ 4-7 ngày' ELSE 'Trễ > 7 ngày'
               END AS bucket,
               COUNT(*)                          AS orders,
               AVG(review_score)                 AS avg_review_score,
               AVG(is_low_review::INT)           AS low_review_rate
        FROM dw.fact_order_fulfillment
        WHERE delivered_ts IS NOT NULL AND review_score IS NOT NULL
        GROUP BY 1, 2 ORDER BY 1
    """), engine)
    return df.drop(columns="bucket_order").to_dict(orient="records")
