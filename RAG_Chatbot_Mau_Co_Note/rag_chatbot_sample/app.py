
# ================================================================
# PHẦN 1 — IMPORT VÀ CẤU HÌNH
# Mục đích: nạp thư viện, đọc API key và khai báo các tham số có thể chỉnh.
# ================================================================
import argparse
import os
from pathlib import Path
from urllib.parse import urlparse

import chromadb
import httpx
from dotenv import load_dotenv
from ollama import Client, ResponseError


PROJECT_DIR = Path(__file__).resolve().parent
KNOWLEDGE_BASE_DIR = PROJECT_DIR / "knowledge_base"
CHROMA_DIR = PROJECT_DIR / "chroma_db"
COLLECTION_NAME = "student_faq"

# Phải nạp .env TRƯỚC khi đọc cấu hình model.
load_dotenv(PROJECT_DIR / ".env")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "https://ollama.com").strip().rstrip("/")
CHAT_MODEL = os.getenv("OLLAMA_CHAT_MODEL", "gpt-oss:20b").strip()
# Embedding chạy trên CPU, độc lập với model chat trên Ollama Cloud.
EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL", "intfloat/multilingual-e5-small"
).strip()
_embedding_model = None

TOP_K = 3                 # Lấy 3 đoạn tài liệu gần câu hỏi nhất.
MEMORY_TURNS = 3          # Chỉ giữ 3 lượt hỏi - đáp gần nhất để tránh dài context.
CHUNK_SIZE = 350          # Đoạn ngắn phù hợp context của model embedding local.
CHUNK_OVERLAP = 80        # Phần gối giữa hai chunk, tránh mất ý ở ranh giới.
MIN_SIMILARITY = 0.70     # Ngưỡng demo cho E5; LLM vẫn phải kiểm tra thông tin trong nguồn.


# ================================================================
# PHẦN 2 — TIỆN ÍCH: ĐỌC FILE VÀ CHIA CHUNK
# Mục đích: biến tài liệu dài thành các đoạn nhỏ để có thể tìm kiếm chính xác.
# Đây là nội dung Document Loading + Chunking (buổi 20).
# ================================================================
def read_text_files(folder: Path) -> list[tuple[str, str]]:
    """Đọc mọi file .txt và trả về [(tên_file, nội_dung), ...]."""
    files = sorted(folder.glob("*.txt"))
    if not files:
        raise FileNotFoundError(
            f"Không có file .txt trong {folder}. Hãy thêm tài liệu trước khi chạy."
        )

    documents = []
    for file_path in files:
        text = file_path.read_text(encoding="utf-8").strip()
        if text:
            documents.append((file_path.name, text))
    if not documents:
        raise ValueError("Các file .txt đều rỗng. Hãy thêm nội dung trước khi chạy.")
    return documents


