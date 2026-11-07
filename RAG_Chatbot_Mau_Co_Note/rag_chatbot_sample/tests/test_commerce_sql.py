"""Transactions must never leave half an order, oversell, or debit stock twice."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch, PropertyMock

import pyodbc

from orders import commerce
from chatbot.shop import load_catalog
from orders.wholesale import quote_items

ROOT = Path(__file__).resolve().parents[1]


class CommerceSQLTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog(ROOT / 'data/demo')
        self.items = [dict(product_id='BK-001', quantity=1, wholesale=True), dict(product_id='BK-004', quantity=1, wholesale=False)]
        self.rules = {p['id']: '0.1000' for p in self.catalog.products}
        self.quote = quote_items(self.catalog, self.items, self.rules)
        self.db = Mock()
        self.cursor = self.db.cursor.return_value
        self.cursor.rowcount = 1
        self.store = commerce.CommerceStore()
        self.enterContext(patch.object(commerce, 'connect', return_value=self.db))
        self.snapshot = self.enterContext(patch.object(commerce, 'read_snapshot', return_value=self.catalog))
        self.order = dict(id='DH-TEST', conversation_id='buyer', total_vnd=self.quote['total_vnd'], status='confirmed', payment_status='pending', demo=True)

    def checkout(self, signature=None):
        return self.store.checkout('buyer', 'once', self.items, signature or self.quote['signature'], mode='demo')

    def test_multiple_items_and_stock_are_committed_together_with_bound_parameters(self):
        with patch.object(commerce, 'rows', side_effect=[[], [dict(product_id=k, k=v) for k, v in self.rules.items()], [self.order], self.quote['lines']]):
            result = self.checkout()
        self.assertEqual(result['total_vnd'], self.quote['total_vnd'])
        self.db.commit.assert_called_once()
        self.db.rollback.assert_not_called()
        updates = [call for call in self.cursor.execute.call_args_list if call.args[0].startswith('UPDATE dbo.products')]
        self.assertEqual(len(updates), 2)
        self.assertTrue(all('stock_qty>=?' in call.args[0] for call in updates))
        self.assertEqual([call.args[-2] for call in updates], ['BK-001', 'BK-004'])

    def test_late_stock_failure_rolls_back_both_items_and_order_header(self):
        with patch.object(commerce, 'rows', side_effect=[[], [dict(product_id=k, k=v) for k, v in self.rules.items()]]), \
                patch.object(type(self.cursor), 'rowcount', new_callable=PropertyMock, side_effect=[1, 0], create=True):
            with self.assertRaisesRegex(ValueError, 'Tồn kho'):
                self.checkout()
        self.db.rollback.assert_called_once()
        self.db.commit.assert_not_called()

    def test_quote_changed_in_sql_requires_customer_to_review_again(self):
        changed = deepcopy(self.catalog)
        changed.product('BK-001')['price_vnd'] += 1
        self.snapshot.return_value = changed
        with patch.object(commerce, 'rows', side_effect=[[], [dict(product_id=k, k=v) for k, v in self.rules.items()]]):
            with self.assertRaisesRegex(ValueError, 'thay đổi'):
                self.checkout()
        self.db.rollback.assert_called_once()
        self.assertFalse(any(call.args[0].startswith('INSERT') for call in self.cursor.execute.call_args_list))

    def test_same_checkout_token_returns_previous_order_without_stock_or_catalog_read(self):
        request_hash = hashlib.sha256(json.dumps([self.items, self.quote['signature'], {}], sort_keys=True).encode()).hexdigest()
        previous = dict(id='DH-TEST', conversation_id='buyer', request_hash=request_hash)
        with patch.object(commerce, 'rows', side_effect=[[previous], [self.order], self.quote['lines']]):
            self.assertEqual(self.checkout()['id'], 'DH-TEST')
        self.snapshot.assert_not_called()
        self.assertFalse(any(call.args[0].startswith(('INSERT', 'UPDATE')) for call in self.cursor.execute.call_args_list))

    def test_other_conversation_cannot_reuse_checkout_token_or_read_previous_order(self):
        with patch.object(commerce, 'rows', return_value=[dict(id='PRIVATE', conversation_id='other', request_hash='x')]):
            with self.assertRaises(ValueError):
                self.checkout()
        self.db.rollback.assert_called_once()
        self.snapshot.assert_not_called()

    def test_failed_sql_write_is_sanitized_and_rolled_back(self):
        self.cursor.execute.side_effect = pyodbc.OperationalError('PWD=must-not-leak')
        with self.assertRaises(ValueError) as caught:
            self.checkout()
        self.assertNotIn('must-not-leak', str(caught.exception))
        self.db.rollback.assert_called_once()
        self.db.commit.assert_not_called()

    def test_paid_order_cannot_be_cancelled_or_restore_stock(self):
        with patch.object(commerce, 'rows', return_value=[dict(status='confirmed', payment_status='paid', payment_reference='REF')]):
            with self.assertRaisesRegex(ValueError, 'hoàn tiền'):
                self.store.update_payment('DH-TEST', action='cancel')
        self.assertFalse(any(call.args[0].startswith('UPDATE') for call in self.cursor.execute.call_args_list))
        self.db.rollback.assert_called_once()

    def test_payment_needs_real_operator_reference_and_does_not_change_stock(self):
        with self.assertRaises(ValueError):
            self.store.update_payment('DH-TEST', action='paid')
        paid = {**self.order, 'payment_status': 'paid', 'payment_reference': 'BANK-TEST'}
        with patch.object(commerce, 'rows', side_effect=[[dict(status='confirmed', payment_status='pending', payment_reference=None)], [paid], []]):
            self.assertEqual(self.store.update_payment('DH-TEST', action='paid', reference='BANK-TEST')['payment_status'], 'paid')
        statements = [call.args[0] for call in self.cursor.execute.call_args_list]
        self.assertFalse(any(s.startswith('UPDATE dbo.products') for s in statements))
        update = next(call for call in self.cursor.execute.call_args_list if call.args[0].startswith('UPDATE dbo.shop_orders'))
        self.assertEqual(update.args[1], 'BANK-TEST')

    def test_cancelling_pending_order_restores_stock_and_marks_cancelled_atomically(self):
        cancelled = {**self.order, 'status': 'cancelled'}
        with patch.object(commerce, 'rows', side_effect=[[dict(status='confirmed', payment_status='pending')],
                [dict(product_id='BK-001', quantity=2)], [cancelled], []]):
            self.assertEqual(self.store.update_payment('DH-TEST', action='cancel')['status'], 'cancelled')
        self.db.commit.assert_called_once()
        updates = [call.args[0] for call in self.cursor.execute.call_args_list if call.args[0].startswith('UPDATE')]
        self.assertEqual(len(updates), 2)
        with patch.object(commerce, 'rows', return_value=[dict(status='cancelled', payment_status='pending')]):
            with self.assertRaises(ValueError):
                self.store.update_payment('DH-TEST', action='cancel')
        self.assertEqual(len([call for call in self.cursor.execute.call_args_list if call.args[0].startswith('UPDATE')]), 2)


if __name__ == '__main__':
    unittest.main()
