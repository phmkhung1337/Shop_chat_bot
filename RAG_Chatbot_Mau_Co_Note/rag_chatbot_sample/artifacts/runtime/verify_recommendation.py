import json
from pathlib import Path
import sys
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import app
from unittest.mock import patch
original_factory=app.create_ollama_client
def traced_client():
    client=original_factory()
    chat=client.chat
    def traced_chat(*args,**kwargs):
        response=chat(*args,**kwargs)
        print('SYNTHETIC MODEL RESPONSE:', response.message.content,flush=True)
        return response
    client.chat=traced_chat
    return client
patch.object(app,'create_ollama_client',side_effect=traced_client).start()
from chatbot.shop import ShopEngine
from tickets.support import SupportStore
engine=ShopEngine(mode='demo',llm_enabled=True,store=SupportStore(ROOT/'artifacts/runtime'/('recommend_'+uuid4().hex+'.sqlite3')))
result=engine.handle_message('Tìm bánh dưới 100k mỗi hộp, không chứa sữa','recommend',uuid4().hex)
print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
(ROOT/'artifacts/chat_recommendation_check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
assert result['status']=='answered' and 'cam kết an toàn dị ứng' in result['reply']
assert result['criteria']['avoid_allergens']==['milk']
assert set(result['product_ids'])=={'BK-001','BK-004','BK-006'}
assert result['model_calls'] == 1
