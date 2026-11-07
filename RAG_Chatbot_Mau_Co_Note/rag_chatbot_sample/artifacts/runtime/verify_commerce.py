"""Only the local DEMO catalog. Restore reserved stock and retain a cancelled demo audit order."""
from contextlib import closing, contextmanager
from decimal import Decimal
import json
from pathlib import Path
import sys
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / '.env')
from orders import commerce
from catalog.catalog_sqlserver import connect, read_catalog
from chatbot.shop import ShopEngine
from tickets.support import SupportStore
from orders.wholesale import wholesale_price

before = read_catalog(mode='demo')
assert before.demo and before.database_path.endswith('/ShopChatbotDemo') and len(before.products) == 200
before_stock = {p['id']: p['stock_qty'] for p in before.products}
commerce.migrate()
store = commerce.CommerceStore()
rules = store.rules()
assert set(rules) == set(before_stock)
assert all(Decimal('0.05') <= Decimal(k) <= Decimal('0.2') for k in rules.values())
commerce.migrate()
assert store.rules() == rules
assert read_catalog(mode='demo').products == before.products
assert wholesale_price(100000, 100, '0.1') == 90000

buyer = 'demo_probe_' + uuid4().hex
engine = ShopEngine(mode='demo', llm_enabled=False, store=SupportStore(ROOT / 'artifacts/runtime' / (buyer + '.sqlite3')))
responses = []
def send(text, request_id=None, **kwargs):
    result = engine.handle_message(text, buyer, request_id or uuid4().hex, **kwargs)
    responses.append(dict(question=text, status=result['status'], reason=result['reason'], reply=result['reply']))
    print(json.dumps(responses[-1], ensure_ascii=False), flush=True)
    return result
order = None
try:
    assert send('Giá nhập sỉ DEMO-187 thế nào?')['reason'] == 'wholesale_quantity'
    quote = send('100 hộp')
    assert quote['status'] == 'answered'
    expected_unit = wholesale_price(before.product('DEMO-187')['price_vnd'], 100, rules['DEMO-187'])
    assert quote['quote']['lines'][0]['unit_price_vnd'] == expected_unit
    assert send('Thêm vào giỏ')['status'] == 'answered'
    assert send('Thêm 1 hộp DEMO-186 vào giỏ')['status'] == 'answered'
    cart_state = engine.state(buyer)
    cart_quote = engine.quote_cart(buyer)
    assert send('Chốt đơn giúp mình')['status'] == 'answered'
    assert store.orders(buyer) == []
    invalid = send('Xác nhận báo giá cũ', commerce_action=dict(action='confirm', signature='0' * 64))
    assert invalid['status'] == 'clarify' and store.orders(buyer) == []
    request_id = uuid4().hex
    result = send('Xác nhận chốt đơn', request_id, commerce_action=dict(action='confirm', signature=cart_quote['signature']))
    assert result['status'] == 'answered'
    order = result['order']
    assert order['demo'] and order['payment_status'] == 'pending' and len(order['lines']) == 2
    assert engine.handle_message('Xác nhận chốt đơn', buyer, request_id, commerce_action=dict(action='confirm', signature=cart_quote['signature'])) == result
    same = store.checkout(buyer, cart_state['cart_revision'], cart_state['cart'], cart_quote['signature'], mode='demo', criteria=cart_state['criteria'])
    assert same['id'] == order['id'] and len(store.orders(buyer)) == 1
    during = read_catalog(mode='demo')
    assert during.product('DEMO-187')['stock_qty'] == before_stock['DEMO-187'] - 100
    assert during.product('DEMO-186')['stock_qty'] == before_stock['DEMO-186'] - 1
    # Verify the real paid transition without leaving a fictitious paid transaction.
    with closing(connect()) as db:
        @contextmanager
        def rollback_only():
            cursor = db.cursor()
            cursor.execute('SET XACT_ABORT ON; SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;')
            try:
                yield cursor
            finally:
                db.rollback()
        with patch.object(commerce, 'transaction', rollback_only):
            paid = store.update_payment(order['id'], action='paid', reference='DEMO-TEST-' + buyer[-8:])
            assert paid['payment_status'] == 'paid' and paid['payment_reference'] and paid['paid_at']
    assert store.orders(buyer)[0]['payment_status'] == 'pending'
finally:
    for own_order in store.orders(buyer):
        if own_order['status'] == 'confirmed' and own_order['payment_status'] == 'pending':
            store.update_payment(own_order['id'], action='cancel')
after = read_catalog(mode='demo')
assert {p['id']: p['stock_qty'] for p in after.products} == before_stock
assert store.rules() == rules
report = dict(database=before.database_path, coefficient_count=len(rules), k_min=str(min(map(Decimal, rules.values()))),
              k_max=str(max(map(Decimal, rules.values()))), coefficients_persisted=True, original_prices_preserved=True,
              example_100000_qty100_k01=90000, sample_unit_price_vnd=expected_unit, order=store.orders(buyer)[0],
              duplicate_did_not_debit_again=True, payment_transition_tested_and_rolled_back=True,
              stock_restored_after_test=True, responses=responses)
(ROOT / 'artifacts/commerce_check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print('COMMERCE CHECK OK', flush=True)
