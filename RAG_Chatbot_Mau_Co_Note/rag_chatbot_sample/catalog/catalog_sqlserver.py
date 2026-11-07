"""Microsoft SQL Server catalog for SSMS, using the existing ODBC 17 installation."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path

import pyodbc

from catalog.catalog_sql import PRODUCT_FIELDS, FAQ_FIELDS, decode_catalog_rows

PROJECT_DIR = Path(__file__).resolve().parents[1]
SCHEMA_FILE = Path(__file__).parent / "sqlserver" / "schema.sql"
DEFAULT_CONNECTION_STRING = (
    "DRIVER={ODBC Driver 17 for SQL Server};SERVER=localhost;DATABASE=ShopChatbotDemo;"
    "Trusted_Connection=yes;Encrypt=yes;TrustServerCertificate=yes;"
)
MASTER_CONNECTION_STRING = DEFAULT_CONNECTION_STRING.replace("DATABASE=ShopChatbotDemo;", "DATABASE=master;")
METADATA_FIELDS = ("id", "schema_version", "demo", "approved", "products_version", "faq_version", "as_of",
                   "price_max_age_hours", "stock_max_age_hours", "ingredients_max_age_hours")


def connect(connection_string=None, *, autocommit=False):
    return pyodbc.connect(connection_string or os.getenv("SHOP_SQLSERVER_CONNECTION_STRING") or DEFAULT_CONNECTION_STRING,
                          timeout=5, autocommit=autocommit)


def rows(cursor):
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def select_columns(fields, date_fields=()):
    # Only fixed schema column names are used here, never model/customer text.
    return ",".join(f"CONVERT(nvarchar(40),[{name}],127) AS [{name}]" if name in date_fields else f"[{name}]" for name in fields)


def read_catalog(connection_string=None, *, mode="demo", clock=None):
    try:
        with closing(connect(connection_string)) as db:
            cursor = db.cursor()
            cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE; SET XACT_ABORT ON;")
            catalog = read_snapshot(cursor, mode=mode, clock=clock)
            db.commit()
        return catalog
    except (pyodbc.Error, KeyError, json.JSONDecodeError, TypeError):
        # Connection strings may contain SQL credentials. Never surface a driver exception to chat/CLI.
        raise ValueError("Không đọc được catalog SQL Server. Kiểm tra kết nối, quyền SELECT và schema; không chuyển sang SQLite/JSON.") from None


def read_snapshot(cursor, *, mode="demo", clock=None):
    """Reuse the catalog validation inside a caller's SQL transaction, including checkout."""
    cursor.execute("SELECT " + select_columns(METADATA_FIELDS, ("as_of",)) +
                   ",CAST(SERVERPROPERTY('ServerName') AS nvarchar(128)) AS server_name,DB_NAME() AS database_name "
                   "FROM dbo.catalog_metadata WHERE id=1")
    metadata_rows = rows(cursor)
    if len(metadata_rows) != 1 or metadata_rows[0]["schema_version"] != 1:
        raise ValueError("Catalog SQL Server chưa có schema/snapshot hợp lệ.")
    metadata = metadata_rows[0]
    cursor.execute("SELECT " + select_columns(PRODUCT_FIELDS, ("price_updated_at", "stock_updated_at")) + " FROM dbo.products ORDER BY id")
    products = rows(cursor)
    cursor.execute("SELECT " + select_columns(FAQ_FIELDS, ("effective_from", "effective_to")) + " FROM dbo.faqs ORDER BY id")
    catalog = decode_catalog_rows(metadata, products, rows(cursor), mode=mode, clock=clock)
    catalog.database_path = f"sqlserver://{metadata['server_name']}/{metadata['database_name']}"
    return catalog


def validated_rows(raw, faq_data, *, mode="demo"):
    from chatbot.shop import catalog_from_data, normalize
    catalog = catalog_from_data(raw, faq_data, mode=mode)
    if len({normalize(p["name"]) for p in catalog.products}) != len(catalog.products):
        raise ValueError("Tên loại bánh kẹo bị trùng.")
    ages = raw["max_age_hours"]
    metadata = (1, 1, int(raw["demo"]), int(raw["approved"]), raw["version"], faq_data["version"], raw["as_of"],
                ages["price"], ages["stock"], ages["ingredients"])
    products = []
    for p in catalog.products:
        encoded = dict(p, name_key=normalize(p["name"]), aliases_json=json.dumps(p["aliases"], ensure_ascii=False),
                       ingredients_json=json.dumps(p["ingredients"], ensure_ascii=False))
        products.append(tuple(encoded.get(field) for field in PRODUCT_FIELDS))
    faqs = []
    for f in catalog.faqs:
        encoded = dict(f, approved=int(f["approved"]), aliases_json=json.dumps(f["aliases"], ensure_ascii=False),
                       conditions_json=json.dumps(f["conditions"], ensure_ascii=False))
        faqs.append(tuple(encoded[field] for field in FAQ_FIELDS))
    return metadata, products, faqs


