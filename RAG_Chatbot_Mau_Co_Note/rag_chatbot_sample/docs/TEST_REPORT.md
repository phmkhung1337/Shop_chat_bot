# Báo cáo kiểm thử — 06/11/2026

## Kết quả đã chạy

| Kiểm tra | Kết quả |
| --- | --- |
| `python -m unittest discover -s tests -v` | **107 tests, OK**, 15,031 giây ở lần chạy cuối sau hội thoại LLM, giới hạn khả năng tư vấn và cập nhật layout trợ lý. |
| Catalog SQL | 15 test SQLite/offline và 11 test SQL Server: đúng 200 loại, tên duy nhất, bound parameters, rollback, bảo toàn dữ liệu hiện hữu, không fallback JSON/SQLite khi SQL Server lỗi và stock 1–999. |
| Test gốc `tests/test_app.py` | Giữ và chạy đạt: Ollama SDK/auth, HTTP/stream errors, memory, local vectors, cosine và legacy-index guard. |
| Luật nghiệp vụ shop | Đạt: SKU, số tiền nguyên, dưới/tối đa, tổng/mỗi đơn vị, số lượng, freshness, unknown, allergen/cross-contact, thành phần khác, giữ tiêu chí, reset và isolation. |
| Chính sách và provenance | Đạt: FAQ điều kiện, draft/expired/conflicting, mixed question và partial outcome; chặn index khác nguồn. |
| RAG mở rộng từ mẫu | Đạt: gọi chính `app.index_knowledge_base`, metadata filter chỉ trên eligible IDs, phân biệt product/FAQ, giữ stable IDs và snapshot cũ. |
| Hàng đợi / staff | Đạt: restart, request deduplication, không đọc chéo phiên, rollback khi SQLite lỗi, takeover/return-to-bot, retention và import giữ backup. |
| Streamlit AppTest | **12 tests đạt**, thêm gọi LLM generation rồi rerun không gọi lại model; giữ phân trang 20/200, tìm SKU cuối, chat/follow-up, form, reset, isolation và staff. Nằm trong tổng 107. |
| Syntax | Các module catalog/SQL Server/UI được import và chạy qua suite; T-SQL được kiểm tra trên SQL Server thật như bên dưới. |
| `python -m pip check` | `No broken requirements found.` |
| HTTP localhost | Hai server khách/staff đã trả `200 ok` tại endpoint `/_stcore/health`. |

Log tự động: [artifacts/test_results.txt](../artifacts/test_results.txt).

## Chatbot gọi LLM và layout Mộc AI

`SHOP_LLM_ENABLED=true` trong `.env`: dữ liệu vẫn demo nhưng chat gọi Ollama thật để hiểu ý định và soạn câu trả lời. `chatbot/shop_rag.py` giữ generation riêng; client/E5/Chroma/retrieval tiếp tục kế thừa `app.py`. Không gọi model chỉ vì tải trang/tìm tên sản phẩm.

- Ba lượt thật liên tiếp: hỏi khả năng trợ lý → hỏi giá/tồn kho DEMO-187 → hỏi tiếp món vừa xem để làm quà. Đều `answered`, `response_origin=llm`; lưu đúng 6 tin nhắn vào history. Gửi lại request ID trả kết quả đã commit, không gọi thêm model.
- Giá DEMO-187 đọc SQL là **108.000 ₫/hộp**, tồn **550 hộp** ở lần thử; generation chèn đúng các giá trị đã kiểm tra. Đây là stock demo cố định, không phải kho kinh doanh trực tiếp.
- Câu thật “Tìm bánh dưới 100k mỗi hộp, không chứa sữa”: LLM extract → parser kiểm tra bằng chứng → lọc SQL → E5/Chroma/RAG gốc → LLM generation → validation → lưu outcome. Kết quả BK-001/BK-004/BK-006 đều là hộp, dưới 100k, có nhãn milk=absent còn hiệu lực trong snapshot demo. Không dùng unknown của 187 loại mới để kết luận không có sữa.
- Lần generation đầu của câu gợi ý viết số và nhãn thành phần ngoài token, bị chặn; một lượt sửa có giới hạn đã tạo phản hồi hợp lệ. Tổng lượt này 3 lần gọi model (1 extract + 2 generation); lỗi mạng hoặc hai phản hồi không hợp lệ không công bố nội dung chưa kiểm tra.
- 10 test mới về client generation, history, giá/tồn kho hiện tại, chặn số/nguồn/HTML/khẳng định ngoài bằng chứng, giữ chính sách nguyên văn, một lần sửa định dạng, timeout, parser đối chiếu độc lập và không hứa nhận đơn/thanh toán. Cùng 1 AppTest mới, tổng tăng 96 → 107.

