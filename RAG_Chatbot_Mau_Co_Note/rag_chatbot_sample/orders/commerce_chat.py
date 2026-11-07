"""Conversation/cart orchestration. The model cannot confirm an order or mark it paid."""
from uuid import uuid4
import re

from orders.wholesale import CommerceAction, quote_items, validate_cart
from pydantic import ValidationError


def parse_commerce(question, state, catalog):
    from chatbot.shop import normalize, contains, exact_products
    q = normalize(question)
    wholesale = any(contains(q, term) for term in ('sỉ', 'buôn', 'wholesale'))
    actions = [('clear', ('xóa giỏ', 'xóa hết giỏ')), ('remove', ('bỏ khỏi giỏ', 'xóa khỏi giỏ')),
               ('add', ('thêm vào giỏ', 'thêm giỏ', 'cho vào giỏ', 'vào giỏ')),
               ('review', ('chốt đơn', 'đặt hàng', 'xem giỏ', 'giỏ hàng', 'checkout')),
               ('orders', ('xem đơn', 'đơn của tôi', 'trạng thái đơn')), ('payment', ('thanh toán', 'đã chuyển khoản'))]
    matched_actions = [name for name, terms in actions if any(contains(q, term) for term in terms)]
    action = matched_actions[0] if matched_actions else None
    if action in {'add', 'remove', 'clear'}:
        # shortcut: accept direct commands only; use the cart buttons for indirect requests.
        command = (r'^(?:moc oi[, ]+)?(?:(?:minh|toi|em) (?:muon|can) |vui long |hay |giup (?:minh|toi) )?'
                   r'(?:them|cho|bo|xoa)\b')
        indirect = (r'\b(?:neu|gia su|vi du|cach|huong dan|duoc khong|hay khong|thi sao)\b'
                    r'|\b(?:dung|chua|khong)\s+(?:(?:can|muon|nen|duoc|the)\s+)*(?:them|cho|bo|xoa)\b')
        if (not re.match(command, q) or q.endswith('?') or re.search(indirect, q)
                or len({'add', 'remove', 'clear'}.intersection(matched_actions)) > 1):
            return {'action': 'clarify'}
    number = r'[+-]?\d+(?:[.,]\d+)*'
    unit = r'(?:hop|goi|tui|hu|cai|san pham)'
    bare = re.fullmatch(rf'(?:khoang|tam|nhap|lay|mua)?\s*({number})\s*{unit}?[.!]?', q)
    if wholesale or (state.get('pending_wholesale') and bare):
        action = action or 'wholesale'
    raw_skus = re.findall(r'\b[A-Za-z]{2,10}-\d{2,10}\b', question)
    if action is None and state.get('wholesale_quantity') and raw_skus:
        action = 'wholesale'
    if action is None:
        return None
    matches = [p for p in catalog.products if contains(question, p['id'])] or exact_products(question, catalog)
    if not matches and action == 'wholesale':
        ids = [item.get('product_id') for item in state.get('pending_wholesale', [])]
        matches = [catalog.product(pid) for pid in ids if pid and catalog.product(pid)]
    amounts = re.findall(rf'(?<![\w.,+-])({number})\s*{unit}\b', q)
    quantity = state.get('wholesale_quantity') if action == 'wholesale' else None
    if bare:
        amounts = [bare[1]]
    if action in {'add', 'wholesale'} and any(not value.isdecimal() for value in amounts):
        return {'action': 'clarify_quantity'}
    if len(amounts) == 1 and (len(matches) <= 1 or contains(q, 'mỗi loại')):
        quantity = int(amounts[0])
    elif len(matches) > 1:
        quantity = None
    unknown_skus = [sku.upper() for sku in raw_skus if catalog.product(sku.upper()) is None]
    if unknown_skus:
        return dict(action=action, items=[dict(product_id=sku, quantity=quantity, wholesale=wholesale) for sku in unknown_skus])
    items = []
    for product in matches:
        pid = re.escape(normalize(product['id']))
        before = re.search(rf'(?<![\w.,+-])({number})\s*(?:{unit}\s+)?{pid}\b', q)
        after = re.search(rf'\b{pid}(?:\s*(?:x|:|so luong)\s*|\s+)({number})(?!\w|[.,]\d)', q)
        count = quantity
        if action in {'add', 'wholesale'} and (before or after):
            values = {match[1] for match in (before, after) if match}
            if len(values) != 1:
                return {'action': 'clarify_quantity'}
            value = values.pop()
            if not value.isdecimal():
                return {'action': 'clarify_quantity'}
            count = int(value)
        items.append(dict(product_id=product['id'], quantity=count, wholesale=wholesale))
    if not items and quantity is not None and action == 'wholesale':
        items = [dict(product_id=None, quantity=quantity)]
    return dict(action=action, items=items)


