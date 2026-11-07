# Đánh giá chatbot khách hàng Mộc AI

*Ngày rà soát 07/11/2026*

Mộc AI tư vấn bánh kẹo, tra cứu giá và tồn kho, trả lời chính sách, hỗ trợ thao tác giỏ và chuyển yêu cầu cho nhân viên. Báo cáo tập trung vào trải nghiệm chat của khách hàng và khả năng phục vụ nhiều phiên đồng thời. Trang nhân viên có một nhiệm vụ trong phạm vi này là tiếp nhận yêu cầu do chatbot chuyển sang.

| Khía cạnh | Đánh giá hiện tại |
| --- | --- |
| Chất lượng code | Backend kiểm tra giá, tồn kho, nhãn và dữ kiện giao dịch. Các nhánh xử lý từ khóa, RAG và nghiệp vụ thương mại có thứ tự ưu tiên chưa nhất quán, nên một nhánh có thể bỏ qua phần còn lại của câu hỏi. |
| Nghiệp vụ chatbot | Đã có tư vấn theo tiêu chí, theo dõi ngữ cảnh, trả lời chính sách từ nguồn được duyệt và chuyển tiếp sang người thật. Cần cải thiện câu hỏi nhiều ý, cách hiểu câu diễn đạt khác và khả năng tiếp nhận yêu cầu khi model lỗi. |
| Khả năng mở rộng | Một lượt xử lý chậm có thể chặn phiên khác qua khóa ghi SQLite. Catalog gửi cho model và số lần tải lại catalog đều tăng chi phí khi phục vụ nhiều khách. |
| Tiếp nhận yêu cầu | Nhân viên cần một danh sách yêu cầu, nội dung khách gửi và ngữ cảnh đủ để tiếp nhận. Các chỉ số RAG, FAQ và cấu hình model thuộc phần vận hành chatbot. |

## Những phần nên giữ

- **Dữ kiện bán hàng do backend quyết định.** Giá, tồn kho và bằng chứng nhãn đến từ catalog. Nội dung về thành phần và giao dịch được tạo từ dữ kiện đã kiểm tra. [shop.py](../chatbot/shop.py) · [commerce_chat.py](../orders/commerce_chat.py)
- **FAQ dùng câu trả lời đã duyệt.** Backend kiểm tra hiệu lực và điều kiện áp dụng trước khi trả lời. Khi thiếu chính sách hoặc có mâu thuẫn, chatbot báo phần cần xác minh. [shop.py](../chatbot/shop.py) · [tests/test_shop.py](../tests/test_shop.py)
- **Yêu cầu có mã và được lưu trước khi báo cho khách.** Hàng đợi giữ câu hỏi, tiêu chí, SKU và nguồn đã kiểm tra. Gửi lại cùng mã yêu cầu được chống ghi trùng. [support.py](../tickets/support.py) · [shop.py](../chatbot/shop.py)

## Cần sửa trước khi mở pilot

### 1. Một câu hỏi có nhiều ý có thể bị trả lời thiếu

- Nhánh giá sỉ và giỏ chạy trước luồng xử lý câu hỏi về sản phẩm và chính sách.
- Tái hiện với fixture demo. “Giá sỉ 10 hộp BK-001 và đổi trả thế nào?” chỉ trả báo giá. Kết quả vẫn được đánh dấu `answered` dù phần đổi trả bị bỏ qua.
- Tách ý định thương mại khỏi ý định tra cứu chính sách. Trả từng phần đã xác minh, đánh dấu phần còn thiếu và giữ nguyên bước xác nhận cho thao tác giỏ hoặc chốt đơn.
- Khách phải hỏi lại phần bị bỏ qua. Chỉ số trả lời thành công cũng dễ cao hơn số lượt thực sự được giải quyết. [shop.py](../chatbot/shop.py), dòng 614–623 và 723–745.

### 2. FAQ đã duyệt vẫn có thể không đến được khách

- Chatbot chọn FAQ bằng từ khóa rồi gọi Chroma trước khi chèn câu trả lời lấy từ catalog.
- Tái hiện hai trường hợp. “Hàng bị lỗi lúc nhận thì có được gửi lại không?” trả `missing_policy` dù model giả lập đã phân loại đúng là FAQ. Câu “Chính sách đổi trả thế nào?” khớp nguồn, nhưng khi giả lập Chroma lỗi thì trả `technical_failure`.
- Với câu khớp rõ, trả trực tiếp chính sách SQL đã duyệt cùng điều kiện. Với câu diễn đạt khác, dùng RAG hoặc bí danh để tìm ứng viên, rồi kiểm tra lại chủ đề, hiệu lực và phạm vi. Hỏi lại khi chưa đủ căn cứ để chọn chính sách.
- Khách có thể phải chờ nhân viên cho một câu hỏi mà cửa hàng đã có câu trả lời. [shop.py](../chatbot/shop.py), dòng 507–538 và 729–741 · [shop_rag.py](../chatbot/shop_rag.py), dòng 23–50.

