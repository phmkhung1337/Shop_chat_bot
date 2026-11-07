"""SQL Server boundary tests use the real pyodbc interface with mocked connections; no live SQL writes."""
from copy import deepcopy
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import pyodbc

from catalog import catalog_sqlserver as sqlserver
from catalog.catalog_sql import PRODUCT_FIELDS, FAQ_FIELDS
from catalog.seed_catalog import build_demo_catalog
from chatbot.shop import load_catalog


class SQLServerTests(unittest.TestCase):
    def setUp(self):
        self.raw, self.faqs = build_demo_catalog()
        self.metadata, self.products, self.faq_rows = sqlserver.validated_rows(self.raw, self.faqs)

    def mocked_snapshot(self):
        metadata = dict(zip(sqlserver.METADATA_FIELDS, self.metadata))
        metadata.update(server_name="LOCAL-TEST", database_name="ShopChatbotDemo")
        return [[metadata], [dict(zip(PRODUCT_FIELDS, row)) for row in self.products],
                [dict(zip(FAQ_FIELDS, row)) for row in self.faq_rows]]

    def test_reader_returns_200_products_from_sqlserver_without_json_or_sqlite(self):
        db = Mock()
        with patch.dict(os.environ, {"SHOP_CATALOG_BACKEND": "sqlserver"}), \
                patch.object(sqlserver, "connect", return_value=db), \
                patch.object(sqlserver, "rows", side_effect=self.mocked_snapshot()), \
                patch("catalog.catalog_sql.read_catalog") as sqlite_reader, \
                patch.object(Path, "read_text", side_effect=AssertionError("No JSON runtime source")):
            catalog = load_catalog()
        self.assertEqual(len(catalog.products), 200)
        self.assertEqual(catalog.database_path, "sqlserver://LOCAL-TEST/ShopChatbotDemo")
        self.assertEqual(len(catalog.records()), 204)
        self.assertEqual(catalog.product("DEMO-187")["name"], "Kẹo gianduja")
        sqlite_reader.assert_not_called()
        db.commit.assert_called_once()
        statements = [call.args[0].lstrip().upper() for call in db.cursor().execute.call_args_list]
        self.assertTrue(all(s.startswith(("SELECT", "SET")) for s in statements))

    def test_driver_failure_is_sanitized_and_never_falls_back(self):
        with patch.dict(os.environ, {"SHOP_CATALOG_BACKEND": "sqlserver"}), \
                patch.object(sqlserver, "connect", side_effect=pyodbc.OperationalError("PWD=not-a-real-password")), \
                patch("catalog.catalog_sql.read_catalog") as sqlite_reader:
            with self.assertRaises(ValueError) as caught:
                load_catalog()
        self.assertNotIn("not-a-real-password", str(caught.exception))
        sqlite_reader.assert_not_called()

    def test_pyodbc_rows_are_mapped_using_cursor_column_names(self):
        cursor = Mock()
        cursor.description = [("id",), ("stock_qty",)]
        cursor.fetchall.return_value = [("DEMO-001", 123)]
        self.assertEqual(sqlserver.rows(cursor), [{"id": "DEMO-001", "stock_qty": 123}])

    def test_datetimeoffset_columns_are_converted_to_iso_strings_in_queries(self):
        self.assertEqual(sqlserver.select_columns(("id", "as_of"), ("as_of",)),
                         "[id],CONVERT(nvarchar(40),[as_of],127) AS [as_of]")

    def test_import_refuses_to_overwrite_existing_ssms_data(self):
        db = Mock()
        db.cursor().fetchone.return_value = (204,)
        with patch.object(sqlserver, "connect", return_value=db):
            with self.assertRaisesRegex(ValueError, "đã có dữ liệu"):
                sqlserver.write_empty_catalog(self.raw, self.faqs)
        db.cursor().executemany.assert_not_called()
        db.rollback.assert_called_once()
        db.commit.assert_not_called()

    def test_import_error_rolls_back_metadata_and_product_batch(self):
        db = Mock()
        db.cursor().fetchone.return_value = (0,)
        db.cursor().executemany.side_effect = pyodbc.IntegrityError("private driver details")
        with patch.object(sqlserver, "connect", return_value=db):
            with self.assertRaisesRegex(ValueError, "rollback"):
                sqlserver.write_empty_catalog(self.raw, self.faqs)
        db.rollback.assert_called_once()
        db.commit.assert_not_called()

    def test_inserted_names_are_bound_values_not_sql_text(self):
        raw = deepcopy(self.raw)
        raw["products"][0]["name"] = "Bánh '); DROP TABLE dbo.products; --"
        db = Mock()
        db.cursor().fetchone.return_value = (0,)
        with patch.object(sqlserver, "connect", return_value=db):
            sqlserver.write_empty_catalog(raw, self.faqs)
        statement, values = db.cursor().executemany.call_args_list[0].args
        self.assertNotIn("DROP TABLE", statement)
        self.assertEqual(values[0][PRODUCT_FIELDS.index("name")], raw["products"][0]["name"])
        db.commit.assert_called_once()

    def test_duplicate_import_is_rejected_before_connecting(self):
        raw = deepcopy(self.raw)
        raw["products"][1]["name"] = raw["products"][0]["name"]
        with patch.object(sqlserver, "connect") as connect:
            with self.assertRaises(ValueError):
                sqlserver.write_empty_catalog(raw, self.faqs)
        connect.assert_not_called()

    def test_random_stock_is_only_allowed_for_200_demo_products(self):
        db = Mock()
        db.cursor().fetchone.side_effect = [(True,), (200,), (200, 2, 999)]
        with patch.object(sqlserver, "connect", return_value=db):
            result = sqlserver.randomize_demo_stock()
        self.assertEqual(result, {"product_count": 200, "minimum": 2, "maximum": 999})
        db.commit.assert_called_once()
        for answers in ([(False,)], [(True,), (199,)]):
            db = Mock()
            db.cursor().fetchone.side_effect = answers
            with patch.object(sqlserver, "connect", return_value=db), self.assertRaises(ValueError):
                sqlserver.randomize_demo_stock()
            self.assertFalse(any("UPDATE" in call.args[0] for call in db.cursor().execute.call_args_list))
            db.rollback.assert_called_once()

    def test_seed_has_200_bounded_persistable_quantities(self):
        self.assertEqual(len(self.raw["products"]), 200)
        self.assertTrue(all(type(p["stock_qty"]) is int and 1 <= p["stock_qty"] <= 999 for p in self.raw["products"]))
        self.assertGreater(len({p["stock_qty"] for p in self.raw["products"]}), 100)

    def test_script_is_unicode_tsql_with_transaction_and_no_existing_data_deletion(self):
        script = sqlserver.build_demo_script()
        self.assertIn("USE [ShopChatbotDemo]", script)
        self.assertIn("N'Bánh", script)
        self.assertEqual(script.count("INSERT INTO dbo.products"), 200)
        self.assertIn("ROLLBACK TRANSACTION", script)
        self.assertNotIn("DELETE FROM", script)
        self.assertNotIn("json_valid", script)
        self.assertEqual(sqlserver.sql_literal("Bánh O'Brien"), "N'Bánh O''Brien'")
        self.assertEqual(sqlserver.sql_literal(None), "NULL")


if __name__ == "__main__":
    unittest.main()
