-- Explicit DEMO operation: run once in SSMS only when you want new random quantities.
-- Reloading the app or asking the chatbot never changes these saved values.
USE [ShopChatbotDemo];
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
BEGIN TRY
    BEGIN TRANSACTION;
    IF NOT EXISTS (SELECT 1 FROM dbo.catalog_metadata WITH (UPDLOCK,HOLDLOCK) WHERE id=1 AND demo=1)
        THROW 50003, N'Chi duoc tao so luong ngau nhien cho catalog demo.', 1;
    IF (SELECT COUNT(*) FROM dbo.products) <> 200
        THROW 50004, N'Catalog phai co dung 200 loai.', 1;

    UPDATE dbo.products
    SET stock_qty = ABS(CONVERT(bigint,CHECKSUM(NEWID()))) % 999 + 1,
        availability = 'available',
        stock_updated_at = (SELECT as_of FROM dbo.catalog_metadata WHERE id=1);

    COMMIT TRANSACTION;
    SELECT COUNT(*) AS product_count, MIN(stock_qty) AS minimum_stock, MAX(stock_qty) AS maximum_stock
    FROM dbo.products;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
