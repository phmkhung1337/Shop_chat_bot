PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS catalog_metadata (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    demo INTEGER NOT NULL CHECK (demo IN (0, 1)),
    approved INTEGER NOT NULL CHECK (approved IN (0, 1)),
    products_version TEXT NOT NULL,
    faq_version TEXT NOT NULL,
    as_of TEXT NOT NULL,
    price_max_age_hours INTEGER NOT NULL CHECK (price_max_age_hours > 0),
    stock_max_age_hours INTEGER NOT NULL CHECK (stock_max_age_hours > 0),
    ingredients_max_age_hours INTEGER NOT NULL CHECK (ingredients_max_age_hours > 0)
);

CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE,
    aliases_json TEXT NOT NULL CHECK (json_valid(aliases_json)),
    category TEXT NOT NULL,
    brand TEXT NOT NULL,
    flavor TEXT NOT NULL,
    description TEXT NOT NULL,
    packaging TEXT NOT NULL,
    unit TEXT NOT NULL,
    price_vnd INTEGER CHECK (price_vnd IS NULL OR (typeof(price_vnd) = 'integer' AND price_vnd >= 0)),
    stock_qty INTEGER CHECK (stock_qty IS NULL OR (typeof(stock_qty) = 'integer' AND stock_qty >= 0)),
    availability TEXT NOT NULL CHECK (availability IN ('available', 'out_of_stock', 'unknown')),
    status TEXT NOT NULL CHECK (status IN ('active', 'discontinued')),
    price_updated_at TEXT,
    stock_updated_at TEXT,
    source TEXT NOT NULL,
    version TEXT NOT NULL,
    url TEXT,
    storage TEXT,
    expiry TEXT,
    ingredients_json TEXT NOT NULL CHECK (json_valid(ingredients_json)),
    CHECK (availability != 'available' OR stock_qty IS NULL OR stock_qty > 0),
    CHECK (availability != 'out_of_stock' OR stock_qty IS NULL OR stock_qty = 0)
);

CREATE INDEX IF NOT EXISTS products_category_status ON products(category, status);

CREATE TABLE IF NOT EXISTS faqs (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    aliases_json TEXT NOT NULL CHECK (json_valid(aliases_json)),
    answer TEXT NOT NULL,
    conditions_json TEXT NOT NULL CHECK (json_valid(conditions_json)),
    approved INTEGER NOT NULL CHECK (approved IN (0, 1)),
    version TEXT NOT NULL,
    source TEXT NOT NULL,
    owner TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    effective_to TEXT
);
