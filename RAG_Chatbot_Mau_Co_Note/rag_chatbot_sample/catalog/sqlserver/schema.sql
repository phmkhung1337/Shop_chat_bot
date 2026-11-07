IF OBJECT_ID(N'dbo.catalog_metadata', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.catalog_metadata (
        id int NOT NULL CONSTRAINT PK_catalog_metadata PRIMARY KEY CONSTRAINT CK_catalog_singleton CHECK (id = 1),
        schema_version int NOT NULL CONSTRAINT CK_catalog_version CHECK (schema_version = 1),
        demo bit NOT NULL,
        approved bit NOT NULL,
        products_version nvarchar(100) NOT NULL,
        faq_version nvarchar(100) NOT NULL,
        as_of datetimeoffset(0) NOT NULL,
        price_max_age_hours bigint NOT NULL CHECK (price_max_age_hours > 0),
        stock_max_age_hours bigint NOT NULL CHECK (stock_max_age_hours > 0),
        ingredients_max_age_hours bigint NOT NULL CHECK (ingredients_max_age_hours > 0)
    );
END;

IF OBJECT_ID(N'dbo.products', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.products (
        id nvarchar(80) NOT NULL CONSTRAINT PK_products PRIMARY KEY,
        name nvarchar(300) NOT NULL,
        name_key nvarchar(300) NOT NULL CONSTRAINT UQ_products_name_key UNIQUE,
        aliases_json nvarchar(max) NOT NULL CHECK (ISJSON(aliases_json) = 1),
        category nvarchar(60) NOT NULL,
        brand nvarchar(150) NOT NULL,
        flavor nvarchar(200) NOT NULL,
        description nvarchar(max) NOT NULL,
        packaging nvarchar(200) NOT NULL,
        unit nvarchar(50) NOT NULL,
        price_vnd bigint NULL CHECK (price_vnd >= 0),
        stock_qty bigint NULL CHECK (stock_qty >= 0),
        availability varchar(20) NOT NULL CHECK (availability IN ('available','out_of_stock','unknown')),
        status varchar(20) NOT NULL CHECK (status IN ('active','discontinued')),
        price_updated_at datetimeoffset(0) NULL,
        stock_updated_at datetimeoffset(0) NULL,
        source nvarchar(200) NOT NULL,
        version nvarchar(100) NOT NULL,
        url nvarchar(2000) NULL,
        storage nvarchar(max) NULL,
        expiry nvarchar(500) NULL,
        ingredients_json nvarchar(max) NOT NULL CHECK (ISJSON(ingredients_json) = 1),
        CHECK (availability <> 'available' OR stock_qty IS NULL OR stock_qty > 0),
        CHECK (availability <> 'out_of_stock' OR stock_qty IS NULL OR stock_qty = 0)
    );
    CREATE INDEX IX_products_category_status ON dbo.products(category,status);
END;

IF OBJECT_ID(N'dbo.faqs', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.faqs (
        id nvarchar(80) NOT NULL CONSTRAINT PK_faqs PRIMARY KEY,
        question nvarchar(1000) NOT NULL,
        aliases_json nvarchar(max) NOT NULL CHECK (ISJSON(aliases_json) = 1),
        answer nvarchar(max) NOT NULL,
        conditions_json nvarchar(max) NOT NULL CHECK (ISJSON(conditions_json) = 1),
        approved bit NOT NULL,
        version nvarchar(100) NOT NULL,
        source nvarchar(200) NOT NULL,
        owner nvarchar(200) NOT NULL,
        effective_from datetimeoffset(0) NOT NULL,
        effective_to datetimeoffset(0) NULL,
        CHECK (effective_to IS NULL OR effective_to >= effective_from)
    );
END;