### 3. Chatbot có thể ghi sai số lượng vào giỏ

- Bộ phân tích số lượng chạy trước schema kiểm tra giỏ.
- Tái hiện với “Thêm BK-001 2.5 hộp vào giỏ”, số lượng được chuyển thành 2. “Thêm 2 hộp BK-001 và BK-002 vào giỏ” áp 2 hộp cho cả hai SKU dù phạm vi số lượng chưa rõ.
- Kiểm tra nguyên vẹn cụm số lượng trước khi chuyển kiểu. Với nhiều SKU, yêu cầu số lượng từng món hoặc xác nhận rõ “mỗi loại”. Nếu mơ hồ, giữ nguyên giỏ và hỏi lại.
- Bảng giá và tổng tiền có thể đúng theo giỏ nhưng sai với yêu cầu của khách. [commerce_chat.py](../orders/commerce_chat.py), dòng 36–53.

### 4. Yêu cầu gặp nhân viên đi qua model và bị tách thành nhiều phiếu

- Nút “Yêu cầu nhân viên” gửi câu văn vào luồng chat chung. Khi nhân viên tiếp quản, mỗi tin nhắn bổ sung được lưu thành một ticket mới.
- Giả lập Ollama hết thời gian chờ. Yêu cầu “Tôi muốn gặp nhân viên hỗ trợ” vẫn được lưu, nhưng bị gắn lý do `technical_failure`. Sau khi tiếp quản, hai tin bổ sung tạo thêm hai mã, thành ba ticket cho cùng tình huống.
- Cho nút gặp nhân viên đi thẳng vào luồng tạo ticket, độc lập với model và catalog. Gắn tin bổ sung vào yêu cầu đang mở và chỉ báo “đã tiếp nhận” sau khi nhân viên nhận. Phân biệt lỗi kỹ thuật với yêu cầu hỗ trợ của khách.
- Nhân viên dễ bỏ sót ngữ cảnh, còn khách có thể nhận nhiều mã cho cùng một việc. [streamlit_app.py](../streamlit_app.py), dòng 31–37 và 241–243 · [shop.py](../chatbot/shop.py), dòng 608–609, 631–637 và 799–812 · [support.py](../tickets/support.py), dòng 85–93.

### 5. Một lượt chat chậm có thể chặn các phiên khác

- Giao dịch SQLite giữ khóa ghi trong lúc chatbot đọc SQL Server, truy xuất Chroma và gọi Ollama.
- Thử bằng hai kết nối tới SQLite tạm. Khi kết nối thứ nhất giữ giao dịch, yêu cầu từ một phiên khác trả `queue_failure` sau khoảng **5,5 giây**. Cùng loại khóa này được dùng khi nhân viên cập nhật trạng thái yêu cầu.
- Gọi model và truy xuất ngoài khóa ghi. Khi lưu, kiểm tra phiên bản hội thoại và trạng thái tiếp quản. Dùng giao dịch ngắn cho phần thay đổi dữ liệu và giữ khóa chống ghi trùng.
- Một khách hỏi câu cần model chậm có thể khiến khách khác gửi tin hoặc nhân viên nhận yêu cầu thất bại. [support.py](../tickets/support.py), dòng 50–64 và 132–149 · [shop.py](../chatbot/shop.py), dòng 775–822.

## Nên tối ưu khi tăng tải

### 6. Giới hạn dữ liệu và số lượt xử lý cho mỗi câu hỏi

- Bước trích xuất gửi toàn bộ danh sách sản phẩm cho model. UI đọc catalog khi chạy lại và đọc thêm một lần khi lấy báo giá giỏ.
- Với bộ seed 200 sản phẩm, riêng mảng sản phẩm trong payload đã khoảng **38,8 KB UTF-8**. Dữ liệu còn tăng theo số SKU, trước khi tính lịch sử và prompt.
- Xử lý trực tiếp SKU rõ, câu FAQ khớp chắc và yêu cầu gặp nhân viên. Với câu mở, gửi một nhóm ứng viên giới hạn cho model. Tái sử dụng catalog trong cùng lượt hiển thị và đọc lại giá, tồn khi xác nhận giao dịch.
- Lượt chat tốn thêm thời gian và chi phí khi danh mục lớn, kể cả với câu chỉ hỏi một SKU. [shop_rag.py](../chatbot/shop_rag.py), dòng 81–89 · [streamlit_app.py](../streamlit_app.py), dòng 38–43 và 161–170 · [shop.py](../chatbot/shop.py), dòng 502–505.