def insert_statement(table, fields):
    return f"INSERT INTO dbo.{table} ({','.join('[' + field + ']' for field in fields)}) VALUES ({','.join('?' for _ in fields)})"


def write_empty_catalog(raw, faq_data, *, mode="demo", connection_string=None):
    """Only populate an empty catalog. Existing SSMS-managed data is never deleted/replaced."""
    metadata, products, faqs = validated_rows(raw, faq_data, mode=mode)
    try:
        with closing(connect(connection_string)) as db:
            try:
                cursor = db.cursor()
                cursor.execute("SET XACT_ABORT ON; SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;")
                cursor.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
                cursor.execute("SELECT (SELECT count(*) FROM dbo.catalog_metadata) + "
                               "(SELECT count(*) FROM dbo.products) + (SELECT count(*) FROM dbo.faqs)")
                if cursor.fetchone()[0]:
                    raise ValueError("Catalog SQL Server đã có dữ liệu. Giữ nguyên dữ liệu; cập nhật qua SSMS hoặc chọn database trống.")
                cursor.execute(insert_statement("catalog_metadata", METADATA_FIELDS), metadata)
                cursor.executemany(insert_statement("products", PRODUCT_FIELDS), products)
                cursor.executemany(insert_statement("faqs", FAQ_FIELDS), faqs)
                db.commit()
            except Exception:
                db.rollback()
                raise
    except pyodbc.Error:
        raise ValueError("Không nhập được catalog SQL Server; transaction đã rollback. Kiểm tra schema và quyền ghi trong SSMS.") from None


def seed_demo():
    # This command can create only this isolated local demo DB, never another configured database.
    with closing(connect(MASTER_CONNECTION_STRING, autocommit=True)) as db:
        cursor = db.cursor()
        cursor.execute("IF DB_ID(N'ShopChatbotDemo') IS NULL EXEC(N'CREATE DATABASE [ShopChatbotDemo]')")
    with closing(connect(DEFAULT_CONNECTION_STRING)) as db:
        cursor = db.cursor()
        cursor.execute("SELECT OBJECT_ID(N'dbo.catalog_metadata',N'U')")
        initialized = cursor.fetchone()[0] is not None
        cursor.execute("SELECT count(*) FROM sys.tables WHERE is_ms_shipped=0")
        table_count = cursor.fetchone()[0]
    if initialized:
        catalog = read_catalog(DEFAULT_CONNECTION_STRING, mode="demo")
        if len(catalog.products) != 200:
            raise ValueError("Catalog hiện có không đủ đúng 200 loại; không tự ý ghi đè.")
        return catalog
    if table_count:
        raise ValueError("ShopChatbotDemo đã có bảng khác. Không sửa database hiện hữu không thuộc catalog này.")
    from catalog.seed_catalog import build_demo_catalog
    raw, faqs = build_demo_catalog()
    write_empty_catalog(raw, faqs, mode="demo", connection_string=DEFAULT_CONNECTION_STRING)
    return read_catalog(DEFAULT_CONNECTION_STRING, mode="demo")


