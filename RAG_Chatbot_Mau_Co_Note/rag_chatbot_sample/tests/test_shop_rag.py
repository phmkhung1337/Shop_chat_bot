"""Real Chroma + SDK with tiny mocked vectors and HTTP. Never touch the demo/student index."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import chromadb
import httpx

import app
from chatbot.shop import ShopEngine, load_catalog
from chatbot.shop_rag import ShopRAG
from tickets.support import SupportStore


class RagTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog(Path(__file__).resolve().parents[1] / "data" / "demo")
        self.catalog.products[0]["version"] = uuid4().hex
        self.rag = ShopRAG()
        self.db = chromadb.EphemeralClient()
        self.enterContext(patch.object(app.chromadb, "PersistentClient", return_value=self.db))
        self.embeddings = self.enterContext(patch.object(app, "embed_texts", side_effect=lambda texts, **kw: [[1.0, 0.0] for _ in texts]))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def test_product_query_only_sees_eligible_ids_and_faq_query_is_separate(self):
        with patch.object(app, "index_knowledge_base", wraps=app.index_knowledge_base) as original_pipeline:
            collection = self.rag.rebuild(self.catalog)
            original_pipeline.assert_called_once()
        rows = self.rag.retrieve(self.catalog, "bánh", "product", ["BK-001", "BK-004"])
        self.assertEqual({r["product_id"] for r in rows}, {"BK-001", "BK-004"})
        self.assertTrue(all(r["record_type"] == "product" for r in rows))
        faq = self.rag.retrieve(self.catalog, "đổi trả", "faq", ["FAQ-RETURN"])
        self.assertEqual([r["faq_id"] for r in faq], ["FAQ-RETURN"])
        self.assertGreater(collection.count(), 3)

    def test_changed_or_removed_source_is_blocked_until_rebuild(self):
        self.rag.rebuild(self.catalog)
        self.catalog.products[0]["description"] = "Nội dung mô tả thay đổi"
        with self.assertRaisesRegex(ValueError, "cũ"):
            self.rag.ensure_current(self.catalog)
        self.rag.rebuild(self.catalog)
        self.rag.ensure_current(self.catalog)
        self.catalog.faqs[0]["approved"] = False
        with self.assertRaises(ValueError):
            self.rag.ensure_current(self.catalog)

    def test_price_stock_refresh_does_not_invalidate_stable_vectors(self):
        self.rag.rebuild(self.catalog)
        signature = self.catalog.signature()
        self.catalog.products[0]["price_vnd"] = 60000
        self.catalog.products[0]["stock_qty"] = 8
        self.assertEqual(signature, self.catalog.signature())
        self.rag.ensure_current(self.catalog)

    def test_failed_embedding_keeps_existing_collection(self):
        collection = self.rag.rebuild(self.catalog)
        count = collection.count()
        self.catalog.products[0]["description"] = "New source which needs an embedding"
        with patch.object(app, "embed_texts", side_effect=RuntimeError("test failure")):
            with self.assertRaises(RuntimeError):
                self.rag.rebuild(self.catalog)
        self.assertEqual(collection.count(), count)

    def test_no_eligible_ids_never_runs_global_query(self):
        self.assertEqual(self.rag.retrieve(self.catalog, "q", "product", []), [])
        self.embeddings.assert_not_called()

    def test_extraction_uses_original_client_and_buffers_json(self):
        def handler(request):
            body = json.loads(request.content)
            self.assertFalse(body["stream"])
            self.assertNotIn("format", body)
            self.assertEqual(request.url.path, "/api/chat")
            return httpx.Response(200, json={"message": {"role": "assistant", "content": json.dumps({"intent": "faq", "changes": {}, "faq": True})}, "done": True})
        client = app.Client(host="https://ollama.com", transport=httpx.MockTransport(handler))
        with patch.object(app, "create_ollama_client", return_value=client):
            result = self.rag.extract("Đổi trả thế nào?", {"criteria": {}, "history": []}, self.catalog)
        self.assertEqual(result["intent"], "faq")


class ConnectedFailureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        source = Path(__file__).resolve().parents[1] / "data" / "demo"
        for file in ("products.json", "faq.json"):
            data = json.loads((source / file).read_text(encoding="utf-8"))
            data["demo"] = False
            (self.path / file).write_text(json.dumps(data), encoding="utf-8")
        self.store = SupportStore(self.path / "queue.sqlite3")

    def engine(self, rag):
        from chatbot.shop import timestamp
        rag.answer.side_effect = lambda question, state, catalog, outcome: (outcome["reply"], 1)
        return ShopEngine(mode="connected", data_dir=self.path, store=self.store,
                          clock=lambda: timestamp("2026-11-05T03:00:00+00:00"), rag=rag)

    def test_bad_llm_json_clarifies_without_committing_criteria(self):
        from unittest.mock import Mock
        rag = Mock()
        rag.extract.return_value = {"intent": "recommendation", "changes": {"budget_vnd": "cheap"}}
        e = self.engine(rag)
        r = e.handle_message("Tìm bánh", "one", "one")
        self.assertEqual(r["status"], "clarify")
        self.assertEqual(r["criteria"], {})
        rag.retrieve.assert_not_called()

    def test_provider_timeout_is_error_not_no_stock(self):
        from unittest.mock import Mock
        rag = Mock()
        rag.extract.side_effect = httpx.ReadTimeout("contains-secret")
        result = self.engine(rag).handle_message("Tìm bánh", "one", "one")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "technical_failure")
        self.assertNotIn("contains-secret", result["reply"])
        self.assertNotIn("hết hàng", result["reply"])
        self.assertEqual(self.store.state("one")["history"], [])

    def test_retrieval_failure_is_error_not_empty_recommendation(self):
        from unittest.mock import Mock
        rag = Mock()
        rag.extract.return_value = {"intent": "recommendation", "changes": {}}
        rag.retrieve.side_effect = RuntimeError("index failure")
        result = self.engine(rag).handle_message("Tìm bánh", "one", "one")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["product_ids"], [])

    def test_duplicate_request_never_repeats_model_call(self):
        from unittest.mock import Mock
        rag = Mock()
        rag.extract.return_value = {"intent": "staff", "changes": {}}
        e = self.engine(rag)
        e.handle_message("Nhân viên", "one", "one")
        e.handle_message("Nhân viên", "one", "one")
        self.assertEqual(rag.extract.call_count, 1)

    def test_explicit_packaging_is_kept_even_if_model_omits_it(self):
        from unittest.mock import Mock
        rag = Mock()
        rag.extract.return_value = {"intent": "recommendation", "changes": {}}
        rag.retrieve.return_value = []
        result = self.engine(rag).handle_message("Tìm bánh dưới 100k mỗi hộp, không chứa sữa", "one", "one")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["criteria"]["packaging"], "hộp")
        self.assertEqual(set(result["product_ids"]), {"BK-001", "BK-004"})
        ids_sent_to_retrieval = rag.retrieve.call_args.args[3]
        self.assertNotIn("BK-003", ids_sent_to_retrieval)


if __name__ == "__main__":
    unittest.main()
