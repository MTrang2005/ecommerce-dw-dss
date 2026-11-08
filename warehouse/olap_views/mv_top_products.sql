-- Xếp hạng sản phẩm theo doanh thu (đọc ORDER BY total_revenue DESC/ASC cho bán chạy/bán chậm).
-- Olist không có tên sản phẩm -> hiển thị bằng product_id + category.
CREATE MATERIALIZED VIEW IF NOT EXISTS dw.mv_top_products AS
SELECT
    p.product_sk,
    p.product_id,
    p.category,
    SUM(f.price)                AS total_revenue,
    SUM(f.quantity)             AS total_quantity,
    COUNT(DISTINCT f.order_id)  AS total_orders,
    AVG(f.review_score)         AS avg_review_score
FROM dw.fact_order_item f
JOIN dw.dim_product p ON f.product_sk = p.product_sk
WHERE f.order_status NOT IN ('canceled', 'unavailable')
GROUP BY p.product_sk, p.product_id, p.category;