### 7. Giữ chỉ mục RAG sẵn sàng khi nội dung đổi

- Tên collection Chroma phụ thuộc vào dấu vân tay của mô tả sản phẩm và FAQ đang hiệu lực.
- Khi FAQ hết hiệu lực, danh sách record đổi theo thời gian. Lượt truy xuất có thể lỗi nếu collection cho dấu vân tay mới chưa được dựng, kể cả với câu hỏi sản phẩm.
- Kiểm tra hiệu lực FAQ khi chọn nguồn để trả lời. Dựng và kiểm tra chỉ mục mới trước khi chuyển sang dùng. Giữ tra SKU chính xác và FAQ đã xác định chạy được nếu truy xuất ngữ nghĩa tạm gián đoạn.
- Một thay đổi nội dung có thể làm gián đoạn nhiều câu hỏi hơn phần catalog vừa đổi. [shop.py](../chatbot/shop.py), dòng 137–157 · [shop_rag.py](../chatbot/shop_rag.py), dòng 23–50.

## Phạm vi trang nhân viên

- **Giữ luồng tiếp nhận.** Danh sách yêu cầu mới, mã, thời điểm, câu hỏi gốc, lý do chuyển, các tin bổ sung và ngữ cảnh liên quan. Trạng thái tối thiểu là mới và đã tiếp nhận. Ngữ cảnh lấy từ phiếu do chatbot tạo. [staff_app.py](../staff_app.py), dòng 35–69 · [support.py](../tickets/support.py), dòng 36–43.
- **Gỡ phần chẩn đoán khỏi màn hình tiếp nhận.** Code hiện đọc catalog, đếm FAQ, kiểm tra Chroma và có nút thử cấu hình Ollama ngay trên trang nhân viên. Đưa các kiểm tra này về lệnh vận hành sẵn có, như `python app.py --check` và lệnh kiểm tra catalog. Giữ màn hình này dành cho hàng đợi, kể cả khi dịch vụ tư vấn gặp sự cố. [staff_app.py](../staff_app.py), dòng 85–116 · [README.md](../README.md).
- **Quản lý đơn và thanh toán nằm ngoài phạm vi trang tiếp nhận.** Đánh giá chỉ giữ phần ảnh hưởng trực tiếp đến chat của khách, như hiểu đúng số lượng, báo giá và xác nhận thao tác. [staff_app.py](../staff_app.py), dòng 118–152 · [commerce_chat.py](../orders/commerce_chat.py).

## Thứ tự triển khai và tiêu chí kiểm thử

| Giai đoạn | Việc ưu tiên | Điều kiện đạt |
| --- | --- | --- |
| Độ đúng câu trả lời | Mục 1–3. Kiểm thử tư vấn, câu nhiều ý, FAQ diễn đạt khác và số lượng. | Câu nhiều ý có kết quả theo từng phần. FAQ giữ nguyên điều kiện. Số lượng mơ hồ không đổi giỏ. |
| Tiếp nhận ổn định | Mục 4–5. Tách đường gửi yêu cầu khỏi model và rút ngắn khóa ghi. | Khách gửi yêu cầu được khi Ollama lỗi. Các tin bổ sung xuất hiện trên cùng mã. Một lượt gọi model chậm không làm phiên khác hoặc thao tác nhận yêu cầu thất bại. |
| Tăng tải | Mục 6–7. Đo từ catalog 200 SKU rồi tăng theo nhu cầu dự kiến. | Theo dõi p95 thời gian phản hồi, tỷ lệ trả lời đủ ý, tỷ lệ chuyển nhân viên, lỗi khóa SQLite, số lần gọi model và kích thước payload. Kiểm tra lại khi thay đổi hoặc hết hiệu lực FAQ. |

**Kết luận.** Ưu tiên làm chatbot trả lời đủ ý và đúng nguồn, rồi bảo đảm khách chuyển được yêu cầu khi model gặp lỗi. Phần nhân viên chỉ cần nhận được đúng yêu cầu và đủ ngữ cảnh để xử lý.

**Giới hạn kiểm chứng.** Báo cáo dựa trên code, kết quả 135 test của lượt kiểm thử gần nhất và các ca bổ sung dùng fixture, Ollama giả lập hoặc SQLite tạm đã được dọn. Chưa đo tải thực tế hoặc đánh giá chất lượng câu trả lời trên hội thoại khách thật.