Bằng chứng live: [chat_llm_check.json](../artifacts/chat_llm_check.json), [chat_recommendation_check.json](../artifacts/chat_recommendation_check.json). Các cuộc gọi này chỉ dùng câu hỏi và catalog demo, không dùng dữ liệu khách thật. Đây là smoke test, chưa phải đánh giá chất lượng model trên toàn bộ cách diễn đạt của khách.

Panel bên phải đổi sang **Mộc AI** với lịch sử cuộn, ô “Nhắn cho Mộc…”, tin nhắn khách xuất hiện ngay khi đang xử lý và spinner trong luồng chat. Tùy chọn/criteria/hỗ trợ được thu gọn. Markdown dùng để hiển thị câu trả lời; không bật HTML từ nội dung model. Không stream token chưa qua validation.

Đã gửi một câu hỏi thật qua trình duyệt trên bản cuối và quan sát spinner → câu trả lời LLM. Desktop **1280×720**: trợ lý nằm bên phải sản phẩm, ô nhập nằm hoàn toàn trong viewport (đáy 575px), không tràn ngang. Mobile **375×820**: thu gọn sidebar, trợ lý nằm trước danh mục, ô nhập ở đáy 566,6px và không tràn ngang. Đã trả viewport về kích thước mặc định và đóng riêng tab QA; tab người dùng vẫn mở.

Ảnh bản chat mới: [desktop](../artifacts/chat-desktop.png), [mobile](../artifacts/chat-mobile.png). Bằng chứng DOM/câu trả lời UI: [chat-layout-check.json](../artifacts/chat-layout-check.json). Chưa kiểm tra tải nhiều người dùng hoặc toàn bộ câu hỏi tự do; model vẫn cần giám sát khi dùng dữ liệu kinh doanh thật.

## SQL Server / SSMS và RAG thật

Đã tạo database riêng **ILMOON / ShopChatbotDemo** trên SQL Server 2019, dùng Windows Authentication và ODBC Driver 17. Không sửa database khác. Runtime `.env` đã chuyển catalog sang `sqlserver`; hàng đợi hỗ trợ giữ SQLite riêng.

- Đọc thật đủ **200 sản phẩm, 4 FAQ**; 104 bánh, 64 kẹo, 32 chocolate. Tên loại và SKU duy nhất.
- Tạo tồn kho ngẫu nhiên bằng SQL Server trong khoảng **1–999** rồi commit. Bộ hiện tại nhỏ nhất 2, lớn nhất 999; không bắt buộc bộ ngẫu nhiên chứa cả hai đầu mút.
- Hai lần đọc và tải UI giữ nguyên toàn bộ 200 số lượng. Không random lại trong luồng chat/reload.
- Chạy chính `catalog/sqlserver/catalog_200.sql` trên database demo đã có: thành công, giữ nguyên toàn bộ stock. Import từ chối ghi đè catalog hiện hữu; transaction rollback được kiểm tra tự động. Chưa thử tạo database từ script SSMS trên máy mới trống; database hiện tại đã được tạo/seed thành công bằng CLI cùng schema.
- Đọc catalog từ SQL Server → `ShopRAG.rebuild/retrieve` → các hàm index/retrieve **gốc trong `app.py`** → E5 cached / Chroma thật: **204 records**, metadata `sql_table`, `sql_id`, `source` đối chiếu đúng hàng SQL. Câu thử chocolate gianduja trả `DEMO-187` đầu tiên.
- AppTest kết nối SQL Server thật: trang khách 20 sản phẩm/trang, sang trang 10, tìm `DEMO-187` đúng một kết quả, không gửi chat; trang staff render không lỗi.
- Lượt tích hợp SQL này không gọi API model trả phí. Kiểm tra model trước đó bên dưới vẫn chỉ áp dụng fixtures cũ.

Bằng chứng: [sqlserver_check.json](../artifacts/sqlserver_check.json), [sqlserver_smoke.txt](../artifacts/sqlserver_smoke.txt). 200 loại là **demo**; giá/số lượng minh họa. 187 loại mới chưa có nhãn thành phần được xác minh, giữ trạng thái unknown.

## Kiểm tra provider và embedding thật

Model `gpt-oss:20b`, embedding `intfloat/multilingual-e5-small`, Chroma cosine. Dùng **fixtures giả lập**, không dùng dữ liệu khách hoặc catalog shop thật.

