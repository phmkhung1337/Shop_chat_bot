"""Exact money, persisted coefficients and multi-turn wholesale/cart behavior; no model/network."""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from chatbot.shop import ShopEngine, load_catalog
from tickets.support import SupportStore
from orders.wholesale import wholesale_price, quote_items, validate_cart
from orders.commerce_chat import parse_commerce

ROOT = Path(__file__).resolve().parents[1]


class WholesaleTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog(ROOT / 'data/demo')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.commerce = Mock()
        self.commerce.rules.return_value = {p['id']: '0.1' for p in self.catalog.products}
        self.commerce.orders.return_value = []
        self.engine = ShopEngine(data_dir=ROOT / 'data/demo', store=SupportStore(Path(self.temp.name) / 'queue.sqlite3'), commerce=self.commerce)
        self.counter = 0

    def send(self, text, **kwargs):
        self.counter += 1
        return self.engine.handle_message(text, 'buyer', f'request_{self.counter}', **kwargs)

    def test_confirmed_percentage_point_example_and_floor(self):
        self.assertEqual(wholesale_price(100000, 100, '0.1'), 90000)
        self.assertEqual(wholesale_price(100000, 299, '0.1'), 70100)
        self.assertEqual(wholesale_price(100000, 300, '0.1'), 70000)
        self.assertEqual(wholesale_price(100000, 10000, '0.2'), 70000)
        self.assertEqual(wholesale_price(100001, 10000, '0.05'), 70001)

    def test_monotonic_for_all_allowed_coefficients_and_never_below_floor(self):
        for k in ('0.05', '0.0731', '0.1', '0.2'):
            prices = [wholesale_price(100001, n, k) for n in (1, 2, 100, 299, 300, 600, 10000)]
            self.assertEqual(prices, sorted(prices, reverse=True))
            self.assertTrue(all(Decimal(price) >= Decimal(100001) * Decimal('0.7') for price in prices))

    def test_invalid_quantity_price_or_coefficient_is_never_coerced(self):
        for price, qty, k in ((100000, True, '.1'), (100000, 0, '.1'), (100000, 10001, '.1'),
                              (-1, 10, '.1'), (100000, 10, '.0499'), (100000, 10, '.2001'),
                              (100000, 10, 'NaN'), (100000, 10, None)):
            with self.subTest(price=price, qty=qty, k=k), self.assertRaises(ValueError):
                wholesale_price(price, qty, k)

    def test_wholesale_question_asks_quantity_before_quote_and_keeps_sku(self):
        first = self.send('Giá nhập sỉ BK-001 thế nào?')
        self.assertEqual(first['reason'], 'wholesale_quantity')
        self.assertIn('bao nhiêu', first['reply'])
        self.commerce.rules.assert_not_called()
        second = self.send('100 hộp')
        self.assertEqual(second['status'], 'answered')
        line = second['quote']['lines'][0]
        self.assertEqual((line['product_id'], line['quantity'], line['unit_price_vnd']), ('BK-001', 100, 58500))
        self.assertEqual(line['line_total_vnd'], 5850000)
        self.assertIn('sql:wholesale_rules/BK-001', second['source_ids'])

    def test_generic_wholesale_then_quantity_then_product(self):
        self.assertEqual(self.send('Shop có giá sỉ không?')['reason'], 'wholesale_quantity')
        self.assertEqual(self.send('100')['reason'], 'wholesale_product')
        final = self.send('BK-001')
        self.assertEqual(final['quote']['lines'][0]['quantity'], 100)

    def test_unknown_sku_is_not_replaced_by_previous_product(self):
        self.send('Giá sỉ BK-001?')
        result = self.send('Giá sỉ XX-999 100 hộp')
        self.assertEqual(result['status'], 'clarify')
        self.assertIsNone(result.get('quote'))
        self.assertIn('XX-999', result['reply'])

    def test_two_skus_get_separate_quantity_discounts_and_retail_stays_retail(self):
        items = [dict(product_id='BK-001', quantity=2, wholesale=True), dict(product_id='BK-004', quantity=3, wholesale=False)]
        quote = quote_items(self.catalog, items, self.commerce.rules(), require_stock=False)
        self.assertEqual(quote['lines'][0]['unit_price_vnd'], 64870)
        self.assertEqual(quote['lines'][1]['unit_price_vnd'], 49000)
        self.assertEqual(quote['total_vnd'], 64870 * 2 + 49000 * 3)

    def test_cart_add_aggregates_sku_but_cannot_confirm_from_chat(self):
        self.assertEqual(self.send('Thêm 2 hộp BK-001 vào giỏ')['status'], 'answered')
        self.assertEqual(self.send('Thêm 3 hộp BK-004 vào giỏ')['status'], 'answered')
        self.assertEqual(self.send('Thêm 1 hộp BK-001 vào giỏ')['status'], 'answered')
        cart = self.engine.state('buyer')['cart']
        self.assertEqual({item['product_id']: item['quantity'] for item in cart}, {'BK-001': 3, 'BK-004': 3})
        preview = self.send('Chốt đơn giúp mình')
        self.assertEqual(preview['status'], 'answered')
        self.assertIn('Xác nhận chốt đơn', preview['reply'])
        self.commerce.checkout.assert_not_called()

    def test_negated_and_indirect_cart_requests_do_not_change_cart(self):
        self.send('Thêm 1 hộp BK-001 vào giỏ')
        before = self.engine.state('buyer')['cart']
        self.engine.llm_enabled = True
        self.engine.rag = Mock()
        self.engine.rag.extract.return_value = dict(intent='cart_add', changes={}, commerce_items=[dict(product_id='BK-001', quantity=2)])
        for text in ('Đừng thêm 2 hộp BK-001 vào giỏ', 'Đừng xóa giỏ hàng',
                     'Thêm 2 hộp BK-001 vào giỏ được không?', 'Cho mình biết cách thêm 2 hộp BK-001 vào giỏ',
                     'Cho mình hai hộp BK-001 để mua sau'):
            with self.subTest(text=text):
                self.assertEqual(self.send(text)['reason'], 'cart_intent')
                self.assertEqual(self.engine.state('buyer')['cart'], before)

    def test_cart_rejects_fractional_and_ambiguous_quantities(self):
        for question in (
            'Thêm BK-001 2.5 hộp vào giỏ',
            'Thêm 2,5 hộp BK-001 vào giỏ',
            'Thêm 2 hộp BK-001 và BK-004 vào giỏ',
        ):
            with self.subTest(question=question):
                self.assertEqual(self.send(question)['status'], 'clarify')
                self.assertEqual(self.engine.state('buyer').get('cart', []), [])
        result = self.send('Thêm 2 hộp BK-001 và 3 hộp BK-004 vào giỏ')
        self.assertEqual(result['status'], 'answered')
        self.assertEqual([line['quantity'] for line in result['quote']['lines']], [2, 3])

    def test_wholesale_and_return_policy_share_one_answer(self):
        result = self.send('Giá sỉ 10 hộp BK-001 và đổi trả thế nào?')
        self.assertEqual(result['status'], 'answered')
        self.assertEqual([part['kind'] for part in result['parts']], ['commerce', 'faq'])
        self.assertIn('FAQ-RETURN@demo-1', result['source_ids'])
        self.assertEqual(result['quote']['lines'][0]['quantity'], 10)
        result = self.send('Giá sỉ 10 hộp BK-001 và có khuyến mãi không?')
        self.assertEqual(result['status'], 'partially_answered')
        self.assertIsNotNone(result['ticket_id'])

    def test_remove_from_cart_succeeds_when_remaining_items_are_out_of_stock(self):
        self.send('Thêm 1 hộp BK-001 vào giỏ')
        self.send('Thêm 1 hộp BK-004 vào giỏ')
        for sku in ('BK-001', 'BK-004'):
            self.catalog.product(sku).update(availability='out_of_stock', stock_qty=0)
        with patch.object(self.engine, 'refresh', return_value=self.catalog):
            result = self.send('Bỏ sản phẩm khỏi giỏ.', commerce_action={'action': 'remove', 'items': [dict(product_id='BK-001')]})
            self.assertEqual(result['status'], 'answered')
            self.assertIn('Chưa thể chốt đơn', result['reply'])
            self.assertEqual([item['product_id'] for item in self.engine.state('buyer')['cart']], ['BK-004'])
            with self.assertRaises(ValueError):
                self.engine.quote_cart('buyer')

    def test_overstock_and_unproven_allergen_cannot_enter_cart(self):
        stock = self.catalog.product('BK-001')['stock_qty']
        result = self.send('Thêm vào giỏ', commerce_action={'action': 'add', 'items': [dict(product_id='BK-001', quantity=stock + 1)]})
        self.assertEqual(result['status'], 'clarify')
        self.assertEqual(self.engine.state('buyer').get('cart', []), [])
        result = self.send('Thêm 1 hộp BK-002 vào giỏ, không chứa sữa')
        self.assertEqual(result['status'], 'clarify')
        self.assertEqual(self.engine.state('buyer')['criteria']['avoid_allergens'], ['milk'])
        self.assertEqual(self.engine.state('buyer').get('cart', []), [])

    def test_allergen_restriction_applies_to_every_sku_in_one_cart_command(self):
        result = self.send('Thêm 1 hộp BK-001 và 1 hộp BK-002 vào giỏ, không chứa sữa')
        self.assertEqual(result['status'], 'clarify')
        self.assertEqual(self.engine.state('buyer')['criteria']['avoid_allergens'], ['milk'])
        self.assertEqual(self.engine.state('buyer').get('cart', []), [])

    def test_checkout_requires_signature_and_duplicate_request_never_checks_out_twice(self):
        self.send('Thêm 1 hộp BK-001 vào giỏ')
        self.commerce.checkout.return_value = dict(id='DH-TEST', total_vnd=65000, status='confirmed', payment_status='pending', demo=True, lines=[])
        signature = self.engine.quote_cart('buyer')['signature']
        result = self.send('Xác nhận chốt đơn', commerce_action={'action': 'confirm', 'signature': signature})
        self.assertEqual(result['order']['payment_status'], 'pending')
        self.assertEqual(self.engine.state('buyer')['cart'], [])
        again = self.engine.handle_message('Xác nhận chốt đơn', 'buyer', f'request_{self.counter}', commerce_action={'action': 'confirm', 'signature': signature})
        self.assertEqual(result, again)
        self.commerce.checkout.assert_called_once()

    def test_payment_chat_cannot_mark_an_order_paid(self):
        self.engine.llm_enabled = True
        self.engine.rag = Mock()
        self.commerce.orders.return_value = [dict(id='DH-TEST', total_vnd=65000, status='confirmed', payment_status='pending', demo=True)]
        result = self.send('Tôi đã chuyển khoản thanh toán rồi')
        self.assertIn('chờ thanh toán', result['reply'])
        self.engine.rag.answer.assert_not_called()
        self.commerce.update_payment.assert_not_called()

    def test_cart_schema_rejects_duplicates_and_invalid_types(self):
        for items in ([], [{'product_id': 'BK-001', 'quantity': True}],
                      [dict(product_id='BK-001', quantity=1), dict(product_id='BK-001', quantity=2)]):
            with self.assertRaises(ValueError):
                validate_cart(items)

    def test_unknown_or_stale_base_price_is_not_replaced_with_a_guess(self):
        for field in ('price_vnd', 'price_updated_at'):
            catalog = deepcopy(self.catalog)
            catalog.product('BK-001')[field] = None
            with self.assertRaises(ValueError):
                quote_items(catalog, [dict(product_id='BK-001', quantity=100, wholesale=True)], self.commerce.rules(), require_stock=False)


if __name__ == '__main__':
    unittest.main()
