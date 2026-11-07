from pathlib import Path
import sys
from unittest.mock import patch
from uuid import uuid4
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import app
from chatbot.shop import ShopEngine
from tickets.support import SupportStore
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
with patch.object(app, 'create_ollama_client', side_effect=traced_client):
    engine=ShopEngine(mode='demo',llm_enabled=True,store=SupportStore(ROOT / 'artifacts/runtime' / ('debug_'+uuid4().hex+'.sqlite3')))
    state=engine.state('debug')
    result=engine._process('Mình muốn biết giá và số lượng còn của DEMO-187.',state,engine.catalog,None,False)
    print('CHECKED', result, flush=True)
    print('ANSWER', engine.rag.answer('Mình muốn biết giá và số lượng còn của DEMO-187.', state, engine.catalog, result), flush=True)
