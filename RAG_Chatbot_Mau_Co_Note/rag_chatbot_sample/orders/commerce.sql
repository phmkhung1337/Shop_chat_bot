-- Select the catalog database before Execute (ShopChatbotDemo on this machine).
-- Additive migration: preserve existing products, prices, stock and coefficients.
IF OBJECT_ID(N'dbo.products',N'U') IS NULL OR OBJECT_ID(N'dbo.catalog_metadata',N'U') IS NULL
    THROW 50010, N'Hay chon database catalog hop le, khong chay trong master.', 1;
IF OBJECT_ID(N'dbo.wholesale_rules', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.wholesale_rules (
        product_id nvarchar(80) NOT NULL PRIMARY KEY REFERENCES dbo.products(id),
        k decimal(5,4) NOT NULL CHECK (k >= 0.05 AND k <= 0.2)
    );
END;

INSERT INTO dbo.wholesale_rules(product_id,k)
SELECT p.id,CONVERT(decimal(5,4),(500 + ABS(CONVERT(bigint,CHECKSUM(NEWID()))) % 1501) / 10000.0)
FROM dbo.products p
WHERE NOT EXISTS (SELECT 1 FROM dbo.wholesale_rules r WHERE r.product_id=p.id);
IF OBJECT_ID(N'dbo.shop_orders', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.shop_orders (
        id nvarchar(80) NOT NULL PRIMARY KEY,
        request_id nvarchar(100) NOT NULL UNIQUE,
        conversation_id nvarchar(100) NOT NULL,
        request_hash varchar(64) NOT NULL,
        total_vnd bigint NOT NULL CHECK (total_vnd >= 0),
        status varchar(20) NOT NULL CHECK (status IN ('confirmed','cancelled')),
        payment_status varchar(20) NOT NULL CHECK (payment_status IN ('pending','paid')),
        payment_reference nvarchar(200) NULL,
        paid_at datetimeoffset(0) NULL,
        demo bit NOT NULL,
        created_at datetimeoffset(0) NOT NULL,
        updated_at datetimeoffset(0) NOT NULL,
        CHECK (status <> 'cancelled' OR payment_status = 'pending')
    );
    CREATE INDEX IX_shop_orders_conversation ON dbo.shop_orders(conversation_id,created_at);
END;
IF OBJECT_ID(N'dbo.shop_order_items', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.shop_order_items (
        order_id nvarchar(80) NOT NULL REFERENCES dbo.shop_orders(id),
        product_id nvarchar(80) NOT NULL REFERENCES dbo.products(id),
        product_name nvarchar(300) NOT NULL,
        quantity bigint NOT NULL CHECK (quantity > 0 AND quantity <= 10000),
        wholesale bit NOT NULL,
        k decimal(5,4) NULL CHECK (k >= 0.05 AND k <= 0.2),
        base_price_vnd bigint NOT NULL CHECK (base_price_vnd >= 0),
        unit_price_vnd bigint NOT NULL CHECK (unit_price_vnd >= 0),
        unit nvarchar(50) NOT NULL,
        PRIMARY KEY (order_id,product_id),
        CHECK (wholesale = 0 OR k IS NOT NULL),
        CHECK (unit_price_vnd >= CEILING(CONVERT(decimal(24,4),base_price_vnd) * 0.70)),
        CHECK (unit_price_vnd <= base_price_vnd)
    );
END;
