-- Grain: 1 dòng = 1 đơn vị sản phẩm trong 1 đơn hàng (= 1 dòng olist_order_items).
CREATE TABLE IF NOT EXISTS dw.fact_order_item (
    order_item_sk     BIGSERIAL PRIMARY KEY,
    order_id          TEXT NOT NULL,          -- degenerate dimension
    order_item_id     INT NOT NULL,
    order_status      TEXT NOT NULL,          -- degenerate dimension
    date_sk           INT NOT NULL REFERENCES dw.dim_date(date_sk),     -- ngày mua
    customer_sk       INT NOT NULL REFERENCES dw.dim_customer(customer_sk),
    product_sk        INT NOT NULL REFERENCES dw.dim_product(product_sk),
    seller_sk         INT REFERENCES dw.dim_seller(seller_sk),
    location_sk       INT REFERENCES dw.dim_location(location_sk),
    payment_type_sk   INT REFERENCES dw.dim_payment_type(payment_type_sk),
    quantity          INT NOT NULL DEFAULT 1,
    price             NUMERIC(12,2) NOT NULL, -- doanh thu sản phẩm (BRL)
    freight_value     NUMERIC(12,2) NOT NULL DEFAULT 0,
    total_value       NUMERIC(14,2) NOT NULL, -- price + freight_value
    delivery_days     NUMERIC(6,2),           -- NULL nếu chưa giao
    is_late_delivery  BOOLEAN,                -- giao sau ngày dự kiến
    review_score      NUMERIC(3,2),           -- điểm review trung bình của đơn
    UNIQUE (order_id, order_item_id)
);
CREATE INDEX IF NOT EXISTS idx_foi_date ON dw.fact_order_item(date_sk);
CREATE INDEX IF NOT EXISTS idx_foi_customer ON dw.fact_order_item(customer_sk);
CREATE INDEX IF NOT EXISTS idx_foi_product ON dw.fact_order_item(product_sk);
CREATE INDEX IF NOT EXISTS idx_foi_location ON dw.fact_order_item(location_sk);

