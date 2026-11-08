CREATE SCHEMA IF NOT EXISTS dw;

-- Bảng tham chiếu tĩnh: bang (UF) của Brazil -> vùng địa lý. Dùng khi build dim_location.
CREATE TABLE IF NOT EXISTS dw.ref_br_state (
    state_code  CHAR(2) PRIMARY KEY,
    state_name  TEXT NOT NULL,
    region      TEXT NOT NULL      -- Norte | Nordeste | Centro-Oeste | Sudeste | Sul
);
INSERT INTO dw.ref_br_state (state_code, state_name, region) VALUES
    ('AC', 'Acre', 'Norte'), ('AP', 'Amapá', 'Norte'), ('AM', 'Amazonas', 'Norte'),
    ('PA', 'Pará', 'Norte'), ('RO', 'Rondônia', 'Norte'), ('RR', 'Roraima', 'Norte'),
    ('TO', 'Tocantins', 'Norte'),
    ('AL', 'Alagoas', 'Nordeste'), ('BA', 'Bahia', 'Nordeste'), ('CE', 'Ceará', 'Nordeste'),
    ('MA', 'Maranhão', 'Nordeste'), ('PB', 'Paraíba', 'Nordeste'), ('PE', 'Pernambuco', 'Nordeste'),
    ('PI', 'Piauí', 'Nordeste'), ('RN', 'Rio Grande do Norte', 'Nordeste'), ('SE', 'Sergipe', 'Nordeste'),
    ('DF', 'Distrito Federal', 'Centro-Oeste'), ('GO', 'Goiás', 'Centro-Oeste'),
    ('MT', 'Mato Grosso', 'Centro-Oeste'), ('MS', 'Mato Grosso do Sul', 'Centro-Oeste'),
    ('ES', 'Espírito Santo', 'Sudeste'), ('MG', 'Minas Gerais', 'Sudeste'),
    ('RJ', 'Rio de Janeiro', 'Sudeste'), ('SP', 'São Paulo', 'Sudeste'),
    ('PR', 'Paraná', 'Sul'), ('RS', 'Rio Grande do Sul', 'Sul'), ('SC', 'Santa Catarina', 'Sul')
ON CONFLICT (state_code) DO NOTHING;

-- Toạ độ trung bình của mỗi zip prefix (từ olist_geolocation, đã lọc điểm nằm ngoài Brazil).
-- Dùng để tính khoảng cách người bán -> khách hàng trong fact_order_fulfillment.
CREATE TABLE IF NOT EXISTS dw.ref_zip_geo (
    zip_code_prefix  TEXT PRIMARY KEY,
    state            CHAR(2),
    lat              DOUBLE PRECISION NOT NULL,
    lng              DOUBLE PRECISION NOT NULL
);

-- Chỉ giữ thuộc tính đang được truy vấn (OLAP view, DSS); quarter, season, is_weekend... bổ sung khi có nhu cầu.
CREATE TABLE IF NOT EXISTS dw.dim_date (
    date_sk       INT PRIMARY KEY,          -- YYYYMMDD
    full_date     DATE NOT NULL UNIQUE,     -- khoá tự nhiên
    day_of_week   INT NOT NULL,             -- 1 = Thứ 2 ... 7 = Chủ nhật (ISO)
    month         INT NOT NULL,
    year          INT NOT NULL
);

-- Địa điểm của khách hàng (nơi giao hàng)
CREATE TABLE IF NOT EXISTS dw.dim_location (
    location_sk  SERIAL PRIMARY KEY,
    city         TEXT NOT NULL,            -- cùng state là khoá tự nhiên
    state        CHAR(2) NOT NULL,
    region       TEXT,
    UNIQUE (city, state)
);

CREATE TABLE IF NOT EXISTS dw.dim_seller (
    seller_sk        SERIAL PRIMARY KEY,
    seller_id        TEXT UNIQUE NOT NULL,
    state            CHAR(2)
);

-- Phương thức thanh toán chính của đơn (thay cho "kênh bán" — Olist không có thông tin kênh)
CREATE TABLE IF NOT EXISTS dw.dim_payment_type (
    payment_type_sk  SERIAL PRIMARY KEY,
    payment_type     TEXT UNIQUE NOT NULL
);

-- SCD Type 1. Khối lượng / thể tích của đơn được tính thẳng từ staging vào fact_order_fulfillment.
CREATE TABLE IF NOT EXISTS dw.dim_product (
    product_sk          SERIAL PRIMARY KEY,
    product_id          TEXT UNIQUE NOT NULL,
    category            TEXT NOT NULL       -- tiếng Anh (đã dịch), 'unknown' nếu thiếu
);

-- SCD Type 2 — theo dõi thay đổi ĐỊA CHỈ (zip, city, state) của khách qua các đơn hàng.
-- Olist ghi địa chỉ theo từng đơn: khách chuyển nhà -> version mới, effective_from = THỜI ĐIỂM mua đơn đầu
-- tiên ở địa chỉ mới (TIMESTAMP, không phải DATE: khách có thể đặt 2 đơn tới 2 địa chỉ trong cùng một ngày).
-- Fact join theo thời điểm mua để lấy đúng địa chỉ tại thời điểm đó.
CREATE TABLE IF NOT EXISTS dw.dim_customer (
    customer_sk       SERIAL PRIMARY KEY,
    customer_id       TEXT NOT NULL,        -- = customer_unique_id của Olist
    zip_code_prefix   TEXT,
    city              TEXT,
    state             CHAR(2),
    effective_from    TIMESTAMP NOT NULL,
    effective_to      TIMESTAMP,            -- NULL = còn hiệu lực
    is_current        BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_dim_customer_bk ON dw.dim_customer (customer_id, is_current);
CREATE UNIQUE INDEX IF NOT EXISTS uq_dim_customer_version ON dw.dim_customer (customer_id, effective_from);
CREATE UNIQUE INDEX IF NOT EXISTS uq_dim_customer_current ON dw.dim_customer (customer_id) WHERE is_current;
