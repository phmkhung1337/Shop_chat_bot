-- Open in SSMS, connected to ILMOON (or localhost) using Windows Authentication.
USE [ShopChatbotDemo];
GO

SELECT COUNT(*) AS product_count,
       COUNT(DISTINCT id) AS sku_count,
       COUNT(DISTINCT name_key) AS distinct_varieties,
       MIN(stock_qty) AS minimum_stock,
       MAX(stock_qty) AS maximum_stock
FROM dbo.products;

SELECT category,COUNT(*) AS product_count
FROM dbo.products
GROUP BY category;

SELECT id,name,category,flavor,price_vnd,stock_qty,availability,packaging,source
FROM dbo.products
ORDER BY id;

SELECT id,question,answer,conditions_json,effective_from,effective_to
FROM dbo.faqs
WHERE approved=1;

-- Unknown ingredient information must never be interpreted as verified absence.
SELECT id,name,JSON_VALUE(ingredients_json,'$.allergens.milk') AS milk_evidence
FROM dbo.products
ORDER BY id;

-- Prices and stock are read from these tables at each turn; they are not taken from embeddings.
SELECT id,name,price_vnd,stock_qty
FROM dbo.products
WHERE status='active' AND availability='available' AND price_vnd <= 100000
ORDER BY price_vnd,id;

-- After changing description/name/FAQ content in SSMS, rebuild the derived shop index:
-- python app.py --rebuild-shop --mode demo
