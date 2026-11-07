"""SQL source, 200 distinct varieties, atomic import and provenance into the original RAG pipeline."""
from contextlib import closing, redirect_stdout
from copy import deepcopy
import io
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import chromadb

import app
from catalog.catalog_sql import connect_readonly, export_sql, read_catalog, write_catalog
from catalog.seed_catalog import build_demo_catalog
from chatbot.shop import ShopEngine, load_catalog, normalize
from chatbot.shop_rag import ShopRAG
from tickets.support import SupportStore


class CatalogSQLTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"SHOP_CATALOG_BACKEND": "sqlite"}))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.path = self.folder / "catalog.sqlite3"
        self.raw, self.faqs = build_demo_catalog()
        write_catalog(self.path, self.raw, self.faqs)

    def engine(self):
        return ShopEngine(catalog_db=self.path, store=SupportStore(self.folder / "support.sqlite3"))

    def test_exactly_200_unique_names_and_skus_are_stored_in_sql(self):
        with closing(connect_readonly(self.path)) as db:
            row = db.execute("SELECT count(*), count(DISTINCT id), count(DISTINCT name_key) FROM products").fetchone()
            self.assertEqual(tuple(row), (200, 200, 200))
            self.assertEqual(dict(db.execute("SELECT category,count(*) FROM products GROUP BY category")),
                             {"Bánh": 104, "Kẹo": 64, "Chocolate": 32})
            self.assertEqual(db.execute("SELECT count(*) FROM faqs").fetchone()[0], 4)
        catalog = read_catalog(self.path)
        self.assertTrue(catalog.demo)
        self.assertEqual(len({normalize(p["name"]) for p in catalog.products}), 200)
        self.assertEqual(len(catalog.records()), 204)

    def test_default_loader_uses_sql_without_reading_json(self):
        with patch.dict(os.environ, {"SHOP_CATALOG_DB": str(self.path)}), \
                patch.object(Path, "read_text", side_effect=AssertionError("JSON runtime fallback is forbidden")):
            catalog = load_catalog()
        self.assertEqual(len(catalog.products), 200)
        self.assertEqual(catalog.database_path, str(self.path.resolve()))

    def test_exact_product_answers_from_sql_and_sees_updated_price(self):
        engine = self.engine()
        first = engine.handle_message("Giá DEMO-187 bao nhiêu?", "sql", "first")
        self.assertEqual(first["product_ids"], ["DEMO-187"])
        original_signature = engine.catalog.signature()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE products SET price_vnd=? WHERE id=?", (77000, "DEMO-187"))
        second = engine.handle_message("Giá DEMO-187 bao nhiêu?", "sql", "second")
        self.assertEqual(second["products"][0]["price_vnd"], 77000)
        self.assertEqual(engine.catalog.signature(), original_signature)

    def test_mutable_commercial_fields_are_not_put_in_rag_documents(self):
        catalog = read_catalog(self.path)
        records = catalog.records()
        product = next(r for r in records if r["id"] == "DEMO-187")
        self.assertEqual(product["source"], "sql:products/DEMO-187")
        self.assertEqual((product["sql_table"], product["sql_id"]), ("products", "DEMO-187"))
        self.assertNotIn("price_vnd", product)
        self.assertNotIn("stock_qty", product)
        faq = next(r for r in records if r["id"] == "FAQ-RETURN")
        self.assertEqual((faq["source"], faq["sql_table"]), ("sql:faqs/FAQ-RETURN", "faqs"))

    def test_unknown_ingredients_in_generated_rows_never_become_absent(self):
        catalog = read_catalog(self.path)
        generated = [p for p in catalog.products if p["id"].startswith("DEMO-")]
        self.assertEqual(len(generated), 187)
        self.assertTrue(all(p["ingredients"]["label"] is None for p in generated))
        self.assertTrue(all(set(p["ingredients"]["allergens"].values()) == {"unknown"} for p in generated))
        result = self.engine().handle_message("Tìm bánh không chứa sữa", "sql", "restriction")
        self.assertTrue(all(not p.startswith("DEMO-") for p in result["product_ids"]))

    def test_missing_or_corrupt_sql_never_falls_back_to_json(self):
        missing = self.folder / "missing.sqlite3"
        with self.assertRaises(ValueError):
            load_catalog(catalog_db=missing)
        self.assertFalse(missing.exists())
        self.path.write_bytes(b"not a database")
        with self.assertRaises(ValueError):
            load_catalog(catalog_db=self.path)

    def test_demo_catalog_is_rejected_in_connected_mode(self):
        with self.assertRaisesRegex(ValueError, "tách riêng"):
            read_catalog(self.path, mode="connected")

    def test_reader_is_readonly_and_sql_constraints_protect_money(self):
        with closing(connect_readonly(self.path)) as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("UPDATE products SET price_vnd=1")
        with closing(sqlite3.connect(self.path)) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE products SET price_vnd=-1 WHERE id='DEMO-001'")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE products SET stock_qty=1.5 WHERE id='DEMO-001'")

    def test_duplicate_or_invalid_import_preserves_previous_snapshot(self):
        for edit in (lambda d: d["products"][1].update(name=d["products"][0]["name"]),
                     lambda d: d["products"][0].update(price_vnd=True)):
            raw = deepcopy(self.raw)
            edit(raw)
            with self.assertRaises(ValueError):
                write_catalog(self.path, raw, self.faqs, replace=True)
            self.assertEqual(len(read_catalog(self.path).products), 200)
        self.assertEqual(list(self.folder.glob("*.backup-*")), [])

    def test_existing_sql_is_not_overwritten_without_explicit_replace(self):
        with self.assertRaises(ValueError):
            write_catalog(self.path, self.raw, self.faqs)
        self.assertEqual(len(read_catalog(self.path).products), 200)

    def test_sql_insert_failure_rolls_back_whole_replacement(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TRIGGER reject_demo BEFORE INSERT ON products WHEN NEW.id='DEMO-050' BEGIN SELECT RAISE(ABORT,'test import failure'); END")
        raw = deepcopy(self.raw)
        raw["products"][0]["price_vnd"] = 99000
        with self.assertRaises(sqlite3.IntegrityError):
            write_catalog(self.path, raw, self.faqs, replace=True)
        catalog = read_catalog(self.path)
        self.assertEqual(len(catalog.products), 200)
        self.assertEqual(catalog.product("BK-001")["price_vnd"], 65000)

    def test_valid_replace_retains_usable_database_backup(self):
        raw = deepcopy(self.raw)
        raw["products"][0]["price_vnd"] = 99000
        backup = write_catalog(self.path, raw, self.faqs, replace=True)
        self.assertEqual(read_catalog(backup).product("BK-001")["price_vnd"], 65000)
        self.assertEqual(read_catalog(self.path).product("BK-001")["price_vnd"], 99000)

    def test_sql_export_roundtrips_into_a_fresh_database(self):
        exported = self.folder / "catalog.sql"
        export_sql(self.path, exported)
        restored = self.folder / "restored.sqlite3"
        with closing(sqlite3.connect(restored)) as db:
            db.executescript(exported.read_text(encoding="utf-8"))
        self.assertEqual(read_catalog(restored).signature(), read_catalog(self.path).signature())
        self.assertEqual(len(read_catalog(restored).products), 200)

    def test_names_are_parameterized_values_not_sql_statements(self):
        raw = deepcopy(self.raw)
        raw["products"][0]["name"] = "Bánh '); DROP TABLE products; --"
        write_catalog(self.path, raw, self.faqs, replace=True)
        self.assertEqual(len(read_catalog(self.path).products), 200)
        self.assertEqual(read_catalog(self.path).product("BK-001")["name"], raw["products"][0]["name"])

    def test_sql_records_go_through_original_chroma_index_and_keep_sql_ids(self):
        catalog = read_catalog(self.path)
        db = chromadb.EphemeralClient()
        rag = ShopRAG()
        with patch.object(app.chromadb, "PersistentClient", return_value=db), \
                patch.object(app, "embed_texts", side_effect=lambda texts, **kw: [[float(b + 1) for b in hashlib.sha256(text.encode()).digest()[:4]] for text in texts]), \
                redirect_stdout(io.StringIO()):
            collection = rag.rebuild(catalog)
            self.assertEqual(collection.count(), 204)
            rows = rag.retrieve(catalog, "kẹo gianduja", "product", ["DEMO-187"])
            self.assertEqual([r["sql_id"] for r in rows], ["DEMO-187"])
            self.assertEqual(rows[0]["sql_table"], "products")
            self.assertEqual(rows[0]["source"], "sql:products/DEMO-187")
            with closing(sqlite3.connect(self.path)) as sql, sql:
                sql.execute("UPDATE products SET description=? WHERE id=?", ("Mô tả vừa thay đổi trong SQL", "DEMO-187"))
            changed = read_catalog(self.path)
            with self.assertRaises(ValueError):
                rag.ensure_current(changed)
            self.assertEqual(collection.count(), 204)


if __name__ == "__main__":
    unittest.main()