1. Ba câu trích xuất thật đã đạt kiểm tra JSON/ý định: tìm bánh dưới 100k và tránh sữa, hỏi đổi trả, hỏi rẻ hơn. Không dùng Cloud schema enforcement.
2. Lần kiểm tra tích hợp cuối chạy `handle_message` → client Ollama gốc → validation → luật Python → `chatbot/shop_rag.py` → `app.index_knowledge_base` / `app.retrieve_relevant_chunks` → E5/Chroma thật → kết quả → SQLite.
3. Câu `Tìm bánh dưới 100k mỗi hộp, không chứa sữa` trả đúng `BK-001`, `BK-004`. SKU dạng gói không lọt qua điều kiện hộp.
4. Index thử có 17 records (13 SKU active + 4 FAQ). Gửi lại cùng request ID trả nguyên kết quả đã commit, không gọi thêm model.
5. Lần tích hợp cuối dùng 1 lượt model, tổng 64,22 giây gồm nạp embedding, tạo index, model và SQLite; không xem đây là benchmark latency vận hành. Cộng với lượt thử trích xuất trước có 4 lượt model thật trong kiểm tra này.

Máy đã có cache embedding. Lượt probe đầu gặp lỗi dọn `TemporaryDirectory` do Chroma giữ file trên Windows, sau khi các assertion chức năng đã đạt. Lượt cuối dùng thư mục thử riêng ở `artifacts/runtime/`, kết thúc exit 0. Không xóa catalog/index gốc hoặc sửa `.env`. Dữ liệu runtime thử không được đưa vào Git.

Kết quả máy đọc được: [artifacts/connection_check.json](../artifacts/connection_check.json). Đây là smoke test kỹ thuật trước khi mở rộng lên 200 loại, không phải kết quả nghiệm thu model trên catalog SQL mới hoặc dữ liệu shop thật.

## Phiên bản xác minh

- Python 3.13.7
- Streamlit 1.65.0
- ChromaDB 1.5.9
- Ollama Python 0.6.3
- HTTPX 0.28.1
- Sentence Transformers 5.7.0
- python-dotenv 1.2.4
- Pydantic 2.13.5
- pyodbc 5.3.0 / Microsoft ODBC Driver 17 / SQL Server 2019

## UI và trạng thái vận hành

Đã tinh gọn giao diện theo ảnh và chỉ đạo mới: danh mục/bộ lọc bên trái, sản phẩm ở giữa, trợ lý mua sắm bên phải. Bỏ banner, KPI và bảng giải thích lặp lại. Chi tiết sản phẩm mở bằng popover; nguồn và tiêu chí được thu gọn. Kết quả tư vấn cập nhật ở vùng sản phẩm, không lặp thẻ trong chat.

Đã kiểm tra bằng in-app browser ở **920×668** và **375×820**. Desktop: sản phẩm nằm bên trái panel trợ lý, ô nhập chat trong viewport. Mobile: thu gọn menu bên, trợ lý xếp phía trên sản phẩm; không tràn ngang. Đã thử mở/đóng chi tiết và bấm gợi ý; trả đúng BK-004/BK-001 ở vùng sản phẩm. Viewport thử đã được trả về kích thước ban đầu; tab kiểm tra riêng đã đóng.

Ảnh kiểm tra: [desktop](../artifacts/ui-desktop.jpg), [mobile](../artifacts/ui-mobile.jpg). Số đo DOM: [ui-validation.json](../artifacts/ui-validation.json).

Đã tham khảo skill **UI UX Pro Max**: style `Minimalism & Swiss Style` và guideline `Focus States`. Màu và panel xanh tím được điều chỉnh theo ảnh người dùng. Các cặp chữ đã đo contrast: nội dung 12,80:1; chữ phụ 4,71:1; nút chính 4,89:1; giá 8,73:1; nhãn tồn kho 5,13:1. Đây là kiểm tra các cặp màu chính, không phải chứng nhận accessibility toàn ứng dụng.

Hai server để `demo` trên loopback: khách cổng 8501, nhân viên cổng 8502, đọc 200 loại từ SQL Server. Dữ liệu thật chưa được duyệt; connected từ chối demo. Chưa có live inventory ngoài catalog này, thông báo staff qua kênh ngoài hoặc public hosting. Ảnh UI phía trên được chụp với bộ fixtures cũ; phân trang 200 loại đã kiểm tra bằng AppTest.

## Trước pilot với khách thật

Chủ shop cần cung cấp catalog/FAQ và ảnh thật đã xác minh, quy định độ mới, người theo dõi hỗ trợ, chính sách dữ liệu gửi Cloud/retention và bộ câu nghiệm thu. UI đã kiểm tra ở hai kích thước nêu trên; nội dung thương mại vẫn là demo. Public deployment, xác thực staff, kênh nhắn tin và storage hosting là mốc riêng; không tự bật trong bản local này.
