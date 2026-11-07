import json
from pathlib import Path
import sys
from uuid import uuid4
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / '.env')
from chatbot.shop import ShopEngine, money
from tickets.support import SupportStore
import app
original_factory = app.create_ollama_client
def traced_client():
    client = original_factory()
    chat = client.chat
    def traced_chat(*args, **kwargs):
        result = chat(*args, **kwargs)
        print('SYNTHETIC MODEL RESPONSE:', result.message.content, flush=True)
        return result
    client.chat = traced_chat
    return client
patcher = patch.object(app, 'create_ollama_client', side_effect=traced_client)
patcher.start()

run_id = uuid4().hex
engine = ShopEngine(mode='demo', llm_enabled=True,
                    store=SupportStore(ROOT / 'artifacts/runtime' / ('chat_' + run_id + '.sqlite3')))
report = {'database': engine.catalog.database_path, 'model': 'gpt-oss:20b', 'messages': []}
for i, question in enumerate(('Chào bạn, bạn có thể giúp mình những gì?',
                              'Mình muốn biết giá và số lượng còn của DEMO-187.',
                              'Cảm ơn, mình mua để tặng đồng nghiệp, bạn có thể nói thêm về món vừa hỏi không?')):
    result = engine.handle_message(question, run_id, f'chat_{run_id}_{i}')
    print(json.dumps({'question': question, 'status': result['status'], 'reply': result['reply'],
                      'origin': result.get('response_origin'), 'calls': result['model_calls']}, ensure_ascii=False), flush=True)
    report['messages'].append({'question': question, 'result': result})
    (ROOT / 'artifacts/chat_llm_check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    assert result['status'] == 'answered' and result['response_origin'] == 'llm'
    assert result['model_calls'] == 2
    if i == 1:
        p = engine.catalog.product('DEMO-187')
        assert result['product_ids'] == [p['id']]
        assert money(p['price_vnd']) in result['reply']
        assert str(p['stock_qty']) in result['reply']
    before = result['model_calls']
    assert result == engine.handle_message(question, run_id, f'chat_{run_id}_{i}')
report['duplicate_request_reused'] = True
report['history_messages'] = len(engine.state(run_id)['history'])
(ROOT / 'artifacts/chat_llm_check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
