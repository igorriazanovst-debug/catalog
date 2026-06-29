-- Расширение для векторного поиска
CREATE EXTENSION IF NOT EXISTS vector;

-- Таблица отраслевых стандартов (Приказ 838 и др.)
CREATE TABLE industry_standards (
    id SERIAL PRIMARY KEY,
    industry_code VARCHAR(20) NOT NULL,
    section_code VARCHAR(10),
    subsection_code VARCHAR(10),
    section_name TEXT,
    subsection_name TEXT,
    item_name TEXT NOT NULL,
    equipment_type VARCHAR(50),
    keywords TEXT[],
    okpd2_code VARCHAR(20),
    ktru_code VARCHAR(20),
    embedding vector(768),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Таблица товаров
-- ВНИМАНИЕ: sku НЕ глобально уникален. Товары — per-supplier: один и тот же
-- артикул у разных поставщиков = разные товары (разные предложения). Уникальность
-- предложения обеспечивает supplier_products UNIQUE(supplier_id, product_id).
CREATE TABLE products (
    id SERIAL PRIMARY KEY,
    sku VARCHAR(100) NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    unit VARCHAR(50) DEFAULT 'шт',
    manufacturer TEXT,
    vat_included BOOLEAN DEFAULT FALSE,
    okpd2_code VARCHAR(20),
    ktru_code VARCHAR(20),
    properties JSONB,
    embedding vector(768),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Таблица поставщиков
CREATE TABLE suppliers (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    short_name TEXT,
    inn VARCHAR(12) UNIQUE,
    contact_person TEXT,
    phone VARCHAR(20),
    email VARCHAR(100),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);
-- Связь поставщик-товар (с ценами и сроками)
CREATE TABLE supplier_products (
    id SERIAL PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    supplier_sku VARCHAR(100),
    cost_price NUMERIC(15,2) NOT NULL,
    retail_price NUMERIC(15,2) NOT NULL,
    delivery_days INTEGER,
    stock_quantity INTEGER DEFAULT 0,
    is_available BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(supplier_id, product_id)
);

-- Связь товар-стандарт (маппинг)
CREATE TABLE product_standard_mapping (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    standard_id INTEGER NOT NULL REFERENCES industry_standards(id) ON DELETE CASCADE,
    match_score FLOAT,
    match_reason TEXT,
    is_manual BOOLEAN DEFAULT FALSE,
    rejected BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(product_id, standard_id)
);

-- Глобальные настройки системы
CREATE TABLE system_settings (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT NOT NULL,
    description TEXT,
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Таблица смет
-- source_* — исходный загруженный файл сметы (для аннотированного экспорта:
--   в оригинальный файл дописываем наши колонки и заполняем подбором).
CREATE TABLE estimates (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    total_amount NUMERIC(15,2),
    source_filename TEXT,
    source_file_b64 TEXT,
    sheet_name TEXT,
    header_row INTEGER,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Позиции сметы
-- source_name — исходное наименование строки/вложения из входящей сметы
--   (нужно, чтобы несопоставленные позиции не теряли текст потребности).
-- group_name  — наименование строки-набора, если позиция получена разложением
--   набора на вложения (NULL — обычная позиция).
CREATE TABLE estimate_items (
    id SERIAL PRIMARY KEY,
    estimate_id INTEGER NOT NULL REFERENCES estimates(id) ON DELETE CASCADE,
    standard_id INTEGER REFERENCES industry_standards(id) ON DELETE SET NULL,
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    supplier_id INTEGER REFERENCES suppliers(id) ON DELETE SET NULL,
    source_name TEXT,
    source_description TEXT,
    source_row INTEGER,
    group_name TEXT,
    unit VARCHAR(50),
    match_method VARCHAR(20),
    match_reason TEXT,
    quantity NUMERIC(10,2) NOT NULL,
    unit_price NUMERIC(15,2) NOT NULL,
    total_price NUMERIC(15,2) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Индексы для производительности
CREATE INDEX idx_industry_standards_industry_code ON industry_standards(industry_code);
CREATE INDEX idx_industry_standards_section_code ON industry_standards(section_code);
CREATE INDEX idx_industry_standards_keywords ON industry_standards USING GIN(keywords);
CREATE INDEX idx_industry_standards_embedding ON industry_standards USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

CREATE INDEX idx_products_sku ON products(sku);
CREATE INDEX idx_products_name ON products(name);
CREATE INDEX idx_products_manufacturer ON products(manufacturer);
CREATE INDEX idx_products_properties ON products USING GIN(properties);
CREATE INDEX idx_products_embedding ON products USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

CREATE INDEX idx_suppliers_inn ON suppliers(inn);
CREATE INDEX idx_suppliers_name ON suppliers(name);

CREATE INDEX idx_supplier_products_supplier_id ON supplier_products(supplier_id);
CREATE INDEX idx_supplier_products_product_id ON supplier_products(product_id);

CREATE INDEX idx_product_standard_mapping_product_id ON product_standard_mapping(product_id);
CREATE INDEX idx_product_standard_mapping_standard_id ON product_standard_mapping(standard_id);

CREATE INDEX idx_estimates_created_at ON estimates(created_at);
CREATE INDEX idx_estimate_items_estimate_id ON estimate_items(estimate_id);

-- Начальные настройки системы
INSERT INTO system_settings (key, value, description) VALUES
('vat_rate', '0.22', 'Ставка НДС (22%)'),
('currency', 'RUB', 'Валюта по умолчанию'),
('company_name', 'Школьный каталог', 'Название организации');