-- Grain: 1 dòng = 1 ĐƠN HÀNG. Accumulating snapshot: mỗi mốc của vòng đời đơn (mua -> duyệt -> bàn
-- giao vận chuyển -> giao tới khách) là 1 cột ngày; dòng được CẬP NHẬT khi đơn đi tới mốc tiếp theo.
-- Đây là nguồn dữ liệu cho 2 mô hình DSS (rủi ro giao trễ, rủi ro review thấp).
CREATE TABLE IF NOT EXISTS dw.fact_order_fulfillment (
    order_id              TEXT PRIMARY KEY,       -- degenerate dimension
    order_status          TEXT NOT NULL,
    customer_sk           INT NOT NULL REFERENCES dw.dim_customer(customer_sk),
    location_sk           INT REFERENCES dw.dim_location(location_sk),
    seller_sk             INT REFERENCES dw.dim_seller(seller_sk),        -- người bán của item đầu tiên
    product_sk            INT REFERENCES dw.dim_product(product_sk),      -- sản phẩm của item đầu tiên
    payment_type_sk       INT REFERENCES dw.dim_payment_type(payment_type_sk),
    -- các mốc thời gian (role-playing dim_date)
    purchase_date_sk      INT NOT NULL REFERENCES dw.dim_date(date_sk),
    approved_date_sk      INT REFERENCES dw.dim_date(date_sk),
    carrier_date_sk       INT REFERENCES dw.dim_date(date_sk),
    delivered_date_sk     INT REFERENCES dw.dim_date(date_sk),
    estimated_date_sk     INT REFERENCES dw.dim_date(date_sk),
    purchase_ts           TIMESTAMP NOT NULL,
    approved_ts           TIMESTAMP,
    carrier_ts            TIMESTAMP,
    delivered_ts          TIMESTAMP,
    estimated_date        DATE,
    -- độ đo của đơn
    n_items               INT NOT NULL,
    n_sellers             INT NOT NULL,
    total_price           NUMERIC(12,2) NOT NULL,
    total_freight         NUMERIC(12,2) NOT NULL,
    total_weight_g        INT,
    total_volume_cm3      BIGINT,
    payment_installments  INT,
    distance_km           NUMERIC(8,1),           -- người bán -> khách (haversine theo zip prefix)
    same_state            BOOLEAN,
    -- độ đo thời gian (ngày / giờ, tính từ lúc mua)
    estimated_days        INT,                    -- ngày hẹn giao - ngày mua
    shipping_limit_days   NUMERIC(6,2),           -- hạn người bán phải bàn giao
    approval_hours        NUMERIC(8,2),
    carrier_days          NUMERIC(6,2),           -- NULL nếu chưa bàn giao vận chuyển
    actual_days           NUMERIC(6,2),           -- NULL nếu chưa giao tới khách
    delay_days            INT,                    -- ngày giao thực tế - ngày hẹn (>0 = trễ)
    is_late               BOOLEAN,
    review_score          NUMERIC(3,2),
    is_low_review         BOOLEAN,                -- review_score <= 2
    updated_at            TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_fof_purchase ON dw.fact_order_fulfillment(purchase_date_sk);
CREATE INDEX IF NOT EXISTS idx_fof_seller ON dw.fact_order_fulfillment(seller_sk);

-- ===== DSS output tables =====

-- Điểm rủi ro theo đơn của từng mô hình (ghi lại toàn bộ mỗi lần retrain).
-- scope = 'holdout' (đơn trong giai đoạn kiểm định, biết kết quả thật -> actual_label)
--       | 'open'    (đơn đang vận chuyển, chưa giao — chỉ có ở mô hình giao trễ)
CREATE TABLE IF NOT EXISTS dw.order_risk_score (
    order_id        TEXT NOT NULL REFERENCES dw.fact_order_fulfillment(order_id),
    model_name      TEXT NOT NULL,          -- late_delivery | low_review
    model_version   TEXT NOT NULL,
    risk_score      NUMERIC(6,4) NOT NULL,  -- xác suất dự đoán
    risk_level      TEXT NOT NULL,          -- High | Medium | Low
    scope           TEXT NOT NULL,
    actual_label    BOOLEAN,
    scored_at       TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (order_id, model_name)
);
CREATE INDEX IF NOT EXISTS idx_ors_model_score ON dw.order_risk_score(model_name, risk_score DESC);

-- Kết quả đánh giá trên tập kiểm định theo thời gian (time-based split)
CREATE TABLE IF NOT EXISTS dw.model_metrics (
    model_name        TEXT NOT NULL,
    model_version     TEXT NOT NULL,        -- hgb_v1 (mô hình chính) | logreg_v1 (baseline)
    is_primary        BOOLEAN NOT NULL,
    train_start       DATE NOT NULL,
    train_end         DATE NOT NULL,
    test_start        DATE NOT NULL,
    test_end          DATE NOT NULL,
    n_train           INT NOT NULL,
    n_test            INT NOT NULL,
    positive_rate     NUMERIC(6,4),         -- tỷ lệ nhãn dương trên tập test
    roc_auc           NUMERIC(6,4),
    pr_auc            NUMERIC(6,4),
    threshold         NUMERIC(6,4),         -- ngưỡng High theo chi phí: C_FP / (C_FP + C_FN)
    precision         NUMERIC(6,4),
    recall            NUMERIC(6,4),
    f1                NUMERIC(6,4),
    threshold_valid   NUMERIC(6,4),         -- để so sánh: ngưỡng max-F1 chọn trên validation
    f1_valid_threshold NUMERIC(6,4),        -- F1 trên test nếu dùng ngưỡng đó
    precision_top10   NUMERIC(6,4),         -- nếu chỉ xử lý 10% đơn rủi ro nhất
    recall_top10      NUMERIC(6,4),
    trained_at        TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (model_name, model_version)
);

-- Permutation importance của mô hình chính (giảm PR-AUC khi xáo trộn feature)
CREATE TABLE IF NOT EXISTS dw.model_feature_importance (
    model_name      TEXT NOT NULL,
    model_version   TEXT NOT NULL,
    feature         TEXT NOT NULL,
    importance      NUMERIC(10,6) NOT NULL,
    PRIMARY KEY (model_name, model_version, feature)
);

-- Campaign chăm sóc khách hàng: bảng VẬN HÀNH do Ops thêm/sửa/xoá trên tab Campaign, không phải dimension
-- (không fact nào tham chiếu). data/campaigns/care_campaigns.csv chỉ THÊM mã mới, không ghi đè thay đổi trên UI.
-- Xoá mềm (deleted_at) để chạy lại ETL không làm campaign đã xoá xuất hiện trở lại.
CREATE TABLE IF NOT EXISTS dw.care_campaign (
    campaign_sk     SERIAL PRIMARY KEY,
    campaign_code   TEXT UNIQUE NOT NULL,
    campaign_name   TEXT NOT NULL,
    description     TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_care_campaign_name
    ON dw.care_campaign (LOWER(campaign_name)) WHERE deleted_at IS NULL;

-- Tệp khách hàng mục tiêu gắn vào campaign: Ops chọn các đơn vừa giao có rủi ro review thấp
-- -> gán khách của đơn đó vào campaign chăm sóc (voucher xin lỗi, gọi hỏi thăm...).
CREATE TABLE IF NOT EXISTS dw.campaign_target (
    campaign_sk     INT NOT NULL REFERENCES dw.care_campaign(campaign_sk),
    customer_sk     INT NOT NULL REFERENCES dw.dim_customer(customer_sk),
    order_id        TEXT NOT NULL,          -- đơn có rủi ro review thấp dẫn tới việc chọn khách
    risk_score      NUMERIC(6,4),
    assigned_at     TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_sk, customer_sk)
);
