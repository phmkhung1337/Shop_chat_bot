# RAG Chatbot mẫu có note

> Tài liệu ban đầu được giữ để tham khảo. Ở bản hiện tại, thêm `--student` để chạy chatbot sinh viên; xem README.md cho ứng dụng shop và các lệnh mới.

Phiên bản này dùng **Ollama Cloud để chat**, **Sentence Transformers để embedding local** và ChromaDB để lưu vector. Không cần OpenAI API key hoặc cài Ollama trên máy. Các note tiếng Việt trong `app.py` vẫn được giữ lại.

Đây là chatbot chạy trong terminal, dùng để minh hoạ luồng:

`tài liệu .txt → chunking → embedding → ChromaDB → semantic search → LLM trả lời → memory + streaming`

`app.py` đã có note tiếng Việt cho từng phần. Dữ liệu trong `knowledge_base/student_faq.txt` là dữ liệu giả lập để thực hành; học sinh có thể thay bằng FAQ của một câu lạc bộ, lớp học hoặc trường.

## 1. Cài đặt

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Trên macOS/Linux, thay lệnh kích hoạt bằng `source .venv/bin/activate`.

Copy `.env.example` thành `.env` nếu chưa có:

```powershell
Copy-Item .env.example .env
```

Mở `.env`, điền `OLLAMA_API_KEY` lấy tại [Ollama API keys](https://ollama.com/settings/keys). Không gửi key qua chat hoặc đưa `.env` lên Git.

```dotenv
OLLAMA_API_KEY=your_ollama_api_key_here
OLLAMA_BASE_URL=https://ollama.com
OLLAMA_CHAT_MODEL=gpt-oss:20b
EMBEDDING_MODEL=intfloat/multilingual-e5-small
```

Đổi `OLLAMA_CHAT_MODEL` sang model tài khoản được dùng. Với API Cloud trực tiếp, dùng tên từ [danh sách model](https://ollama.com/api/tags), không thêm hậu tố `-cloud`. URL gốc không thêm `/api` hay `/v1`; SDK tự gọi `/api/chat` và gửi key bằng Bearer header theo [tài liệu Ollama Cloud](https://docs.ollama.com/cloud).

Model embedding đa ngôn ngữ tải từ Hugging Face trong lần đầu (cần mạng và dung lượng đĩa), sau đó dùng cache và chạy trên CPU. Embedding không gửi đến Ollama Cloud; câu hỏi, lịch sử và các đoạn tài liệu tìm được sẽ gửi đến Cloud để tạo câu trả lời.

Model mặc định [multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small) dành cho truy xuất đa ngôn ngữ. Code thêm tiền tố `query:` cho câu hỏi và `passage:` cho tài liệu theo hướng dẫn của model; vẫn giữ nội dung nguồn gốc trong ChromaDB.

## 2. Chạy lần đầu

Kiểm tra API key, model chat, streaming và embedding local:

```bash
python app.py --check
```

Sau khi kiểm tra thành công, chạy chatbot:

```bash
python app.py --rebuild
```

Tùy chọn `--rebuild` sẽ đọc lại các file `.txt`, chia chunk và tạo embedding mới. Khi chỉ chat với cùng bộ tài liệu, dùng:

```bash
python app.py
```

Nếu từng chạy bản OpenAI hoặc đổi `EMBEDDING_MODEL`, cần chạy lại `--rebuild`; vector từ các model khác nhau không dùng chung được. Điểm truy xuất dùng cosine similarity; `MIN_SIMILARITY` là ngưỡng demo, cần đánh giá lại khi đổi model hoặc tài liệu.

Nếu PowerShell không cho kích hoạt môi trường, gọi trực tiếp `.\.venv\Scripts\python.exe app.py --check` và `.\.venv\Scripts\python.exe app.py --rebuild`.

Lỗi truy cập: kiểm tra API key/quyền tài khoản (401/403), tên model (404), hạn mức Cloud (429), hoặc kết nối mạng/thời gian chờ. Không cần `ollama serve` hay `ollama pull` cho cấu hình này.

Chạy kiểm tra tự động (API giả lập, không tốn lượt dùng Cloud):

```bash
python -m unittest discover -s tests -v
```

## 3. Lệnh khi chat

- `/history`: xem các lượt hội thoại chatbot đang nhớ.
- `/reset`: xóa memory của phiên chat hiện tại.
- `/exit`: thoát chương trình.

## 4. Kịch bản demo nhanh trên lớp

1. Hỏi: `Điều kiện để được dự thi là gì?`
2. Hỏi tiếp: `Nếu em chỉ tham dự 75% thì sao?` — để học sinh thấy chatbot dùng memory.
3. Hỏi: `Một nhóm đồ án có mấy người?`
4. Hỏi ngoài tài liệu: `Học phí kỳ này là bao nhiêu?` — để quan sát chatbot từ chối bịa thông tin.

## 5. Bài mở rộng cho học sinh

1. Thêm file FAQ mới và chạy lại với `--rebuild`.
2. Thử thay đổi `CHUNK_SIZE`, `CHUNK_OVERLAP`, `TOP_K`; ghi nhận câu trả lời thay đổi ra sao.
3. Thêm metadata `category` rồi chỉ tìm kiếm trong một nhóm tài liệu nhất định.
4. Lưu lịch sử chat ra file JSON.