def quote_text(quote):
    from chatbot.shop import money
    text = []
    for line in quote['lines']:
        kind = 'giá sỉ' if line['wholesale'] else 'giá lẻ'
        text.append(f"{line['name']} ({line['product_id']}) · {line['quantity']} {line['unit']}: {kind} {money(line['unit_price_vnd'])}/{line['unit']}, thành tiền {money(line['line_total_vnd'])}.")
        if line['stock_qty'] is None or line['stock_qty'] < line['quantity']:
            text.append('Số lượng này chưa đủ tồn kho đã xác minh để chốt đơn.')
    text.append('Tổng tiền hàng: ' + money(quote['total_vnd']) + '. Chưa gồm phí giao hàng nếu có.')
    return '\n\n'.join(text)


def order_text(order):
    from chatbot.shop import money
    payment = {'pending': 'chờ thanh toán', 'paid': 'đã được nhân viên ghi nhận thanh toán'}[order['payment_status']]
    status = 'đã hủy' if order['status'] == 'cancelled' else 'đã chốt'
    return f"Đơn {order['id']} {status} · {money(order['total_vnd'])} tiền hàng · {payment}." + (' Đây là đơn demo.' if order['demo'] else '')


def handle_commerce(engine, action, state, catalog, *, conversation_id):
    result = dict(status='answered', reply='', products=[], product_ids=[], source_ids=[], parts=[], reason=None,
                  ticket_needed=False, checked_product_ids=[], model_calls=0, skip_generation=True)
    if action.get('action') == 'clarify':
        return dict(result, status='clarify', reason='cart_intent',
                    reply='Bạn muốn thay đổi giỏ hàng thế nào? Hãy nêu rõ thao tác hoặc dùng nút trong giỏ hàng.')
    if action.get('action') == 'clarify_quantity':
        return dict(result, status='clarify', reason='commerce_quantity',
                    reply='Bạn cho mình số lượng nguyên từ 1 đến 10.000 cho từng SKU nhé.')
    store = engine.commerce_store()
    cart = state.get('cart', [])
    try:
        action = CommerceAction.model_validate(action).model_dump()
        kind, items = action['action'], action['items']
        if kind == 'wholesale':
            if not items:
                items = state.get('pending_wholesale') or [{'product_id': state.get('criteria', {}).get('requested_id'), 'quantity': None}]
            if any(item.get('quantity') is None for item in items):
                state['pending_wholesale'] = items
                result.update(status='clarify', reason='wholesale_quantity', reply='Bạn dự kiến nhập khoảng bao nhiêu sản phẩm mỗi loại? Cho mình biết số lượng và tên hoặc SKU để tính giá sỉ nhé.')
                return result
            if any(not item.get('product_id') for item in items):
                state['wholesale_quantity'] = items[0]['quantity']
                result.update(status='clarify', reason='wholesale_product', reply='Bạn muốn nhập sỉ mặt hàng nào? Hãy cho mình tên hoặc SKU nhé.')
                return result
            items = [{**item, 'wholesale': True} for item in items]
            quote = quote_items(catalog, items, store.rules(), require_stock=False)
            state.pop('pending_wholesale', None)
            state.pop('wholesale_quantity', None)
            state['last_wholesale_items'] = items
            result.update(quote=quote, reply=quote_text(quote))
        elif kind in {'add', 'set', 'remove', 'clear'}:
            if kind == 'add' and not items:
                items = state.get('last_wholesale_items', [])
            if kind == 'remove' and not items:
                raise ValueError('Bạn muốn bỏ SKU nào khỏi giỏ hàng?')
            merged = {item['product_id']: dict(item) for item in cart}
            if kind == 'clear':
                merged = {}
            elif kind == 'remove':
                for item in items:
                    merged.pop(item['product_id'], None)
            else:
                for item in validate_cart(items):
                    previous = merged.get(item['product_id'])
                    merged[item['product_id']] = {**item, 'quantity': item['quantity'] + (previous['quantity'] if previous and kind == 'add' else 0)}
            cart = list(merged.values())
            quote_error = None
            try:
                quote = quote_items(catalog, cart, store.rules(), criteria=state['criteria']) if cart else None
            except ValueError as exc:
                if kind != 'remove':
                    raise
                quote, quote_error = None, str(exc)
            state.update(cart=cart, cart_revision=uuid4().hex)
            if quote_error:
                result['reply'] = 'Đã cập nhật giỏ hàng. Chưa thể chốt đơn với các món còn lại. ' + quote_error
            elif quote:
                result.update(quote=quote, reply='Đã cập nhật giỏ hàng.\n\n' + quote_text(quote) + '\n\nBạn xem giỏ hàng rồi chọn Xác nhận chốt đơn khi đã đồng ý.')
            else:
                result.update(quote=None, reply='Giỏ hàng hiện trống.')
        elif kind in {'review', 'confirm'}:
            if not cart:
                raise ValueError('Giỏ hàng đang trống. Hãy thêm sản phẩm và số lượng trước nhé.')
            if kind == 'review':
                quote = quote_items(catalog, cart, store.rules(), criteria=state['criteria'])
                result.update(quote=quote, reply=quote_text(quote) + '\n\nĐây là bản xem trước. Mở tab Giỏ hàng và chọn Xác nhận chốt đơn sau khi kiểm tra.')
            else:
                order = store.checkout(conversation_id, state['cart_revision'], cart, action.get('signature'), mode=engine.mode, criteria=state['criteria'])
                state.update(cart=[], last_order_id=order['id'])
                result.update(order=order, reply=order_text(order))
        elif kind in {'orders', 'payment'}:
            orders = store.orders(conversation_id)
            state['orders_opened'] = True
            result.update(orders=orders, reply='\n\n'.join(order_text(order) for order in orders[:5]) if orders else 'Phiên này chưa có đơn hàng. Bạn có thể thêm sản phẩm vào giỏ rồi xác nhận chốt đơn.')
            if kind == 'payment':
                result['reply'] += '\n\nChưa thu tiền online trong ứng dụng. Nhân viên chỉ ghi nhận đã thanh toán sau khi kiểm tra giao dịch thực tế; tin nhắn của khách không tự đổi trạng thái.'
        else:
            raise ValueError('Thao tác giỏ hàng không hợp lệ.')
        if result.get('quote'):
            result['product_ids'] = [line['product_id'] for line in result['quote']['lines']]
            result['source_ids'] = [f"sql:products/{pid}" for pid in result['product_ids']]
            result['source_ids'].extend('sql:wholesale_rules/' + line['product_id'] for line in result['quote']['lines'] if line['wholesale'])
        result['parts'] = [dict(kind='commerce', status='answered', reply=result['reply'])]
    except ValidationError:
        result.update(status='clarify', reason='commerce_validation', reply='Hãy kiểm tra SKU, số lượng nguyên từ 1 đến 10.000 và thao tác giỏ hàng.')
    except ValueError as exc:
        result.update(status='clarify', reason='commerce_validation', reply=str(exc))
    return result
