-- Staging schema: landing tables 1-1 với file CSV nguồn, chưa có surrogate key.
-- Nguồn chính: Brazilian E-Commerce Public Dataset by Olist (Kaggle), 2016-09 -> 2018-10.
-- Lấy các cột cần cho DW + vài thuộc tính nguồn chưa dùng (độ dài tên/mô tả, số ảnh sản phẩm, ...) để bổ sung
-- vào dimension khi cần (báo cáo Mục 6.6). Tên cột giữ nguyên như file gốc (kể cả lỗi chính tả "lenght").
CREATE SCHEMA IF NOT EXISTS staging;

-- olist_orders_dataset.csv
CREATE TABLE IF NOT EXISTS staging.olist_orders (
    order_id                       TEXT NOT NULL,
    customer_id                    TEXT NOT NULL,   -- khoá theo đơn, KHÔNG phải khách hàng thật
    order_status                   TEXT,
    order_purchase_timestamp       TIMESTAMP,
    order_approved_at              TIMESTAMP,
    order_delivered_carrier_date   TIMESTAMP,
    order_delivered_customer_date  TIMESTAMP,
    order_estimated_delivery_date  TIMESTAMP,
    loaded_at                      TIMESTAMP NOT NULL DEFAULT now()
);

-- olist_customers_dataset.csv
CREATE TABLE IF NOT EXISTS staging.olist_customers (
    customer_id               TEXT NOT NULL,
    customer_unique_id        TEXT NOT NULL,        -- khoá nghiệp vụ của khách hàng thật
    customer_zip_code_prefix  TEXT,
    customer_city             TEXT,
    customer_state            TEXT,
    loaded_at                 TIMESTAMP NOT NULL DEFAULT now()
);

-- olist_order_items_dataset.csv (1 dòng = 1 đơn vị sản phẩm trong đơn)
CREATE TABLE IF NOT EXISTS staging.olist_order_items (
    order_id             TEXT NOT NULL,
    order_item_id        INT NOT NULL,              -- số thứ tự item trong đơn (1, 2, 3...)
    product_id           TEXT NOT NULL,
    seller_id            TEXT,
    shipping_limit_date  TIMESTAMP,
    price                NUMERIC(12,2),
    freight_value        NUMERIC(12,2),
    loaded_at            TIMESTAMP NOT NULL DEFAULT now()
);

-- olist_order_payments_dataset.csv (1 đơn có thể có nhiều dòng thanh toán)
CREATE TABLE IF NOT EXISTS staging.olist_order_payments (
    order_id              TEXT NOT NULL,
    payment_sequential    INT,
    payment_type          TEXT,                     -- credit_card | boleto | voucher | debit_card | not_defined
    payment_installments  INT,
    payment_value         NUMERIC(12,2),
    loaded_at             TIMESTAMP NOT NULL DEFAULT now()
);

-- olist_order_reviews_dataset.csv (review_id không duy nhất, 1 đơn có thể có nhiều review)
CREATE TABLE IF NOT EXISTS staging.olist_order_reviews (
    review_id             TEXT NOT NULL,
    order_id              TEXT NOT NULL,
    review_score          INT,
    review_creation_date  TIMESTAMP,
    loaded_at             TIMESTAMP NOT NULL DEFAULT now()
);

-- olist_products_dataset.csv
CREATE TABLE IF NOT EXISTS staging.olist_products (
    product_id                  TEXT NOT NULL,
    product_category_name       TEXT,               -- tiếng Bồ Đào Nha, có NULL
    product_name_lenght         INT,
    product_description_lenght  INT,
    product_photos_qty          INT,
    product_weight_g            INT,
    product_length_cm           INT,
    product_height_cm           INT,
    product_width_cm            INT,
    loaded_at                   TIMESTAMP NOT NULL DEFAULT now()
);

-- olist_sellers_dataset.csv
CREATE TABLE IF NOT EXISTS staging.olist_sellers (
    seller_id               TEXT NOT NULL,
    seller_zip_code_prefix  TEXT,
    seller_city             TEXT,
    seller_state            TEXT,
    loaded_at               TIMESTAMP NOT NULL DEFAULT now()
);

-- olist_geolocation_dataset.csv (~1 triệu dòng, nhiều toạ độ cho 1 zip prefix -> gom TB ở bước Transform)
CREATE TABLE IF NOT EXISTS staging.olist_geolocation (
    geolocation_zip_code_prefix  TEXT NOT NULL,
    geolocation_lat              DOUBLE PRECISION,
    geolocation_lng              DOUBLE PRECISION,
    geolocation_city             TEXT,
    geolocation_state            TEXT,
    loaded_at                    TIMESTAMP NOT NULL DEFAULT now()
);

-- product_category_name_translation.csv
CREATE TABLE IF NOT EXISTS staging.product_category_translation (
    product_category_name          TEXT NOT NULL,
    product_category_name_english  TEXT,
    loaded_at                      TIMESTAMP NOT NULL DEFAULT now()
);
