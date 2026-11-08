-- Hiệu suất giao hàng + mức độ hài lòng theo tháng mua x tuyến (bang người bán -> bang khách).
-- Chỉ tính đơn đã giao tới khách. Phục vụ Manager Dashboard (logistics) và câu hỏi nghiệp vụ (3).
CREATE MATERIALIZED VIEW IF NOT EXISTS dw.mv_delivery_performance AS
SELECT
    d.year,
    d.month,
    s.state                                   AS seller_state,
    l.region                                  AS customer_region,
    l.state                                   AS customer_state,
    COUNT(*)                                  AS delivered_orders,
    COUNT(*) FILTER (WHERE f.is_late)         AS late_orders,
    SUM(f.actual_days)                        AS sum_actual_days,
    SUM(f.estimated_days)                     AS sum_estimated_days,
    COUNT(f.review_score)                     AS reviewed_orders,
    SUM(f.review_score)                       AS sum_review_score,
    COUNT(*) FILTER (WHERE f.is_low_review)   AS low_review_orders
FROM dw.fact_order_fulfillment f
JOIN dw.dim_date d          ON d.date_sk = f.purchase_date_sk
LEFT JOIN dw.dim_seller s   ON s.seller_sk = f.seller_sk
LEFT JOIN dw.dim_location l ON l.location_sk = f.location_sk
WHERE f.delivered_ts IS NOT NULL
GROUP BY d.year, d.month, s.state, l.region, l.state;
-- Lưu tổng (SUM/COUNT) thay vì trung bình để rollup lên tháng / tuyến / vùng vẫn đúng.

CREATE INDEX IF NOT EXISTS idx_mv_delivery_ym ON dw.mv_delivery_performance(year, month);
