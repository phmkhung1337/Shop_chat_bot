"""Kiểm tra API Ollama qua SDK thật với HTTP giả lập; không dùng key thật."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import chromadb
import httpx
from ollama import ResponseError

import app


class OllamaTests(unittest.TestCase):
    def client(self, handler):
        return app.Client(
            host="https://ollama.com",
            headers={"Authorization": "Bearer test-only-key"},
            transport=httpx.MockTransport(handler),
        )

    def test_native_chat_auth_and_streaming(self):
        def handler(request):
            self.assertEqual(request.url.path, "/api/chat")
            self.assertEqual(request.headers["Authorization"], "Bearer test-only-key")
            body = json.loads(request.content)
            self.assertEqual(body["model"], app.CHAT_MODEL)
            self.assertTrue(body["stream"])
            self.assertEqual(body["messages"][1]["content"], "Câu hỏi thử")
            chunks = [
                {"message": {"role": "assistant", "thinking": "Không in suy luận", "content": ""}, "done": False},
                {"message": {"role": "assistant", "content": "Xin "}, "done": False},
                {"message": {"role": "assistant", "content": "chào!"}, "done": True},
            ]
            return httpx.Response(200, content="\n".join(json.dumps(x) for x in chunks))

        output = io.StringIO()
        with self.client(handler) as client, contextlib.redirect_stdout(output):
            answer = app.stream_answer(client, "Câu hỏi thử")
        self.assertEqual(answer, "Xin chào!")
        self.assertEqual(output.getvalue(), "Xin chào!\n")

    def test_http_and_midstream_errors(self):
        for status in [401, 404, 429, 503]:
            with self.subTest(status=status):
                with self.client(lambda r: httpx.Response(status, json={"error": "test-only-key"})) as client:
                    with self.assertRaises(ResponseError) as caught:
                        app.stream_answer(client, "test")
                self.assertNotIn("test-only-key", app.friendly_error(caught.exception))
        with self.client(lambda r: httpx.Response(200, content='{"error":"model failed"}\n')) as client:
            with self.assertRaises(ResponseError):
                app.stream_answer(client, "test")

    def test_empty_stream_is_not_a_success(self):
        with self.client(lambda r: httpx.Response(200, content='{"message":{"role":"assistant","content":""},"done":true}\n')) as client:
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, "không trả về nội dung"):
                app.stream_answer(client, "test")

    def test_truncated_stream_is_not_saved_as_complete(self):
        with self.client(lambda r: httpx.Response(200, content='{"message":{"role":"assistant","content":"partial"},"done":false}\n')) as client:
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, "bị ngắt"):
                app.stream_answer(client, "test")

    def test_cloud_requires_key(self):
        for value in ["", "your_ollama_api_key_here"]:
            with patch.dict(os.environ, {"OLLAMA_API_KEY": value}), patch.object(app, "OLLAMA_BASE_URL", "https://ollama.com"):
                with self.assertRaises(EnvironmentError):
                    app.create_ollama_client()

    def test_base_url_rejects_api_path(self):
        with patch.object(app, "OLLAMA_BASE_URL", "https://ollama.com/api"):
            with self.assertRaises(ValueError):
                app.create_ollama_client()

    def test_failed_answer_does_not_enter_memory_and_chat_continues(self):
        history = []
        chunks = [{"similarity": 0.9, "content": "Tài liệu", "source": "faq.txt", "chunk_number": 0}]
        with patch("builtins.input", side_effect=["Câu một", "Câu hai", "/exit"]), \
                patch.object(app, "retrieve_relevant_chunks", return_value=chunks), \
                patch.object(app, "stream_answer", side_effect=[ResponseError("fail", 503), "Trả lời"]), \
                patch.object(app, "add_to_memory", side_effect=lambda h, q, a: history.append((q, a))), \
                contextlib.redirect_stdout(io.StringIO()):
            app.run_chatbot(None, None)
        self.assertEqual(history, [("Câu hai", "Trả lời")])


class IndexTests(unittest.TestCase):
    def test_legacy_index_requires_rebuild_and_new_index_uses_cosine(self):
        db = chromadb.EphemeralClient()
        with patch.object(app.chromadb, "PersistentClient", return_value=db), \
                patch.object(app, "COLLECTION_NAME", f"test_{uuid4().hex}"):
            db.create_collection(app.COLLECTION_NAME, metadata={"description": "OpenAI index"})
            with self.assertRaisesRegex(ValueError, "--rebuild"):
                app.get_collection(False)
            with contextlib.redirect_stdout(io.StringIO()):
                collection = app.get_collection(True)
            self.assertEqual(collection.metadata["embedding_model"], app.EMBEDDING_MODEL)
            self.assertEqual(collection.configuration["hnsw"]["space"], "cosine")

    def test_index_and_query_use_explicit_local_vectors(self):
        db = chromadb.EphemeralClient()
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            knowledge = folder / "docs"
            knowledge.mkdir()
            (knowledge / "faq.txt").write_text("Nhóm đồ án có từ 3 đến 5 thành viên.", encoding="utf-8")
            with patch.object(app.chromadb, "PersistentClient", return_value=db), \
                    patch.object(app, "COLLECTION_NAME", f"test_{uuid4().hex}"), \
                    patch.object(app, "KNOWLEDGE_BASE_DIR", knowledge), \
                    patch.object(app, "embed_texts", side_effect=lambda texts, **kwargs: [[1.0, 0.0] for _ in texts]), \
                    contextlib.redirect_stdout(io.StringIO()):
                collection = app.get_collection(False)
                app.index_knowledge_base(collection)
                result = app.retrieve_relevant_chunks(collection, "Một nhóm có mấy người?")
                app.index_knowledge_base(collection)
            self.assertEqual(collection.count(), 1)
            self.assertEqual(result[0]["source"], "faq.txt")
            self.assertEqual(result[0]["similarity"], 1.0)


if __name__ == "__main__":
    unittest.main()