def split_into_chunks(text: str, chunk_size: int = CHUNK_SIZE,
                      overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Giữ ranh giới đoạn văn; đoạn dài được chia theo ký tự và có overlap."""
    if chunk_size <= 0 or not 0 <= overlap < chunk_size:
        raise ValueError("Cần chunk_size > 0 và 0 <= overlap < chunk_size.")
    chunks = []
    # Không trộn quy định của hai chủ đề khác nhau vào cùng một vector.
    for paragraph in text.split("\n\n"):
        clean_text = " ".join(paragraph.split())
        start = 0
        while start < len(clean_text):
            end = min(start + chunk_size, len(clean_text))
            chunk = clean_text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end == len(clean_text):
                break
            start = end - overlap

    return chunks


# ================================================================
# PHẦN 3 — EMBEDDING VÀ VECTOR DATABASE
# Mục đích: đổi text thành vector, rồi lưu vector cùng nội dung vào ChromaDB.
# Đây là Embedding/Semantic Search + Vector Database (buổi 19 và 21).
# ================================================================
def create_ollama_client() -> Client:
    """Ollama Cloud dùng API key trong header, không dùng OpenAI key."""
    parsed = urlparse(OLLAMA_BASE_URL)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path not in {"", "/"}:
        raise ValueError("OLLAMA_BASE_URL phải là URL gốc, ví dụ https://ollama.com (không thêm /api hoặc /v1).")
    api_key = os.getenv("OLLAMA_API_KEY", "").strip()
    if parsed.hostname == "ollama.com" and (not api_key or api_key == "your_ollama_api_key_here"):
        raise EnvironmentError(
            "Chưa có OLLAMA_API_KEY. Hãy điền key Ollama Cloud vào file .env."
        )
    if not CHAT_MODEL or not EMBEDDING_MODEL:
        raise ValueError("Tên model chat và embedding không được để trống.")
    return Client(
        host=OLLAMA_BASE_URL,
        headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
        timeout=httpx.Timeout(120.0, connect=10.0),
    )


def embed_texts(texts: list[str], *, is_query: bool = False) -> list[list[float]]:
    """Tạo vector local; lần đầu tải model từ Hugging Face, các lần sau dùng cache."""
    global _embedding_model
    if not texts:
        return []
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        print(f"Đang nạp model embedding local: {EMBEDDING_MODEL}")
        _embedding_model = SentenceTransformer(EMBEDDING_MODEL, device="cpu")
    # E5 yêu cầu query:/passage: kể cả khi nội dung là tiếng Việt.
    prefix = "query: " if is_query else "passage: "
    inputs = [prefix + text for text in texts] if "e5" in EMBEDDING_MODEL.lower() else texts
    return _embedding_model.encode(
        inputs, normalize_embeddings=True, show_progress_bar=False
    ).tolist()


def get_collection(rebuild: bool, *, collection_name=None):
    """Mở database local. rebuild=True sẽ xóa index cũ để lập lại từ đầu."""
    chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    name = collection_name or COLLECTION_NAME

    if rebuild:
        try:
            chroma_client.delete_collection(name)
            print("Đã xóa index cũ.")
        except chromadb.errors.NotFoundError:
            # Lần chạy đầu chưa có collection nên không cần báo lỗi.
            pass

    collection = chroma_client.get_or_create_collection(
        name=name,
        embedding_function=None,
        configuration={"hnsw": {"space": "cosine"}},
        metadata={
            "description": "Tài liệu FAQ cho chatbot lớp học",
            "embedding_model": EMBEDDING_MODEL,
            "distance_metric": "cosine",
        },
    )
    # Không dùng lẫn vector OpenAI cũ hoặc vector của hai model khác nhau.
    metadata = collection.metadata or {}
    if (metadata.get("embedding_model") != EMBEDDING_MODEL
            or metadata.get("distance_metric") != "cosine"):
        raise ValueError("Index cũ dùng model embedding khác. Chạy python app.py --rebuild để tạo lại.")
    return collection


def index_knowledge_base(collection, *, records=None, source_signature=None) -> None:
    """Đọc tài liệu -> chunking -> embedding -> lưu vào ChromaDB."""
    if collection.count() > 0:
        if records is not None and (collection.count() != len(records) or
                                   (collection.metadata or {}).get("source_signature") != source_signature):
            raise ValueError("Chỉ mục shop không khớp nguồn. Dừng chat và chạy --rebuild-shop.")
        print(f"Đang dùng index có sẵn: {collection.count()} chunks.")
        return

    all_chunks, all_ids, all_metadatas = [], [], []
    if records is None:
        documents = read_text_files(KNOWLEDGE_BASE_DIR)
        for filename, text in documents:
            chunks = split_into_chunks(text)
            for index, chunk in enumerate(chunks):
                all_chunks.append(chunk)
                all_ids.append(f"{Path(filename).stem}_chunk_{index}")
                # Metadata giúp biết câu trả lời lấy từ file và đoạn nào.
                all_metadatas.append({"source": filename, "chunk_number": index})
    else:
        # Giữ nguyên một FAQ ngắn cùng điều kiện áp dụng; không trộn thành phần của SKU khác.
        all_chunks = [r["content"] for r in records]
        all_ids = [r["id"] for r in records]
        all_metadatas = [{k: v for k, v in r.items() if k not in {"id", "content"}} for r in records]

    print(f"Đang tạo embedding cho {len(all_chunks)} chunks...")
    embeddings = embed_texts(all_chunks)
    if not embeddings or len(embeddings) != len(all_chunks) or not all(v and len(v) == len(embeddings[0]) for v in embeddings):
        raise ValueError("Embedding không đầy đủ; chưa ghi dữ liệu vào chỉ mục.")
    collection.add(
        ids=all_ids,
        documents=all_chunks,
        metadatas=all_metadatas,
        embeddings=embeddings,
    )
    if source_signature is not None:
        collection.modify(metadata={**collection.metadata, "source_signature": source_signature,
                                    "description": "Shop approved knowledge"})
    print("Đã lập index xong. Lần sau không cần tạo lại trừ khi đổi tài liệu.")


# ================================================================
# PHẦN 4 — RETRIEVAL / SEMANTIC SEARCH
# Mục đích: tìm các chunks có ý nghĩa gần nhất với câu hỏi người dùng.
# Đây là bước R (Retrieval) trong RAG.
# ================================================================
def distance_to_similarity(distance: float) -> float:
    """Đổi distance thành một điểm dễ đọc trong demo; không phải độ chính xác tuyệt đối."""
    return round(max(0.0, min(1.0, 1 - distance)), 4)


def retrieve_relevant_chunks(collection, question: str, *, where=None, limit=TOP_K) -> list[dict]:
    """Embedding câu hỏi rồi truy vấn top-k chunks gần nhất trong vector DB."""
    if collection.count() == 0:
        return []

    question_embedding = embed_texts([question], is_query=True)[0]
    filters = {"where": where} if where else {}
    result = collection.query(
        query_embeddings=[question_embedding],
        n_results=min(limit, collection.count()),
        include=["documents", "metadatas", "distances"],
        **filters,
    )

    retrieved = []
    for record_id, document, metadata, distance in zip(
        result["ids"][0], result["documents"][0], result["metadatas"][0], result["distances"][0]
    ):
        retrieved.append(
            {
                "content": document,
                "source": metadata["source"],
                "chunk_number": metadata.get("chunk_number", 0),
                "similarity": distance_to_similarity(distance),
                "id": record_id,
                **{k: metadata[k] for k in ("product_id", "faq_id", "record_type", "version", "sql_table", "sql_id", "source_reference") if k in metadata},
            }
        )
    return retrieved


# ================================================================
# PHẦN 5 — MEMORY VÀ PROMPT
# Mục đích: giữ một ít ngữ cảnh hội thoại, rồi ghép cùng tài liệu đã truy xuất.
# Đây là Memory (buổi 18) và cách chuẩn bị context cho RAG (buổi 22).
# ================================================================
def add_to_memory(history: list[dict], question: str, answer: str) -> None:
    """Lưu lượt hỏi - đáp hiện tại, chỉ giữ số lượt gần nhất theo MEMORY_TURNS."""
    history.extend([
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer},
    ])
    del history[:-MEMORY_TURNS * 2]


def format_history(history: list[dict]) -> str:
    """Biến lịch sử thành text để gửi vào prompt."""
    if not history:
        return "Chưa có lịch sử hội thoại."

    role_name = {"user": "Người dùng", "assistant": "Chatbot"}
    return "\n".join(f"{role_name[item['role']]}: {item['content']}" for item in history)


def build_rag_prompt(question: str, history: list[dict], chunks: list[dict]) -> str:
    """Ghép câu hỏi + memory + các đoạn đã retrieve thành input cho LLM."""
    context = "\n\n".join(
        f"[Nguồn: {item['source']} | đoạn {item['chunk_number']}]\n{item['content']}"
        for item in chunks
    )
    return f"""TÀI LIỆU THAM KHẢO:
{context}

LỊCH SỬ HỘI THOẠI GẦN ĐÂY:
{format_history(history)}

CÂU HỎI HIỆN TẠI:
{question}

YÊU CẦU:
- Chỉ trả lời dựa vào TÀI LIỆU THAM KHẢO.
- Nếu tài liệu không có thông tin cần thiết, hãy nói rõ: "Mình chưa tìm thấy thông tin này trong tài liệu hiện có."
- Trả lời ngắn gọn, bằng tiếng Việt, dễ hiểu.
- Không tự bịa quy định, con số hoặc nguồn.
"""


# ================================================================
# PHẦN 6 — GENERATION + STREAMING
# Mục đích: LLM dùng context đã retrieve để soạn câu trả lời và in dần ra màn hình.
# Đây là bước G (Generation) trong RAG và Streaming (buổi 18).
# ================================================================
def stream_answer(client: Client, prompt: str) -> str:
    """Gọi LLM và in từng phần text ngay khi nhận được, thay vì chờ cả câu trả lời."""
    stream = client.chat(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": (
                "Bạn là chatbot hỗ trợ sinh viên. Tuân thủ nghiêm ngặt dữ liệu được "
                "cung cấp trong prompt."
            )},
            {"role": "user", "content": prompt},
        ],
        stream=True,
    )

    answer = ""
    completed = False
    for event in stream:
        # Ollama gửi text trong message.content; không in phần thinking.
        text = event.message.content or ""
        print(text, end="", flush=True)
        answer += text
        completed = event.done
    print()
    if not completed:
        raise ValueError("Luồng trả lời Ollama bị ngắt. Hãy thử lại câu hỏi.")
    if not answer.strip():
        raise ValueError("Ollama không trả về nội dung. Kiểm tra model hoặc thử lại.")
    return answer


# ================================================================
# PHẦN 7 — VÒNG LẶP CHATBOT
# Mục đích: nhận câu hỏi, retrieve, kiểm tra chất lượng, trả lời và cập nhật memory.
# ================================================================
def print_sources(chunks: list[dict]) -> None:
    """Hiện nguồn để người học thấy chatbot đã lấy context ở đâu."""
    print("\nNguồn được tìm thấy:")
    for item in chunks:
        print(
            f"- {item['source']} | đoạn {item['chunk_number']} "
            f"| độ gần đúng: {item['similarity']}"
        )


def run_chatbot(collection, client: Client, *, shop_engine=None) -> None:
    """Chạy chatbot terminal cho tới khi người dùng gõ /exit."""
    history: list[dict] = []
    if shop_engine is not None:
        from uuid import uuid4
        from chatbot.shop import handle_message, money
        conversation_id = uuid4().hex
        print(f"Chế độ cửa hàng: {shop_engine.mode.upper()}")
    print("\nChatbot đã sẵn sàng. Lệnh: /history, /reset, /exit")

    while True:
        try:
            question = input("\nBạn: ").strip()
        except EOFError:
            print("\nTạm biệt!")
            break
        if not question:
            continue
        if question.lower() == "/exit":
            print("Tạm biệt!")
            break
        if question.lower() == "/reset":
            if shop_engine is not None:
                result = handle_message("/reset", conversation_id, engine=shop_engine)
                print(result["reply"])
                continue
            history.clear()
            print("Đã xóa memory hội thoại.")
            continue
        if question.lower() == "/history":
            print("\n" + format_history(shop_engine.state(conversation_id)["history"] if shop_engine else history))
            continue

        if shop_engine is not None:
            result = handle_message(question, conversation_id, engine=shop_engine)
            print("\nChatbot:", result["reply"])
            for product in result["products"]:
                price = money(product["price_vnd"]) if product["price_vnd"] is not None else "Cần xác minh giá"
                print(f"- {product['name']} ({product['id']}): {price}/{product['unit']} — {product['packaging']}")
            continue

        try:
            chunks = retrieve_relevant_chunks(collection, question)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"\nLỗi truy xuất: {friendly_error(exc)}")
            continue

        # Guardrail đơn giản: không đủ liên quan thì không gọi LLM để tránh trả lời bừa.
        best_similarity = max((item["similarity"] for item in chunks), default=0)
        if best_similarity < MIN_SIMILARITY:
            answer = "Mình chưa tìm thấy thông tin này trong tài liệu hiện có."
            print(f"\nChatbot: {answer}")
            add_to_memory(history, question, answer)
            continue

        print_sources(chunks)
        prompt = build_rag_prompt(question, history, chunks)
        print("\nChatbot: ", end="")
        try:
            answer = stream_answer(client, prompt)
        except (ResponseError, ConnectionError, httpx.HTTPError, ValueError) as exc:
            print(f"\nLỗi: {friendly_error(exc)}")
            continue  # Không lưu câu trả lời lỗi/dở dang vào memory.
        add_to_memory(history, question, answer)


def friendly_error(exc: Exception) -> str:
    """Thông báo lỗi dễ hiểu và không đưa API key ra terminal."""
    if isinstance(exc, ResponseError):
        if exc.status_code in {401, 403}:
            return "Ollama từ chối truy cập. Kiểm tra OLLAMA_API_KEY và quyền tài khoản."
        if exc.status_code == 404:
            return "Không tìm thấy model/API. Kiểm tra OLLAMA_CHAT_MODEL và OLLAMA_BASE_URL."
        if exc.status_code == 429:
            return "Ollama đang giới hạn lượt dùng. Hãy chờ rồi thử lại hoặc kiểm tra hạn mức."
        return f"Ollama gặp lỗi (HTTP {exc.status_code}). Hãy thử lại hoặc kiểm tra model."
    if isinstance(exc, (ConnectionError, httpx.HTTPError)):
        return "Không kết nối được hoặc hết thời gian chờ Ollama. Kiểm tra mạng và OLLAMA_BASE_URL."
    message = str(exc)
    api_key = os.getenv("OLLAMA_API_KEY", "").strip()
    return message.replace(api_key, "[ẩn API key]") if api_key else message


def main() -> None:
    """Điểm bắt đầu chương trình; --rebuild dùng khi thay đổi file tài liệu."""
    parser = argparse.ArgumentParser(description="Chatbot RAG mẫu cho lớp học")
    parser.add_argument(
        "--check", action="store_true",
        help="Gọi thử Ollama Cloud và embedding local, không thay đổi database",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Xóa index cũ và tạo lại embedding từ knowledge_base",
    )
    parser.add_argument("--student", action="store_true", help="Chạy nguyên chatbot FAQ sinh viên mẫu")
    parser.add_argument("--mode", choices=["demo", "connected"], default=None, help="Chế độ shop; mặc định SHOP_MODE hoặc demo")
    parser.add_argument("--rebuild-shop", action="store_true", help="Lập index shop riêng, giữ nguyên chỉ mục sinh viên và snapshot cũ")
    parser.add_argument("--import-data", metavar="FOLDER", help="Kiểm tra và nhập products.json + faq.json đã duyệt; dừng chat trước khi chạy")
    args = parser.parse_args()

    try:
        if args.import_data:
            from chatbot.shop import import_catalog
            backup = import_catalog(args.import_data, mode="connected")
            print("Đã nhập catalog hợp lệ vào SQL. Cần rebuild index shop nếu mô tả/FAQ thay đổi.")
            if backup:
                print(f"Bản sao khôi phục: {backup}")
            return
        if args.rebuild_shop:
            from chatbot.shop import create_engine
            engine = create_engine(args.mode)
            from chatbot.shop_rag import ShopRAG
            collection = ShopRAG().rebuild(engine.catalog)
            print(f"Shop index ready: {collection.count()} records. Dữ liệu {'DEMO' if engine.catalog.demo else 'đã duyệt'}.")
            return
        if not args.student and not args.check and not args.rebuild:
            from chatbot.shop import create_engine
            run_chatbot(None, None, shop_engine=create_engine(args.mode))
            return
        with create_ollama_client() as client:
            if args.check:
                print(f"Kiểm tra Ollama: {OLLAMA_BASE_URL} | model: {CHAT_MODEL}")
                stream_answer(client, "Hãy trả lời ngắn gọn bằng tiếng Việt: Xin chào!")
                vector = embed_texts(["Kiểm tra embedding tiếng Việt."])[0]
                print(f"Embedding local OK: {len(vector)} chiều.")
                return
            collection = get_collection(rebuild=args.rebuild)
            index_knowledge_base(collection)
            run_chatbot(collection, client)
    except (OSError, ResponseError, httpx.HTTPError, ValueError) as exc:
        parser.exit(1, f"Lỗi: {friendly_error(exc)}\n")
    except KeyboardInterrupt:
        print("\nTạm biệt!")


if __name__ == "__main__":
    main()