def import_json_catalog(source_dir, *, mode="connected"):
    source = Path(source_dir)
    try:
        raw = json.loads((source / "products.json").read_text(encoding="utf-8"))
        faqs = json.loads((source / "faq.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("Không đọc được dữ liệu JSON để nhập SQL Server.") from None
    write_empty_catalog(raw, faqs, mode=mode)


def randomize_demo_stock():
    """Explicit operator action only. Page loads and chat never generate stock values."""
    try:
        with closing(connect()) as db:
            try:
                cursor = db.cursor()
                cursor.execute("SET XACT_ABORT ON; SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;")
                cursor.execute("SELECT demo FROM dbo.catalog_metadata WITH (UPDLOCK,HOLDLOCK) WHERE id=1")
                metadata = cursor.fetchone()
                if metadata is None or not metadata[0]:
                    raise ValueError("Chỉ tạo số lượng ngẫu nhiên cho dữ liệu demo.")
                cursor.execute("SELECT count(*) FROM dbo.products")
                if cursor.fetchone()[0] != 200:
                    raise ValueError("Catalog phải có đúng 200 loại trước khi tạo lại số lượng demo.")
                cursor.execute("UPDATE dbo.products SET stock_qty=ABS(CONVERT(bigint,CHECKSUM(NEWID()))) % 999 + 1, "
                               "availability='available',stock_updated_at=(SELECT as_of FROM dbo.catalog_metadata WHERE id=1)")
                cursor.execute("SELECT count(*),min(stock_qty),max(stock_qty) FROM dbo.products")
                count, minimum, maximum = cursor.fetchone()
                if count != 200 or not 1 <= minimum <= maximum <= 999:
                    raise ValueError("Số lượng ngẫu nhiên không hợp lệ; chưa lưu.")
                db.commit()
                return {"product_count": count, "minimum": minimum, "maximum": maximum}
            except Exception:
                db.rollback()
                raise
    except pyodbc.Error:
        raise ValueError("Không cập nhật được tồn kho demo; transaction đã rollback.") from None


def sql_literal(value):
    if value is None:
        return "NULL"
    if type(value) in (int, bool):
        return str(int(value))
    if isinstance(value, str):
        return "N'" + value.replace("'", "''") + "'"
    raise ValueError("Kiểu giá trị không được hỗ trợ trong T-SQL seed.")


def build_demo_script():
    from catalog.seed_catalog import build_demo_catalog
    raw, faqs = build_demo_catalog()
    metadata, products, faq_rows = validated_rows(raw, faqs)
    header = """-- SQL Server 2019+ / SSMS. 200 named DEMO varieties; prices/stock are illustrative.
-- Does not delete existing data. SQL Server tables are the application/RAG source.
USE [master];
GO
IF DB_ID(N'ShopChatbotDemo') IS NULL EXEC(N'CREATE DATABASE [ShopChatbotDemo]');
GO
USE [ShopChatbotDemo];
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
BEGIN TRY
    BEGIN TRANSACTION;
IF OBJECT_ID(N'dbo.catalog_metadata',N'U') IS NULL AND EXISTS (SELECT 1 FROM sys.tables WHERE is_ms_shipped=0)
    THROW 50001, N'Database da co bang khac. Khong tu y ghi de.', 1;
"""
    guard = """
IF EXISTS (SELECT 1 FROM dbo.products) OR EXISTS (SELECT 1 FROM dbo.faqs) OR EXISTS (SELECT 1 FROM dbo.catalog_metadata)
BEGIN
    IF (SELECT count(*) FROM dbo.products)=200 AND EXISTS (SELECT 1 FROM dbo.catalog_metadata WHERE demo=1 AND schema_version=1)
    BEGIN
        COMMIT TRANSACTION;
        PRINT N'Catalog 200 loai da co san. Du lieu SSMS duoc giu nguyen.';
        RETURN;
    END;
    THROW 50002, N'Catalog da co du lieu. Khong xoa hoac ghi de.', 1;
END;
"""
    inserts = []
    for table, fields, batch in (("catalog_metadata", METADATA_FIELDS, [metadata]),
                                  ("products", PRODUCT_FIELDS, products), ("faqs", FAQ_FIELDS, faq_rows)):
        columns = ",".join("[" + field + "]" for field in fields)
        for row in batch:
            inserts.append(f"INSERT INTO dbo.{table} ({columns}) VALUES ({','.join(sql_literal(v) for v in row)});")
    footer = """
    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
SELECT count(*) AS product_count, count(DISTINCT name_key) AS distinct_varieties FROM dbo.products;
SELECT category,count(*) AS product_count FROM dbo.products GROUP BY category;
"""
    return header + SCHEMA_FILE.read_text(encoding="utf-8") + guard + "\n".join(inserts) + footer


def main():
    parser = argparse.ArgumentParser(description="Catalog Microsoft SQL Server cho SSMS và RAG")
    parser.add_argument("command", choices=("seed-demo", "check", "script-demo", "randomize-stock"))
    parser.add_argument("--mode", choices=("demo", "connected"), default="demo")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "sqlserver" / "catalog_200.sql")
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(PROJECT_DIR / ".env")
    try:
        if args.command == "script-demo":
            args.output.write_text(build_demo_script(), encoding="utf-8-sig")
            print(f"T-SQL cho SSMS: {args.output}")
            return
        if args.command == "randomize-stock":
            print(json.dumps(randomize_demo_stock(), ensure_ascii=False))
            return
        catalog = seed_demo() if args.command == "seed-demo" else read_catalog(mode=args.mode)
        print(f"Nguồn SQL Server: {catalog.database_path}")
        print(f"{len(catalog.products)} sản phẩm · {len(catalog.faqs)} FAQ · {'DEMO' if catalog.demo else 'đã duyệt'}")
    except (pyodbc.Error, ValueError, OSError):
        parser.exit(1, "Không hoàn tất thao tác SQL Server. Kiểm tra instance, Windows Authentication, ODBC 17 và quyền database; không dùng SQLite/JSON thay thế.\n")


if __name__ == "__main__":
    main()
