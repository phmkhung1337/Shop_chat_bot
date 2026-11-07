"""Money and quotes stay in Python; k is percentage points per purchased unit."""
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class CartItem(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    product_id: Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')]
    quantity: Annotated[int, Field(ge=1, le=10000)]
    wholesale: bool = False


class RequestedItem(CartItem):
    product_id: Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')] | None = None
    quantity: Annotated[int, Field(ge=1, le=10000)] | None = None


class CommerceAction(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    action: Literal['wholesale', 'add', 'set', 'remove', 'clear', 'review', 'confirm', 'orders', 'payment']
    items: list[RequestedItem] = Field(default_factory=list, max_length=200)
    signature: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')] | None = None


def wholesale_price(base_price, quantity, k):
    if type(base_price) is not int or not 0 <= base_price <= 10**12 or type(quantity) is not int or not 1 <= quantity <= 10000:
        raise ValueError('Giá gốc hoặc số lượng nhập sỉ không hợp lệ.')
    try:
        coefficient = Decimal(str(k))
        if not coefficient.is_finite() or not Decimal('0.05') <= coefficient <= Decimal('0.2'):
            raise ValueError('Hệ số k phải từ 0,05 đến 0,2.')
    except InvalidOperation:
        raise ValueError('Hệ số k không hợp lệ.') from None
    base = Decimal(base_price)
    price = max(base * Decimal('0.70'), base * (1 - coefficient * quantity / 100))
    return int(price.to_integral_value(rounding=ROUND_CEILING))


def validate_cart(items):
    if not isinstance(items, list) or not 1 <= len(items) <= 200:
        raise ValueError('Giỏ hàng cần từ 1 đến 200 mặt hàng.')
    try:
        result = [CartItem.model_validate(item).model_dump() for item in items]
    except ValidationError:
        raise ValueError('Kiểm tra lại SKU và số lượng nguyên từ 1 đến 10.000.') from None
    if len({item['product_id'] for item in result}) != len(result):
        raise ValueError('Mỗi SKU chỉ có một dòng trong giỏ hàng.')
    return result


def quote_items(catalog, items, rules, *, criteria=None, require_stock=True):
    from chatbot.shop import eligibility
    lines = []
    restrictions = {key: value for key, value in (criteria or {}).items() if key in {'avoid_allergens', 'avoid_ingredients'}}
    for item in validate_cart(items):
        product = catalog.product(item['product_id'])
        if product is None:
            raise ValueError('Không tìm thấy SKU ' + item['product_id'] + '.')
        if product['status'] != 'active' or product['price_vnd'] is None or not catalog.fresh(product.get('price_updated_at'), 'price'):
            raise ValueError(product['name'] + ': giá/trạng thái cần được xác minh trước khi báo giá.')
        if require_stock:
            failed, unknown = eligibility(product, {**restrictions, 'quantity': item['quantity']}, catalog)
            if failed or unknown:
                raise ValueError(product['name'] + ': ' + '; '.join(failed + unknown) + '.')
        k = rules.get(product['id']) if item['wholesale'] else None
        unit_price = wholesale_price(product['price_vnd'], item['quantity'], k) if item['wholesale'] else product['price_vnd']
        stock = product['stock_qty'] if catalog.fresh(product.get('stock_updated_at'), 'stock') and product['availability'] != 'unknown' else None
        lines.append(dict(product_id=product['id'], name=product['name'], quantity=item['quantity'], wholesale=item['wholesale'],
                          k=str(k) if k is not None else None, base_price_vnd=product['price_vnd'], unit_price_vnd=unit_price,
                          line_total_vnd=unit_price * item['quantity'], unit=product['unit'], stock_qty=stock))
    signature = hashlib.sha256(json.dumps(lines, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return dict(lines=lines, total_vnd=sum(line['line_total_vnd'] for line in lines), signature=signature, demo=catalog.demo)
