-- Rollup doanh thu theo tháng x ngành hàng x bang/vùng (chỉ tính đơn không bị huỷ).
CREATE MATERIALIZED VIEW IF NOT EXISTS dw.mv_revenue_by_month_category AS
SELECT
    d.year,
    d.month,
    p.category,
    l.region,
    l.state,
    SUM(f.price)                      AS total_revenue,
    SUM(f.freight_value)              AS total_freight,
    SUM(f.quantity)                   AS total_quantity,
    COUNT(DISTINCT f.order_id)        AS total_orders
FROM dw.fact_order_item f
JOIN dw.dim_date d     ON f.date_sk = d.date_sk
JOIN dw.dim_product p  ON f.product_sk = p.product_sk
LEFT JOIN dw.dim_location l ON f.location_sk = l.location_sk
WHERE f.order_status NOT IN ('canceled', 'unavailable')
GROUP BY d.year, d.month, p.category, l.region, l.state;

CREATE INDEX IF NOT EXISTS idx_mv_rev_ym ON dw.mv_revenue_by_month_category(year, month);
-- REFRESH MATERIALIZED VIEW dw.mv_revenue_by_month_category; -- chạy lại sau mỗi lần ETL DW
