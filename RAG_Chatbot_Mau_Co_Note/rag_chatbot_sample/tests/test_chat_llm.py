"""Generation calls the original SDK; SQL facts and request deduplication stay authoritative."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx

import app
from chatbot.shop import ShopEngine, checked_product, load_catalog, parse_demo, validate_extraction
from chatbot.shop_rag import ShopRAG
from tickets.support import SupportStore

ROOT = Path(__file__).resolve().parents[1]


class ChatLLMTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.catalog = load_catalog(ROOT / 'data/demo')
        self.store = SupportStore(Path(self.temp.name) / 'support.sqlite3')

    def test_approved_faq_does_not_depend_on_vector_index_or_generation(self):
        rag = Mock()
        rag.extract.return_value = {"intent": "faq", "changes": {}}
        rag.retrieve.side_effect = RuntimeError("index unavailable")
        engine = ShopEngine(data_dir=ROOT / 'data/demo', store=self.store, rag=rag, llm_enabled=True)
        result = engine.handle_message("Chính sách đổi trả thế nào?", "customer")
        self.assertEqual(result["status"], "answered")
        self.assertIn("FAQ-RETURN@demo-1", result["source_ids"])
        rag.retrieve.assert_not_called()
        rag.answer.assert_not_called()

    def test_explicit_handoff_works_when_catalog_and_model_are_unavailable(self):
        rag = Mock()
        rag.extract.side_effect = httpx.ReadTimeout("model unavailable")
        engine = ShopEngine(data_dir=ROOT / 'data/demo', store=self.store, rag=rag, llm_enabled=True)
        with patch.object(engine, "refresh", side_effect=ValueError("catalog unavailable")):
            result = engine.handle_message("Tôi muốn gặp nhân viên hỗ trợ.", "customer")
        self.assertEqual(result["reason"], "staff_requested")
        self.assertIsNotNone(result["ticket_id"])
        rag.extract.assert_not_called()

    def generate(self, reply, *, source_ids=None, products=True, policy=None):
        outcome = dict(products=[checked_product(self.catalog.product('BK-001'), self.catalog, direct=True)] if products else [],
                       reply='Đã đối chiếu dữ liệu.', source_ids=['BK-001@demo-v1'] if products else [], parts=[])
        if policy:
            outcome['parts'].append(dict(kind='faq', reply=policy))
        self.payload = None
        replies = iter(reply if isinstance(reply, list) else [reply, reply])
        def handler(request):
            body = json.loads(request.content)
            self.assertFalse(body['stream'])
            self.assertEqual(request.url.path, '/api/chat')
            self.payload = json.loads(body['messages'][1]['content'])
            return httpx.Response(200, json={'done': True, 'message': {'role': 'assistant', 'content': json.dumps(dict(
                reply=next(replies), source_ids=outcome['source_ids'] if source_ids is None else source_ids))}})
        client = app.Client(host='https://ollama.com', transport=httpx.MockTransport(handler))
        with patch.object(app, 'create_ollama_client', return_value=client):
            text, self.answer_calls = ShopRAG().answer('Mình hỏi tiếp về món vừa xem nhé.',
                                    {'criteria': {}, 'history': [{'role': 'user', 'content': 'Mua làm quà'}]}, self.catalog, outcome)
            return text

    def test_original_client_generates_conversation_with_checked_facts_and_history(self):
        reply = self.generate('Bạn có thể chọn {{BK-001.name}}, giá {{BK-001.price}}, còn {{BK-001.stock}}. Bạn muốn tặng ai?')
        self.assertIn('65.000 ₫/hộp', reply)
        self.assertIn('Bánh yến mạch nguyên bản', reply)
        self.assertNotIn('{{', reply)
        self.assertEqual(self.payload['history'][0]['content'], 'Mua làm quà')

    def test_stock_tokens_use_current_facts_and_unknown_price_is_not_revealed(self):
        p = self.catalog.product('BK-001')
        p.update(stock_qty=987, price_updated_at=None)
        reply = self.generate('{{BK-001.name}}: {{BK-001.price}}, còn {{BK-001.stock}}.')
        self.assertIn('987 hộp', reply)
        self.assertIn('giá cần xác minh', reply)
        self.assertNotIn('65.000', reply)

    def test_unknown_facts_numbers_sources_and_allergy_claims_are_blocked(self):
        for reply in ('{{BK-001.name}} giá 999 đồng.', '{{BK-001.name}} {{XX-999.price}}',
                      '{{BK-001.name}} không chứa đậu nành.', '{{BK-001.name}} phù hợp với người dị ứng sữa.',
                      '{{BK-001.name}} miễn phí giao hàng.', '{{BK-001.name}} Khoản chuyển khoản đã được ghi nhận.',
                      '{{BK-001.name}} mình hỗ trợ đặt hàng nhé.',
                      '{{BK-001.name}} <script>x</script>'):
            with self.subTest(reply=reply), self.assertRaises(ValueError):
                self.generate(reply)
        with self.assertRaises(ValueError):
            self.generate('{{BK-001.name}}', source_ids=['FAKE'])

    def test_approved_policy_conditions_are_inserted_verbatim(self):
        policy = 'Chỉ đổi trong 3 ngày, còn nguyên bao bì.'
        self.assertEqual(self.generate('Mình gửi bạn thông tin: {{policy}}', products=False, policy=policy),
                         'Mình gửi bạn thông tin: ' + policy)
        with self.assertRaises(ValueError):
            self.generate('Bạn cứ mang đổi nhé.', products=False, policy=policy)

    def test_product_count_binding_is_computed_and_safe_disclaimer_is_not_blocked(self):
        reply = self.generate('Có {{product_count}} lựa chọn: {{BK-001.name}}. Chỉ đối chiếu bằng chứng nhãn đã xác minh; đây không phải cam kết an toàn dị ứng.')
        self.assertIn('Có 1 lựa chọn:', reply)

    def test_greeting_capabilities_do_not_promise_online_payment(self):
        reply = self.generate('Chào bạn! {{capabilities}} Bạn muốn mua dùng hay làm quà?', products=False)
        self.assertIn('Chưa thu tiền online', reply)

    def test_ingredient_question_uses_verified_label_without_model_generation(self):
        rag = Mock()
        rag.extract.return_value = dict(intent='product_fact', changes={})
        engine = ShopEngine(data_dir=ROOT / 'data/demo', store=self.store, rag=rag, llm_enabled=True)
        result = engine.handle_message('BK-002 có phù hợp với người dị ứng sữa không?', 'customer')
        self.assertEqual(result['status'], 'answered')
        self.assertIn('Nhãn thành phần: Bột mì, bơ sữa', result['reply'])
        self.assertIn('cam kết an toàn dị ứng', result['reply'])
        rag.answer.assert_not_called()

    def test_invalid_first_generation_is_corrected_once_without_publishing_it(self):
        reply = self.generate(['{{BK-001.name}} giá 999 đồng.', '{{BK-001.name}} giá {{BK-001.price}}.'])
        self.assertEqual(self.answer_calls, 2)
        self.assertNotIn('999', reply)
        self.assertIn('65.000', reply)

    def test_demo_data_can_call_llm_and_followup_receives_history_without_duplicate_calls(self):
        rag = Mock()
        rag.extract.return_value = dict(intent='conversation', changes={})
        rag.answer.return_value = ('Chào bạn! Bạn muốn mua dùng hay làm quà?', 1)
        engine = ShopEngine(data_dir=ROOT / 'data/demo', store=self.store, rag=rag, llm_enabled=True)
        first = engine.handle_message('Chào bạn', 'customer', 'hello')
        self.assertEqual(first['response_origin'], 'llm')
        self.assertEqual(first['model_calls'], 2)
        self.assertTrue(first['demo'])
        self.assertEqual(first['product_ids'], [])
        self.assertEqual(first, engine.handle_message('Chào bạn', 'customer', 'hello'))
        self.assertEqual(rag.answer.call_count, 1)
        engine.handle_message('Mình mua làm quà', 'customer', 'followup')
        history = rag.extract.call_args.args[1]['history']
        self.assertEqual(history[0]['content'], 'Chào bạn')
        self.assertEqual(history[1]['content'], first['reply'])

    def test_generation_timeout_never_publishes_partial_text_or_loses_question(self):
        rag = Mock()
        rag.extract.return_value = dict(intent='conversation', changes={})
        rag.answer.side_effect = httpx.ReadTimeout('provider secret')
        engine = ShopEngine(data_dir=ROOT / 'data/demo', store=self.store, rag=rag, llm_enabled=True)
        result = engine.handle_message('Chào bạn', 'customer', 'hello')
        self.assertEqual(result['status'], 'error')
        self.assertNotIn('provider secret', result['reply'])
        self.assertEqual(self.store.state('customer')['history'], [])
        self.assertEqual(self.store.list_tickets()[0]['question'], 'Chào bạn')

    def test_missing_llm_quote_only_accepts_criteria_independently_proven_by_existing_parser(self):
        question = 'Tìm bánh dưới 100k mỗi hộp, không chứa sữa'
        explicit = parse_demo(question, {}, self.catalog)['changes']
        value = dict(intent='recommendation', changes=dict(budget_vnd=100000, avoid_allergens=['milk']))
        self.assertEqual(validate_extraction(value, question, explicit_changes=explicit)['changes']['budget_vnd'], 100000)
        value['changes']['budget_vnd'] = 200000
        with self.assertRaises(ValueError):
            validate_extraction(value, question, explicit_changes=explicit)
        with self.assertRaises(ValueError):
            validate_extraction(dict(intent='recommendation', changes={'brand': 'Fake'}), question, explicit_changes=explicit)


if __name__ == '__main__':
    unittest.main()
