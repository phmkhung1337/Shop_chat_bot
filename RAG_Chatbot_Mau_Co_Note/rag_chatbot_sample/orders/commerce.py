"""SQL Server orders and stock changes share one transaction; no payment gateway is implied."""
from contextlib import closing, contextmanager
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4

import pyodbc

from catalog.catalog_sqlserver import connect, read_snapshot, rows
from orders.wholesale import quote_items, validate_cart

SCHEMA = Path(__file__).with_suffix('.sql')


@contextmanager
def transaction():
    try:
        with closing(connect()) as db:
            try:
                cursor = db.cursor()
                cursor.execute('SET XACT_ABORT ON; SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;')
                yield cursor
                db.commit()
            except Exception:
                db.rollback()
                raise
    except pyodbc.Error:
        raise ValueError('Chưa hoàn tất thao tác đơn hàng trong SQL Server; dữ liệu chưa commit đã rollback. Vui lòng tải lại để kiểm tra trước khi thử lại.') from None


def migrate():
    with transaction() as cursor:
        cursor.execute(SCHEMA.read_text(encoding='utf-8'))


class CommerceStore:
    def rules(self):
        with transaction() as cursor:
            cursor.execute('SELECT product_id,k FROM dbo.wholesale_rules')
            return {row['product_id']: str(row['k']) for row in rows(cursor)}

    def checkout(self, conversation_id, request_id, items, expected_signature, *, mode, criteria=None):
        items = validate_cart(items)
        if any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value) for value in (conversation_id, request_id)):
            raise ValueError('Mã phiên hoặc mã yêu cầu không hợp lệ.')
        if not isinstance(expected_signature, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_signature):
            raise ValueError('Cần xem và xác nhận lại báo giá trước khi chốt đơn.')
        request_hash = hashlib.sha256(json.dumps([items, expected_signature, criteria or {}], sort_keys=True).encode()).hexdigest()
        with transaction() as cursor:
            cursor.execute('SELECT id,conversation_id,request_hash FROM dbo.shop_orders WITH (UPDLOCK,HOLDLOCK) WHERE request_id=?', request_id)
            previous = rows(cursor)
            if previous:
                if previous[0]['conversation_id'] != conversation_id or previous[0]['request_hash'] != request_hash:
                    raise ValueError('Mã yêu cầu đã được dùng cho đơn hàng khác.')
                return self._order(cursor, previous[0]['id'])
            catalog = read_snapshot(cursor, mode=mode)
            cursor.execute('SELECT product_id,k FROM dbo.wholesale_rules WITH (HOLDLOCK)')
            rules = {row['product_id']: str(row['k']) for row in rows(cursor)}
            quote = quote_items(catalog, items, rules, criteria=criteria)
            if quote['signature'] != expected_signature:
                raise ValueError('Giá, hệ số hoặc tồn kho đã thay đổi. Hãy xem lại báo giá mới và xác nhận lại.')
            order_id = 'DH-' + uuid4().hex.upper()
            cursor.execute("INSERT INTO dbo.shop_orders(id,request_id,conversation_id,request_hash,total_vnd,status,payment_status,demo,created_at,updated_at) "
                           "VALUES (?,?,?,?,?,'confirmed','pending',?,SYSUTCDATETIME(),SYSUTCDATETIME())",
                           order_id, request_id, conversation_id, request_hash, quote['total_vnd'], int(catalog.demo))
            for line in quote['lines']:
                cursor.execute('INSERT INTO dbo.shop_order_items(order_id,product_id,product_name,quantity,wholesale,k,base_price_vnd,unit_price_vnd,unit) VALUES (?,?,?,?,?,?,?,?,?)',
                               order_id, line['product_id'], line['name'], line['quantity'], int(line['wholesale']), line['k'], line['base_price_vnd'], line['unit_price_vnd'], line['unit'])
                cursor.execute("UPDATE dbo.products SET stock_qty=stock_qty-?, "
                               "availability=CASE WHEN stock_qty-?=0 THEN 'out_of_stock' ELSE 'available' END, "
                               "stock_updated_at=CASE WHEN (SELECT demo FROM dbo.catalog_metadata WHERE id=1)=1 THEN (SELECT as_of FROM dbo.catalog_metadata WHERE id=1) ELSE SYSUTCDATETIME() END "
                               "WHERE id=? AND status='active' AND availability='available' AND stock_qty>=?",
                               line['quantity'], line['quantity'], line['product_id'], line['quantity'])
                if cursor.rowcount != 1:
                    raise ValueError('Tồn kho vừa thay đổi; đơn chưa được chốt. Vui lòng kiểm tra lại giỏ hàng.')
            return self._order(cursor, order_id)

    @staticmethod
    def _order(cursor, order_id):
        cursor.execute('SELECT id,conversation_id,total_vnd,status,payment_status,payment_reference,CONVERT(nvarchar(40),paid_at,127) AS paid_at,demo,CONVERT(nvarchar(40),created_at,127) AS created_at FROM dbo.shop_orders WHERE id=?', order_id)
        found = rows(cursor)
        if not found:
            raise ValueError('Không tìm thấy đơn hàng.')
        order = found[0]
        order['demo'] = bool(order['demo'])
        cursor.execute('SELECT product_id,product_name AS name,quantity,wholesale,CONVERT(nvarchar(20),k) AS k,base_price_vnd,unit_price_vnd,unit FROM dbo.shop_order_items WHERE order_id=? ORDER BY product_id', order_id)
        order['lines'] = rows(cursor)
        return order

    def orders(self, conversation_id=None):
        with transaction() as cursor:
            if conversation_id is None:
                cursor.execute('SELECT TOP (100) id FROM dbo.shop_orders ORDER BY created_at DESC,id')
            else:
                cursor.execute('SELECT TOP (100) id FROM dbo.shop_orders WHERE conversation_id=? ORDER BY created_at DESC,id', conversation_id)
            ids = [row['id'] for row in rows(cursor)]
            return [self._order(cursor, order_id) for order_id in ids]

    def update_payment(self, order_id, *, action, reference=''):
        if action not in {'paid', 'cancel'}:
            raise ValueError('Chỉ ghi nhận đã thanh toán hoặc hủy đơn chưa thanh toán.')
        if action == 'paid' and (not isinstance(reference, str) or not reference.strip() or len(reference) > 200):
            raise ValueError('Cần mã giao dịch/phiếu thu đã đối chiếu để ghi nhận thanh toán.')
        with transaction() as cursor:
            cursor.execute('SELECT status,payment_status,payment_reference FROM dbo.shop_orders WITH (UPDLOCK,HOLDLOCK) WHERE id=?', order_id)
            found = rows(cursor)
            if not found or found[0]['status'] != 'confirmed':
                raise ValueError('Đơn không tồn tại hoặc đã hủy.')
            if action == 'paid':
                if found[0]['payment_status'] == 'paid':
                    if found[0]['payment_reference'] != reference.strip():
                        raise ValueError('Đơn đã ghi nhận thanh toán; không ghi đè mã giao dịch cũ.')
                    return self._order(cursor, order_id)
                cursor.execute("UPDATE dbo.shop_orders SET payment_status='paid',payment_reference=?,paid_at=SYSUTCDATETIME(),updated_at=SYSUTCDATETIME() WHERE id=?", reference.strip(), order_id)
            else:
                if found[0]['payment_status'] == 'paid':
                    raise ValueError('Đơn đã thanh toán cần xử lý hoàn tiền riêng; không tự hủy.')
                cursor.execute('SELECT product_id,quantity FROM dbo.shop_order_items WHERE order_id=? ORDER BY product_id', order_id)
                for line in rows(cursor):
                    cursor.execute("UPDATE dbo.products SET stock_qty=stock_qty+?,availability='available', "
                                   "stock_updated_at=CASE WHEN (SELECT demo FROM dbo.catalog_metadata WHERE id=1)=1 THEN (SELECT as_of FROM dbo.catalog_metadata WHERE id=1) ELSE SYSUTCDATETIME() END WHERE id=? AND stock_qty IS NOT NULL",
                                   line['quantity'], line['product_id'])
                    if cursor.rowcount != 1:
                        raise ValueError('Chưa hoàn lại được tồn kho; chưa hủy đơn.')
                cursor.execute("UPDATE dbo.shop_orders SET status='cancelled',updated_at=SYSUTCDATETIME() WHERE id=?", order_id)
            return self._order(cursor, order_id)


if __name__ == '__main__':
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    migrate()
    print('Đã bổ sung bảng giá sỉ/đơn hàng; giữ nguyên giá gốc, tồn kho và hệ số đã có.')
