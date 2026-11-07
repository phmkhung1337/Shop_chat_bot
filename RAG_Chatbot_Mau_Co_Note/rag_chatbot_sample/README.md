# Shop chatbot — phát triển từ RAG mẫu ban đầu

Ứng dụng tư vấn bánh kẹo tiếng Việt chạy local bằng Streamlit. Giữ pipeline E5 → Chroma → Ollama, ghi chú tiếng Việt và các test gốc. Phần shop mở rộng chính `app.py`; `chatbot/shop_rag.py` tách điều phối RAG để bạn kiểm soát. Không dùng agent framework.

UI dùng bố cục ba vùng theo ảnh tham khảo: danh mục/bộ lọc bên trái, sản phẩm ở giữa, trợ lý mua sắm bên phải. Đã bỏ banner, KPI và các khối giải thích lặp lại. Chi tiết, nguồn và tiêu chí chỉ mở khi cần; không đưa số liệu CRM giả vào giao diện.

Thiết kế có đối chiếu skill **UI UX Pro Max** đã cài: `Minimalism & Swiss Style` trong `styles.csv` và `Focus States` trong `ux-guidelines.csv`. Giữ màu xanh tím/bo góc theo ảnh của người dùng, áp dụng phân cấp chữ, lưới sản phẩm, focus rõ, reduced-motion và thu gọn thông tin phụ. Không áp dụng gợi ý hero/showcase tự động vì không phù hợp yêu cầu màn hình sản phẩm.

Ảnh đã kiểm tra: [desktop](artifacts/ui-desktop.jpg), [mobile 375px](artifacts/ui-mobile.jpg). Thông số bố cục: [ui-validation.json](artifacts/ui-validation.json).

## Chạy local

Từ thư mục có `app.py`, dùng **Python 3.13.7** và dependencies đã ghim trong `requirements.txt`:

```powershell
python -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8501
```

