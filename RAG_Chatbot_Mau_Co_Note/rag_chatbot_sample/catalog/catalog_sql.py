"""SQL is the catalog source; Chroma is only the derived semantic index."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

PROJECT_DIR = Path(__file__).resolve().parents[1]
SCHEMA_FILE = Path(__file__).with_name("catalog_schema.sql")
PRODUCT_FIELDS = ("id", "name", "name_key", "aliases_json", "category", "brand", "flavor", "description",
                  "packaging", "unit", "price_vnd", "stock_qty", "availability", "status", "price_updated_at",
                  "stock_updated_at", "source", "version", "url", "storage", "expiry", "ingredients_json")
FAQ_FIELDS = ("id", "question", "aliases_json", "answer", "conditions_json", "approved", "version", "source",
              "owner", "effective_from", "effective_to")


def default_catalog_path(mode):
    if mode not in {"demo", "connected"}:
        raise ValueError("Chế độ catalog không hợp lệ.")
    return Path(os.getenv("SHOP_CATALOG_DB") or PROJECT_DIR / "data" /
                ("demo" if mode == "demo" else "approved") / "shop_catalog.sqlite3")


def connect_readonly(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError("Chưa có catalog SQL. Chạy python -m catalog.catalog_sql seed-demo cho bản mẫu hoặc nhập dữ liệu đã duyệt.")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def decode_catalog_rows(metadata, product_rows, faq_rows, *, mode="demo", clock=None):
    from chatbot.shop import catalog_from_data
    products = []
    for row in product_rows:
        p = dict(row)
        p.pop("name_key")
        p["aliases"] = json.loads(p.pop("aliases_json"))
        p["ingredients"] = json.loads(p.pop("ingredients_json"))
        products.append(p)
    faqs = []
    for row in faq_rows:
        f = dict(row)
        f["aliases"] = json.loads(f.pop("aliases_json"))
        f["conditions"] = json.loads(f.pop("conditions_json"))
        f["approved"] = bool(f["approved"])
        faqs.append(f)
    raw = {"demo": bool(metadata["demo"]), "approved": bool(metadata["approved"]),
           "version": metadata["products_version"], "as_of": metadata["as_of"],
           "max_age_hours": {"price": metadata["price_max_age_hours"], "stock": metadata["stock_max_age_hours"],
                             "ingredients": metadata["ingredients_max_age_hours"]}, "products": products}
    faq_data = {"demo": raw["demo"], "approved": raw["approved"], "version": metadata["faq_version"], "faqs": faqs}
    return catalog_from_data(raw, faq_data, mode=mode, clock=clock)


def read_catalog(path, *, mode="demo", clock=None):
    try:
        with closing(connect_readonly(path)) as db:
            db.execute("BEGIN")
            metadata = db.execute("SELECT * FROM catalog_metadata WHERE id=1").fetchone()
            if metadata is None or metadata["schema_version"] != 1:
                raise ValueError("Schema catalog SQL không hợp lệ hoặc chưa được hỗ trợ.")
            product_rows = db.execute("SELECT * FROM products ORDER BY id").fetchall()
            faq_rows = db.execute("SELECT * FROM faqs ORDER BY id").fetchall()
            catalog = decode_catalog_rows(metadata, product_rows, faq_rows, mode=mode, clock=clock)
            db.commit()
        catalog.database_path = str(Path(path).resolve())
        return catalog
    except (sqlite3.Error, KeyError, json.JSONDecodeError, TypeError):
        raise ValueError("Không đọc được catalog SQL hợp lệ. Chưa sử dụng JSON hoặc snapshot cũ thay thế.") from None


def write_catalog(path, raw, faq_data, *, mode="demo", replace=False):
    """Validate first, then commit all tables together. A replacement keeps a SQL backup."""
    from chatbot.shop import catalog_from_data, normalize
    catalog = catalog_from_data(raw, faq_data, mode=mode)
    names = [normalize(p["name"]) for p in catalog.products]
    if len(set(names)) != len(names):
        raise ValueError("Tên loại bánh kẹo bị trùng; không chỉ đổi SKU để tăng số lượng.")
    path = Path(path).resolve()
    if path.exists() and not replace:
        raise ValueError("Catalog SQL đã tồn tại. Không ghi đè dữ liệu; chỉ thay khi chủ động yêu cầu import.")
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        # backup() copies a consistent SQLite snapshot, including committed WAL data.
        backup = path.with_name(path.name + ".backup-" + uuid4().hex)
        with closing(connect_readonly(path)) as source, closing(sqlite3.connect(backup)) as target:
            source.backup(target)
    with closing(sqlite3.connect(path, timeout=5)) as db:
        db.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
        with db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM products")
            db.execute("DELETE FROM faqs")
            db.execute("DELETE FROM catalog_metadata")
            ages = raw["max_age_hours"]
            db.execute("INSERT INTO catalog_metadata VALUES (1,1,?,?,?,?,?,?,?,?)",
                       (int(raw["demo"]), int(raw["approved"]), raw["version"], faq_data["version"], raw["as_of"],
                        ages["price"], ages["stock"], ages["ingredients"]))
            rows = []
            for product in catalog.products:
                p = dict(product, name_key=normalize(product["name"]),
                         aliases_json=json.dumps(product["aliases"], ensure_ascii=False),
                         ingredients_json=json.dumps(product["ingredients"], ensure_ascii=False))
                rows.append(tuple(p.get(field) for field in PRODUCT_FIELDS))
            db.executemany(f"INSERT INTO products ({','.join(PRODUCT_FIELDS)}) VALUES ({','.join('?' for _ in PRODUCT_FIELDS)})", rows)
            rows = []
            for faq in catalog.faqs:
                f = dict(faq, approved=int(faq["approved"]), aliases_json=json.dumps(faq["aliases"], ensure_ascii=False),
                         conditions_json=json.dumps(faq["conditions"], ensure_ascii=False))
                rows.append(tuple(f[field] for field in FAQ_FIELDS))
            db.executemany(f"INSERT INTO faqs ({','.join(FAQ_FIELDS)}) VALUES ({','.join('?' for _ in FAQ_FIELDS)})", rows)
    return backup


def import_json_catalog(source_dir, destination, *, mode="connected"):
    folder = Path(source_dir)
    try:
        raw = json.loads((folder / "products.json").read_text(encoding="utf-8"))
        faqs = json.loads((folder / "faq.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("Không đọc được cặp products.json và faq.json để nhập SQL.") from None
    return write_catalog(destination, raw, faqs, mode=mode, replace=Path(destination).exists())


def export_sql(path, output):
    output = Path(output).resolve()
    if output == Path(path).resolve():
        raise ValueError("File SQL xuất ra phải khác file database.")
    with closing(connect_readonly(path)) as db:
        dump = "\n".join(db.iterdump())
    output.write_text("-- SQLite catalog export. DEMO data is not verified shop inventory.\n" + dump + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Catalog SQLite cho shop/RAG — không dùng LLM để thực thi SQL")
    parser.add_argument("command", choices=("seed-demo", "check", "export"))
    parser.add_argument("--db", type=Path)
    parser.add_argument("--mode", choices=("demo", "connected"), default="demo")
    parser.add_argument("--output", type=Path, help="File .sql khi dùng export")
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(PROJECT_DIR / ".env")
    path = args.db or default_catalog_path(args.mode)
    try:
        if args.command == "seed-demo":
            if args.mode != "demo":
                raise ValueError("Chỉ seed dữ liệu minh họa ở chế độ demo.")
            if not path.exists():
                from catalog.seed_catalog import build_demo_catalog
                raw, faqs = build_demo_catalog()
                write_catalog(path, raw, faqs, mode="demo")
            catalog = read_catalog(path, mode="demo")
            if len(catalog.products) != 200:
                raise ValueError("SQL hiện có không đủ đúng 200 loại; chưa tự ý thay nội dung đang có.")
        else:
            catalog = read_catalog(path, mode=args.mode)
        if args.command == "export":
            if args.output is None:
                raise ValueError("Cần --output để chọn file SQL.")
            export_sql(path, args.output)
        print(f"Catalog SQL: {path.resolve()}")
        print(f"{len(catalog.products)} sản phẩm · {len(catalog.faqs)} FAQ · {'DEMO' if catalog.demo else 'đã duyệt'}")
        print("Nguồn tham chiếu: products / faqs. Chroma là chỉ mục dẫn xuất.")
    except (ValueError, OSError, sqlite3.Error):
        parser.exit(1, "Không hoàn tất thao tác catalog. Kiểm tra schema, dữ liệu, chế độ và quyền đọc/ghi.\n")


if __name__ == "__main__":
    main()
