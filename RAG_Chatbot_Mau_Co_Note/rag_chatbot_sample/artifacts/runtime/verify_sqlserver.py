"""Local integration probe: SQL Server, original RAG, and Streamlit; no model API call."""
import json
import os
from pathlib import Path
import re
import sys
from contextlib import closing

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from dotenv import load_dotenv
load_dotenv(ROOT / '.env')
from catalog.catalog_sqlserver import connect, MASTER_CONNECTION_STRING
from chatbot.shop import load_catalog

first = load_catalog(mode='demo')
before = {p['id']: p['stock_qty'] for p in first.products}
assert len(before) == 200 and all(1 <= n <= 999 for n in before.values())
assert first.database_path.startswith('sqlserver://')
with closing(connect(MASTER_CONNECTION_STRING, autocommit=True)) as db:
    cursor = db.cursor()
    for batch in re.split(r'^\s*GO\s*$', (ROOT / 'catalog/sqlserver/catalog_200.sql').read_text(encoding='utf-8-sig'), flags=re.M | re.I):
        if batch.strip():
            cursor.execute(batch)
            while cursor.nextset():
                pass
second = load_catalog(mode='demo')
assert before == {p['id']: p['stock_qty'] for p in second.products}

from chatbot.shop_rag import ShopRAG
rag = ShopRAG()
collection = rag.rebuild(second)
assert collection.count() == 204
hits = rag.retrieve(second, 'Kẹo chocolate gianduja', 'product', list(before))
assert hits and all(h['sql_table'] == 'products' and h['sql_id'] in before and h['source'] == 'sql:products/' + h['sql_id'] for h in hits)

from streamlit.testing.v1 import AppTest
os.environ['SHOP_DB_PATH'] = str(ROOT / 'artifacts/runtime/sqlserver_ui_support.sqlite3')
at = AppTest.from_file(str(ROOT / 'streamlit_app.py'), default_timeout=30).run()
assert not at.exception, [e.message for e in at.exception]
assert at.session_state['shop_engine'].catalog.database_path == first.database_path
assert len([b for b in at.button if (b.key or '').startswith('ask_catalog_')]) == 20
next(s for s in at.selectbox if s.label == 'Trang sản phẩm').select(10).run()
assert not at.exception
next(t for t in at.text_input if t.label == 'Tìm theo tên hoặc SKU').set_value('DEMO-187').run()
assert not at.exception
assert [b.key for b in at.button if (b.key or '').startswith('ask_catalog_')] == ['ask_catalog_DEMO-187']
assert at.session_state['last_result'] is None
staff = AppTest.from_file(str(ROOT / 'staff_app.py'), default_timeout=30).run()
assert not staff.exception, [e.message for e in staff.exception]
assert before == {p['id']: p['stock_qty'] for p in load_catalog(mode='demo').products}
report = dict(database=first.database_path, products=len(before), faqs=len(first.faqs), stock_min=min(before.values()),
              stock_max=max(before.values()), stock_persisted_after_reload=True, ssms_script_preserved_existing_data=True,
              original_rag_records=collection.count(), retrieval_sources=[h['source'] for h in hits],
              customer_ui_pagination=True, search_last_sku=True, staff_ui=True, paid_model_calls=0)
(ROOT / 'artifacts/sqlserver_check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