Trang khách: [http://127.0.0.1:8501](http://127.0.0.1:8501). Mở terminal khác:

```powershell
python -m streamlit run staff_app.py --server.address 127.0.0.1 --server.port 8502
```

Trang nhân viên: [http://127.0.0.1:8502](http://127.0.0.1:8502). Hai trang chỉ bind loopback, chưa triển khai public. Dừng bằng `Ctrl+C` trong terminal chạy server.

Mặc định `SHOP_MODE=demo`: **200 loại bánh kẹo khác nhau và 4 FAQ** trong Microsoft SQL Server, tên shop Mộc là minh họa. Mỗi loại có tồn kho ngẫu nhiên **1–999**, lưu cố định trong bảng, không tạo lại khi reload/chat. Giá và tồn kho là dữ liệu giả lập với snapshot cố định 05/11/2026. **Chatbot gọi LLM thật** khi `SHOP_LLM_ENABLED=true` (mặc định), không chuyển catalog demo thành dữ liệu đã duyệt.

## Hội thoại AI

Panel **Mộc AI** bên phải là khung chat, có lịch sử, trạng thái đang soạn và ô “Nhắn cho Mộc…”. Model `gpt-oss:20b` qua Ollama hiểu yêu cầu rồi soạn câu trả lời bằng tiếng Việt theo lịch sử và dữ liệu đã đối chiếu. Trang không gọi API chỉ để render; rerun/tìm sản phẩm cũng không tự gửi tin.

`chatbot/shop_rag.py` giữ cả trích xuất lẫn generation, tiếp tục dùng client, E5, Chroma và retrieval gốc trong `app.py`. Giá/tồn kho/tên/quy cách dùng bindings từ các hàng SQL hiện tại, chính sách được chèn nguyên văn kèm điều kiện. Backend trả lời trực tiếp về thành phần, các tiêu chí cần tránh, giỏ hàng, đơn hàng và trạng thái thanh toán. Với lượt dùng model để soạn câu trả lời, JSON sai, nguồn lạ và con số tự viết bị chặn. Một lượt như vậy dùng 2 cuộc gọi model, tối đa 3 nếu cần sửa định dạng.

Đặt `SHOP_LLM_ENABLED=false` để dùng grammar offline khi thử demo không cần API. `connected` vẫn yêu cầu LLM và catalog thật đã duyệt. Khởi động lại server sau khi đổi `.env`. Tin nhắn, lịch sử gần đây và dữ liệu catalog liên quan được gửi tới dịch vụ Ollama đã cấu hình; key chỉ ở server.

## SQL Server / SSMS

Database đã tạo trên máy này: **`ILMOON / ShopChatbotDemo`**. Trong SSMS chọn server `ILMOON` hoặc `localhost`, **Windows Authentication**, rồi Refresh mục Databases. Ba bảng là `dbo.products`, `dbo.faqs`, `dbo.catalog_metadata`.

- [catalog/sqlserver/queries.sql](catalog/sqlserver/queries.sql): xem 200 sản phẩm, giá và tồn kho.
- [catalog/sqlserver/catalog_200.sql](catalog/sqlserver/catalog_200.sql): T-SQL tạo database và seed 200 loại trên máy mới; giữ nguyên catalog đã có.
- [catalog/sqlserver/randomize_stock.sql](catalog/sqlserver/randomize_stock.sql): chỉ chạy khi muốn tạo lại số lượng demo 1–999.
- [Hướng dẫn SSMS](catalog/sqlserver/README.md): kết nối và cập nhật dữ liệu.

```powershell
python -m catalog.catalog_sqlserver check
# Chỉ khi database demo chưa có:
python -m catalog.catalog_sqlserver seed-demo
# Sau khi catalog đã có dữ liệu, bổ sung bảng giá sỉ và đơn hàng:
python -m orders.commerce
# Tạo chỉ mục bằng pipeline RAG gốc từ nguồn SQL Server:
python app.py --rebuild-shop --mode demo
```

`.env` dùng `SHOP_CATALOG_BACKEND=sqlserver` và ODBC 17 với Windows Authentication. Mất kết nối SQL Server sẽ báo lỗi, không tự đổi sang JSON/SQLite. `catalog/catalog_sql.py` và SQLite catalog chỉ dùng cho fixture/offline; SQLite của `tickets/support.py` vẫn lưu hội thoại và tickets riêng.

`.venv` cũ trong bản mẫu trỏ tới Python của máy khác. Không dùng lại môi trường đó. Cài mới nếu cần:

```powershell
py -3.13 -m venv .venv-shop
.\.venv-shop\Scripts\python.exe -m pip install -r requirements.txt
.\.venv-shop\Scripts\python.exe -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8501
```

## Kế thừa code gốc

| Phần gốc trong `app.py` | Phát triển |
| --- | --- |
| `read_text_files()`, `split_into_chunks()` | Giữ cho tài liệu `.txt` và FAQ sinh viên. Product/FAQ ngắn dùng record riêng để giữ SKU, nguồn và điều kiện. |
| `create_ollama_client()`, `friendly_error()` | Giữ SDK, Bearer header, timeout và kiểm tra cấu hình. Shop không đưa lỗi provider chi tiết ra UI. |
| `embed_texts()` | Dùng lại CPU embeddings, cache, prefix `query:` / `passage:` của E5 và normalization. |
| `get_collection()` | Thêm tham số tên collection; mặc định cũ vẫn là `student_faq`. |
| `index_knowledge_base()` | Nhận thêm records đã validate; dùng chính đường đi embedding → `collection.add()` của mẫu. Kiểm tra source signature. |
| `retrieve_relevant_chunks()` | Thêm `where`, `limit`, stable ID và metadata nguồn; giữ cách tính cosine. |
| `run_chatbot()`, `main()` | Chính vòng lặp gốc nhận thêm engine shop; không có terminal runner song song. |
| Memory, prompt, `stream_answer()` | Giữ cho chế độ mẫu. Shop lưu criteria riêng và chỉ công bố kết quả hoàn chỉnh đã kiểm tra. |
| `tests/test_app.py` | Giữ test gốc cho HTTP SDK, lỗi, memory, Chroma và vectors. |

Tài liệu gốc lưu ở [README_SAMPLE.md](docs/README_SAMPLE.md). Chạy mẫu bằng `python app.py --student`; rebuild sinh viên bằng `python app.py --student --rebuild`. Chạy shop terminal bằng `python app.py --mode demo`. Lệnh `/history`, `/reset`, `/exit` vẫn có.

## Bố cục và luồng

```text
streamlit_app.py / app.py::run_chatbot()
  → chatbot/shop.py::handle_message()
    → kiểm tra input, deduplicate request, tải criteria riêng của phiên
    → hiểu yêu cầu, xác nhận thay đổi, hỏi lại nếu cần
    → Python lọc điều kiện cứng trên catalog hiện tại
    → chatbot/shop_rag.py gọi pipeline RAG gốc trong app.py (connected)
    → LLM soạn câu trả lời theo lịch sử; chèn dữ kiện SQL đã kiểm tra, validate trước khi hiển thị
    → tickets/support.py commit outcome / ticket / audit trong SQLite
  → ui/ui_components.py hiển thị kết quả hoàn chỉnh

staff_app.py → tickets/support.py → tiếp nhận / ghi chú / giải quyết / bàn giao lại bot
```

| File | Trách nhiệm |
| --- | --- |
| `app.py` | RAG mẫu được mở rộng, cấu hình Ollama/E5/Chroma và CLI. |
| `chatbot/shop_rag.py` | Fingerprint index, rebuild, metadata filters, retrieval, trích xuất và generation JSON; dùng client/pipeline gốc. |
| `chatbot/shop.py` | Import, validation/Pydantic, criteria, ngân sách/tồn kho/thành phần và `handle_message`. |
| `catalog/catalog_sqlserver.py`, `catalog/sqlserver/` | Nguồn SQL Server/SSMS, schema, import transaction và truy vấn kiểm tra. |
| `orders/` | Xử lý giá sỉ, giỏ, chốt đơn và schema đơn hàng. |
| `catalog/seed_catalog.py` | Dataset demo 200 tên loại khác nhau; tồn kho 1–999, không coi thành phần chưa biết là an toàn. |
| `tickets/support.py` | SQLite, idempotency, state/audit, tickets, staff CLI và retention. |
| `streamlit_app.py` | Chat khách, form criteria, danh mục và chính sách; không sao chép bộ lọc. |
| `staff_app.py` | Trang nhân viên riêng, không phải tab khách. |
| `ui/ui_components.py`, `ui/shop.css` | Thẻ, trạng thái, bảng phân tích và responsive styling. |
| `data/demo/` | JSON fixtures cho demo, gồm [FAQ mẫu](data/demo/faq.json). Streamlit với backend SQL Server đọc bảng `dbo.faqs` trong catalog. |
| `data/approved/` | Mẫu schema dữ liệu thật, gồm [file FAQ đang chờ nội dung](data/approved/faq.json). |
| `docs/` | [Đánh giá chatbot](docs/DANH_GIA_PROJECT.md), kế hoạch và báo cáo kiểm thử. |

Contract chung: `handle_message(message, conversation_id, request_id, engine=engine)`. Kết quả có `status`, `publish`, `reply`, IDs nguồn/sản phẩm, thẻ đã kiểm tra, `criteria`, `parts`, `ticket_id`, trạng thái hỗ trợ, version và thời điểm kiểm tra.

## Kiểm soát RAG tại chatbot/shop_rag.py

- `collection_name()`: fingerprint gồm nội dung ổn định, model embedding và cosine. Prefix `shop_knowledge_`, tách khỏi `student_faq`.
- `rebuild()`: gọi chính `app.index_knowledge_base()`. Source thay đổi dùng collection khác, giữ snapshot cũ; chỉ xóa collection cùng fingerprint nếu lần xây trước chưa hoàn chỉnh.
- `ensure_current()`: chặn index cũ/thiếu, không âm thầm dùng với source mới.
- `retrieve()`: `record_type` và danh sách SKU/FAQ hợp lệ được truyền vào `where` của chính `app.retrieve_relevant_chunks()`. Tối đa 12 retrieval candidates; luật hiển thị 3 sản phẩm ở `chatbot/shop.py` là riêng biệt.
- `extract()`: gọi chính `app.create_ollama_client()`. JSON text phải qua Pydantic và kiểm tra bằng chứng trước khi đổi criteria; không dựa vào schema enforcement Cloud.

Giá, tồn kho, thời điểm và bằng chứng nhãn được đọc từ catalog hiện tại, không lấy từ vector. Thay giá/tồn kho không bắt buộc re-embedding nếu nội dung mô tả/FAQ không đổi. `product.version` là phiên bản nội dung ổn định.

FAQ trả nguyên câu đã duyệt cùng điều kiện và nguồn. Alias rõ ràng được ưu tiên; diễn đạt chưa đủ căn cứ được hỏi lại/chuyển kiểm tra, không dùng similarity làm bằng chứng chính sách. Chưa có chính sách dài thật được cung cấp nên không thêm tài liệu dài giả vào knowledge base.

## Thử hội thoại

```text
Tìm bánh dưới 100k mỗi hộp, không chứa sữa, không lấy vị dâu
Giá BK-001 bao nhiêu?
Có loại rẻ hơn không?
Tôi cần 3 hộp bánh với ngân sách 200k
Tổng cả 3 hộp
Giá BK-006 còn hàng không?
Tìm bánh dưới 100k mỗi hộp và shop có khuyến mại không?
Tôi muốn gặp nhân viên
```

LLM được bật cho cả catalog demo và connected qua cấu hình riêng. Tắt LLM chỉ dành cho demo offline với ngữ pháp giới hạn; giá/tồn kho/thành phần vẫn do Python kiểm tra và render.

- Criteria tồn tại ngoài memory ba lượt. Điều kiện bỏ qua ở lượt sau không bị xóa.
- “Dưới” là nhỏ hơn; “tối đa” cho phép bằng. Tổng ngân sách so giá × số lượng bằng số nguyên.
- Unknown, giá/nhãn cũ và bằng chứng không đủ không được gợi ý như đã xác minh. Không suy ra “không chứa” từ nhãn rỗng.
- “Rẻ hơn” hỏi lại nếu nhiều tham chiếu; có thể nêu SKU. Form và chat dùng chung backend. Bỏ hạn chế thành phần hoặc reset cần xác nhận.
- Xem trực tiếp SKU hết hàng vẫn hiện giá hợp lệ và đúng trạng thái.
- Thêm nhiều SKU vào giỏ bằng nút trên sản phẩm hoặc lệnh chat rõ ràng. Kiểm tra báo giá rồi xác nhận trong tab Giỏ hàng để tạo đơn chờ thanh toán và trừ tồn kho. Nhân viên ghi nhận giao dịch đã đối chiếu trên trang nhân viên.
- Reload tạo session mới; tickets/audit vẫn lưu. Chưa có khôi phục phiên qua danh tính được xác thực.

## Chuyển sang dữ liệu thật / connected

Chưa có sản phẩm/FAQ thật và quyết định vận hành do chủ shop duyệt. Không tự đổi fixtures thành dữ liệu thật.

1. Chuẩn bị thư mục nguồn chứa `products.json` và `faq.json`, tham khảo cấu trúc fixtures. Chỉ đặt `demo=false`, `approved=true` sau khi xác minh.
2. Chủ shop chốt `max_age_hours` cho giá, tồn kho và nhãn. Không coi ngưỡng demo là đã được duyệt.
3. Mỗi SKU cần ID riêng, nguồn/phiên bản, integer VND hoặc null, tồn kho rõ ràng, timestamp có timezone, URL http/https nếu có và bằng chứng nhãn. Trạng thái allergen: `absent/present/may_contain/unknown`. `verified_absent` là danh sách thành phần khác đã xác minh không có. Thiếu bằng chứng phải kiểm tra thêm. Hạn dùng theo lô không được suy ra từ mô tả.
4. FAQ có approval, owner, source, version, hiệu lực và điều kiện. Trường `answer` nhận một chuỗi hoặc một danh sách các dòng. Khi nhập, ứng dụng ghép các dòng bằng dấu cách. MVP hỗ trợ điều kiện `region` và `min_order_vnd`. Import từ chối các điều kiện khác.
5. Dừng chat trước khi nhập/rebuild:

```powershell
python app.py --import-data "C:\duong-dan\du-lieu-shop-da-duyet"
python -m orders.commerce
python app.py --rebuild-shop --mode connected
```

Import validate cả hai file rồi nhập trong một transaction vào **SQL Server catalog trống**. Dữ liệu hiện hữu không bị xóa/ghi đè; tạo database riêng cho dữ liệu thật, áp dụng `catalog/sqlserver/schema.sql` và đặt connection string trước khi import. Không dùng lại database demo để giả làm dữ liệu thật. Lỗi import rollback toàn bộ.

6. Cấu hình `.env` theo `.env.example`, đặt `SHOP_MODE=connected`, khởi động lại hai server. Không tự chuyển sang demo khi API lỗi. Key chỉ ở server, không đưa vào widget/URL/Git.
7. Chốt người trực queue, nguồn giá/tồn kho, dữ liệu được phép gửi Cloud và retention. `SHOP_QUEUE_MONITORED=true` chỉ khi có người thực sự theo dõi.

Khách/staff phải cùng `SHOP_MODE`, `SHOP_SQLSERVER_CONNECTION_STRING` và đường dẫn `SHOP_DB_PATH` cho hàng đợi. Hai hàng đợi demo/connected mặc định tách riêng; database SQL Server thật phải được cấu hình riêng. Kiểm tra SDK/embedding gốc bằng `python app.py --check` (có gọi model).

## Hàng đợi và vận hành

Chỉ xác nhận ticket sau commit SQLite. Mặc định nói đã lưu local, chưa xác nhận nhân viên nhận hoặc thông báo đã gửi. Ghi thất bại không trả mã giả. Request ID được deduplicate, không cho đọc kết quả phiên khác.

Trang nhân viên lọc, phân công, ghi chú và lưu nội dung để sao chép. `in_progress` đặt hội thoại vào chế độ tiếp quản. Sau `resolved`, chọn bàn giao lại bot. Chưa có adapter gửi tin khách.

CLI dữ liệu thật; demo thêm `--db shop_demo.sqlite3` trước subcommand:

```powershell
python -m tickets.support list --status new
python -m tickets.support update HT-MA_YEU_CAU --status in_progress --assignee Linh --notes "Đang kiểm tra nhãn"
python -m tickets.support update HT-MA_YEU_CAU --status resolved --resolution "Nội dung đã xác minh" --return-to-bot
```

Sau khi shop chốt thời hạn lưu, operator có thể dùng `python -m tickets.support purge --before 2026-10-01T00:00:00+00:00 --confirm`. Lệnh xóa thật những phiên cũ đã hoàn tất, không xóa phiên có ticket chưa giải quyết; sao lưu trước khi dùng. Không chạy purge trong bàn giao này.

SQLite tuần tự hóa lượt xử lý để giữ state/commit đúng trong pilot local. Trước khi dùng nhiều worker/máy, chuyển sang xử lý theo conversation để lượt model chậm không chặn writer khác. Bảo vệ quyền truy cập DB, backup và `.env`.

## Kiểm tra và giới hạn

```powershell
python -m unittest discover -s tests -v
python -m pip check
```

Test dùng HTTP/SQL Server giả lập, embeddings nhỏ và SQLite/Chroma tạm; không gọi paid API/sửa database runtime. Có kiểm tra generation từ client gốc, giữ history, bindings chống bịa số/nguồn, chèn chính sách nguyên văn, lỗi/timeout và không gọi model lại khi rerun; cùng các test SQL và nghiệp vụ trước đó. Kiểm tra tích hợp riêng dùng Ollama/SQL Server/E5/Chroma thật; xem báo cáo.

Kết quả thực tế ở [TEST_REPORT.md](docs/TEST_REPORT.md). Bản UI mới đã được kiểm tra bằng trình duyệt ở desktop 920×668 và mobile 375×820 (thu gọn menu bên). Danh mục nằm bên trái panel trợ lý trên desktop; mobile xếp trợ lý phía trên sản phẩm, không tràn ngang và ô chat nằm trong viewport. Đã thử mở chi tiết và gửi gợi ý: kết quả hiện ở vùng sản phẩm, không lặp thẻ trong lịch sử chat. AppTest kiểm tra thêm thao tác tìm kiếm/xem toàn bộ không tự gửi tin hoặc tạo ticket.

Đây là MVP local với 200 loại demo đã lưu SQL Server. Dataset gồm 104 bánh, 64 kẹo, 32 chocolate; 187 loại bổ sung chưa có bằng chứng nhãn thành phần. Dữ liệu thương mại thật, đánh giá model trên câu hỏi shop thật, xác thực staff, hosting và kênh hỗ trợ cần được chốt trước vận hành public. Đơn hàng và tồn kho dùng SQL Server. Thanh toán được ghi nhận thủ công sau khi đối chiếu giao dịch.

Nguồn kỹ thuật: [Streamlit chat input](https://docs.streamlit.io/develop/api-reference/chat/st.chat_input), [Streamlit AppTest](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest), [Chroma metadata filters](https://docs.trychroma.com/docs/querying-collections/metadata-filtering), [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs). Planning: [docs/SHOP_CHATBOT_PLAN.md](docs/SHOP_CHATBOT_PLAN.md) do người dùng cung cấp.
