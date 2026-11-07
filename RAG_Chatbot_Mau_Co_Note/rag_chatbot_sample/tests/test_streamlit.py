"""Native Streamlit interaction tests; actual desktop/mobile layout is checked separately."""
import os
from pathlib import Path
import tempfile
import sqlite3
import json
import unittest
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from chatbot.shop import ShopEngine
from tickets.support import SupportStore

ROOT = Path(__file__).resolve().parents[1]


class StreamlitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "ui.sqlite3"
        from catalog.catalog_sql import write_catalog
        catalog_db = Path(self.temp.name) / "catalog.sqlite3"
        raw = json.loads((ROOT / "data" / "demo" / "products.json").read_text(encoding="utf-8"))
        faqs = json.loads((ROOT / "data" / "demo" / "faq.json").read_text(encoding="utf-8"))
        write_catalog(catalog_db, raw, faqs)
        self.enterContext(patch.dict(os.environ, {"SHOP_MODE": "demo", "SHOP_LLM_ENABLED": "false", "SHOP_DB_PATH": str(self.db), "SHOP_CATALOG_BACKEND": "sqlite", "SHOP_CATALOG_DB": str(catalog_db)}))

    def app(self, name="streamlit_app.py"):
        at = AppTest.from_file(str(ROOT / name), default_timeout=20).run()
        self.assertFalse(at.exception, [e.message for e in at.exception])
        return at

    def test_initial_render_is_demo_and_does_not_create_ticket(self):
        at = self.app()
        self.assertEqual(len(at.chat_input), 1)
        self.assertEqual(SupportStore(self.db).list_tickets(), [])
        self.assertIn("LOCAL DEMO", " ".join(m.value for m in at.markdown))

    def test_cart_requires_price_review_and_explicit_confirmation_before_checkout(self):
        from unittest.mock import Mock
        commerce = Mock()
        commerce.rules.return_value = {'BK-001': '0.1000'}
        commerce.orders.return_value = []
        at = self.app()
        at.session_state['shop_engine'].commerce = commerce
        next(n for n in at.number_input if n.key == 'buy_qty_catalog_BK-001').set_value(2).run()
        next(c for c in at.checkbox if c.key == 'buy_wholesale_catalog_BK-001').check().run()
        next(b for b in at.button if b.key == 'add_cart_catalog_BK-001').click().run()
        self.assertFalse(at.exception, [e.message for e in at.exception])
        self.assertEqual(at.session_state['shop_engine'].state(at.session_state['conversation_id'])['cart'][0]['quantity'], 2)
        next(b for b in at.button if b.label == 'Xác nhận chốt đơn').click().run()
        commerce.checkout.assert_not_called()
        next(c for c in at.checkbox if c.label == 'Tôi đã kiểm tra số lượng, loại giá và tổng tiền hàng').check()
        commerce.checkout.return_value = dict(id='DH-TEST', total_vnd=129740, status='confirmed', payment_status='pending', demo=True, lines=[])
        next(b for b in at.button if b.label == 'Xác nhận chốt đơn').click().run()
        self.assertFalse(at.exception, [e.message for e in at.exception])
        self.assertEqual(commerce.checkout.call_count, 1, str(at.session_state['last_result']))
        self.assertEqual(at.session_state['last_result']['order']['payment_status'], 'pending')
        self.assertEqual(at.session_state['shop_engine'].state(at.session_state['conversation_id'])['cart'], [])

    def test_unsaved_cart_quantity_disables_checkout(self):
        from unittest.mock import Mock
        commerce = Mock()
        commerce.rules.return_value = {'BK-001': '0.1000'}
        at = self.app()
        at.session_state['shop_engine'].commerce = commerce
        next(b for b in at.button if b.key == 'add_cart_catalog_BK-001').click().run()
        next(n for n in at.number_input if n.key == 'cart_qty_BK-001').set_value(2).run()
        self.assertTrue(next(b for b in at.button if b.label == 'Xác nhận chốt đơn').disabled)
        commerce.checkout.assert_not_called()

    def test_chat_uses_llm_generation_and_keeps_answer_after_ui_rerun(self):
        from unittest.mock import Mock
        rag = Mock()
        rag.extract.return_value = {"intent": "conversation", "changes": {}}
        rag.answer.return_value = ("Chào bạn! Mình có thể tư vấn món quà phù hợp với người nhận.", 1)
        with patch.dict(os.environ, {"SHOP_LLM_ENABLED": "true"}), patch('chatbot.shop_rag.ShopRAG', return_value=rag):
            at = self.app()
            self.assertEqual(at.chat_input[0].placeholder, "Nhắn cho Mộc…")
            at.chat_input[0].set_value("Xin chào, bạn giúp gì được?").run()
            self.assertFalse(at.exception, [e.message for e in at.exception])
            self.assertEqual(at.session_state['last_result']['response_origin'], 'llm')
            self.assertIn(rag.answer.return_value[0], ' '.join(m.value for m in at.markdown))
            at.run()
            self.assertEqual(rag.extract.call_count, 1)
            self.assertEqual(rag.answer.call_count, 1)

    def test_catalogue_is_visible_immediately_and_search_does_not_send_message(self):
        at = self.app()
        product_buttons = [b for b in at.button if (b.key or "").startswith("ask_catalog_")]
        self.assertEqual(len(product_buttons), 13)
        self.assertEqual(len(at.metric), 0)
        self.assertNotIn('class="hero"', " ".join(m.value for m in at.markdown))
        next(t for t in at.text_input if t.label == "Tìm theo tên hoặc SKU").set_value("BK-001").run()
        self.assertFalse(at.exception)
        self.assertEqual([b.key for b in at.button if (b.key or "").startswith("ask_catalog_")], ["ask_catalog_BK-001"])
        self.assertIsNone(at.session_state["last_result"])
        self.assertEqual(SupportStore(self.db).state(at.session_state["conversation_id"])["version"], 0)

    def test_product_button_asks_assistant_and_catalogue_can_be_restored_without_new_request(self):
        at = self.app()
        next(b for b in at.button if b.key == "ask_catalog_BK-001").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["last_result"]["product_ids"], ["BK-001"])
        self.assertEqual(len([b for b in at.button if (b.key or "").startswith("ask_catalog_")]), 1)
        state_before = SupportStore(self.db).state(at.session_state["conversation_id"])["version"]
        next(b for b in at.button if b.key == "browse_all").click().run()
        self.assertEqual(len([b for b in at.button if (b.key or "").startswith("ask_catalog_")]), 13)
        self.assertEqual(SupportStore(self.db).state(at.session_state["conversation_id"])["version"], state_before)

    def test_200_sql_products_are_paginated_and_last_sku_is_searchable(self):
        from catalog.catalog_sql import write_catalog
        from catalog.seed_catalog import build_demo_catalog
        raw, faqs = build_demo_catalog()
        write_catalog(os.environ["SHOP_CATALOG_DB"], raw, faqs, replace=True)
        at = self.app()
        self.assertEqual(len([b for b in at.button if (b.key or "").startswith("ask_catalog_")]), 20)
        next(s for s in at.selectbox if s.label == "Trang sản phẩm").select(10).run()
        self.assertFalse(at.exception)
        self.assertEqual(len([b for b in at.button if (b.key or "").startswith("ask_catalog_")]), 20)
        next(t for t in at.text_input if t.label == "Tìm theo tên hoặc SKU").set_value("DEMO-187").run()
        self.assertFalse(at.exception)
        self.assertEqual([b.key for b in at.button if (b.key or "").startswith("ask_catalog_")], ["ask_catalog_DEMO-187"])
        self.assertIsNone(at.session_state["last_result"])

    def test_chat_followup_and_widget_rerun_keep_criteria(self):
        at = self.app()
        at.chat_input[0].set_value("Tìm bánh dưới 100k mỗi hộp, không chứa sữa").run()
        self.assertFalse(at.exception, [e.message for e in at.exception])
        result = at.session_state["last_result"]
        self.assertEqual(set(result["product_ids"]), {"BK-001", "BK-004"})
        before = at.session_state["shop_engine"].state(at.session_state["conversation_id"])["version"]
        at.run()
        after = at.session_state["shop_engine"].state(at.session_state["conversation_id"])["version"]
        self.assertEqual(before, after)
        at.chat_input[0].set_value("Giá BK-001 bao nhiêu?").run()
        at.chat_input[0].set_value("Có loại rẻ hơn không?").run()
        self.assertEqual(at.session_state["last_result"]["product_ids"], ["BK-004"])
        self.assertEqual(at.session_state["last_result"]["criteria"]["avoid_allergens"], ["milk"])

    def test_two_sessions_do_not_share_history_or_criteria(self):
        alice = self.app()
        alice.chat_input[0].set_value("Tìm bánh không chứa sữa").run()
        bob = self.app()
        self.assertNotEqual(alice.session_state["conversation_id"], bob.session_state["conversation_id"])
        self.assertEqual(bob.session_state["messages"], [])
        self.assertIsNone(bob.session_state["last_result"])

    def test_support_button_creates_one_ticket_even_when_catalog_is_unavailable(self):
        with patch("chatbot.shop.load_catalog", side_effect=ValueError("catalog unavailable")):
            at = self.app()
            button = next(b for b in at.button if b.label == "Yêu cầu nhân viên")
            button.click().run()
            self.assertFalse(at.exception, [e.message for e in at.exception])
            self.assertEqual(at.session_state["last_result"]["reason"], "staff_requested")
            self.assertEqual(len(SupportStore(self.db).list_tickets()), 1)
            at.run()
            self.assertEqual(len(SupportStore(self.db).list_tickets()), 1)

    def test_criteria_form_uses_same_backend_and_requires_removal_confirmation(self):
        at = self.app()
        next(s for s in at.selectbox if s.label == "Loại sản phẩm").select("Bánh")
        next(n for n in at.number_input if n.label.startswith("Ngân sách")).set_value(100000)
        next(s for s in at.selectbox if s.label == "Phạm vi ngân sách").select("Mỗi sản phẩm")
        next(s for s in at.selectbox if s.label == "Quy cách").select("hộp")
        next(m for m in at.multiselect if m.label == "Thành phần cần tránh").set_value(["milk"])
        next(b for b in at.button if b.label == "Áp dụng tiêu chí").click().run()
        self.assertFalse(at.exception, [e.message for e in at.exception])
        self.assertEqual(set(at.session_state["last_result"]["product_ids"]), {"BK-001", "BK-004"})
        next(m for m in at.multiselect if m.label == "Thành phần cần tránh").set_value([])
        next(b for b in at.button if b.label == "Áp dụng tiêu chí").click().run()
        self.assertEqual(at.session_state["last_result"]["reason"], "confirm_restriction_removal")
        self.assertEqual(at.session_state["shop_engine"].state(at.session_state["conversation_id"])["criteria"]["avoid_allergens"], ["milk"])

    def test_partial_and_persistence_error_keep_customer_question(self):
        at = self.app()
        at.chat_input[0].set_value("Tìm bánh dưới 100k mỗi hộp và shop có khuyến mại không?").run()
        self.assertEqual(at.session_state["last_result"]["status"], "partially_answered")
        engine = at.session_state["shop_engine"]
        with patch.object(engine.store, "save", side_effect=sqlite3.OperationalError("test")):
            at.chat_input[0].set_value("Gặp nhân viên").run()
        self.assertFalse(at.exception, [e.message for e in at.exception])
        self.assertEqual(at.session_state["last_result"]["reason"], "queue_failure")
        self.assertEqual(at.session_state["messages"][-1]["question"], "Gặp nhân viên")
        self.assertIsNone(at.session_state["last_result"]["ticket_id"])
        at.run()
        self.assertEqual(len(SupportStore(self.db).list_tickets()), 1)

    def test_retry_preserves_cart_action_and_request_id_after_persistence_failure(self):
        at = self.app()
        engine = at.session_state['shop_engine']
        engine.commerce = Mock()
        engine.commerce.rules.return_value = {}
        next(b for b in at.button if b.key == 'add_cart_catalog_BK-001').click().run()
        with patch.object(engine.store, 'save', side_effect=sqlite3.OperationalError('test')):
            next(b for b in at.button if b.key == 'cart_remove_BK-001').click().run()
        failed = at.session_state['last_result']
        self.assertEqual(failed['reason'], 'queue_failure')
        next(b for b in at.button if b.label == 'Thử gửi lại').click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state['last_result']['request_id'], failed['request_id'])
        self.assertEqual(engine.state(at.session_state['conversation_id'])['cart'], [])

    def test_reset_is_explicit_and_keeps_support_records(self):
        at = self.app()
        at.chat_input[0].set_value("Tìm bánh không chứa sữa").run()
        reset = next(b for b in at.button if b.label == "Xác nhận bắt đầu lại")
        self.assertTrue(reset.disabled)
        next(c for c in at.checkbox if c.label == "Tôi đồng ý xóa lịch sử và tiêu chí").check().run()
        next(b for b in at.button if b.label == "Xác nhận bắt đầu lại").click().run()
        self.assertFalse(at.exception, [e.message for e in at.exception])
        self.assertEqual(at.session_state["messages"], [])
        self.assertEqual(at.session_state["shop_engine"].state(at.session_state["conversation_id"])["criteria"], {})

    def test_staff_receives_ticket_and_can_take_over(self):
        store = SupportStore(self.db)
        engine = ShopEngine(store=store)
        ticket = engine.handle_message("Gặp nhân viên", "customer", "req")["ticket_id"]
        at = self.app("staff_app.py")
        next(s for s in at.selectbox if s.label == "Trạng thái xử lý").select("in_progress")
        next(t for t in at.text_input if t.label == "Nhân viên phụ trách").set_value("Linh")
        next(b for b in at.button if b.label == "Lưu kết quả xử lý").click().run()
        self.assertFalse(at.exception, [e.message for e in at.exception])
        self.assertTrue(store.state("customer")["takeover"])
        self.assertEqual(store.list_tickets()[0]["assignee"], "Linh")
        self.assertEqual(store.list_tickets()[0]["id"], ticket)
        next(s for s in at.selectbox if s.label == "Trạng thái xử lý").select("resolved")
        next(c for c in at.checkbox if c.label.startswith("Bàn giao hội thoại")).check()
        next(b for b in at.button if b.label == "Lưu kết quả xử lý").click().run()
        self.assertFalse(at.exception, [e.message for e in at.exception])
        self.assertFalse(store.state("customer")["takeover"])


if __name__ == "__main__":
    unittest.main()
