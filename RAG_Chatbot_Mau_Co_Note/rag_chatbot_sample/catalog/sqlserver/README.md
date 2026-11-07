# Quản lý catalog bằng SSMS

Kết nối **Database Engine**, server **ILMOON** (hoặc `localhost`), **Windows Authentication**. Database **ShopChatbotDemo** đã có 200 loại bánh kẹo và 4 FAQ. Refresh mục Databases nếu chưa thấy.

Mở [queries.sql](queries.sql) trong SSMS rồi Execute để xem sản phẩm. Có thể dùng **Edit Top 200 Rows** trên `dbo.products` để chỉnh `price_vnd`, `stock_qty` và các thông tin khác. Đây là dữ liệu demo; giá và số lượng không phải tồn kho kinh doanh đã xác minh.

- `dbo.products`: tên, SKU, giá VND, tồn kho, mô tả, quy cách, nguồn và bằng chứng thành phần.
- `dbo.faqs`: câu hỏi/chính sách cùng approval, điều kiện và hiệu lực.
- `dbo.catalog_metadata`: phiên bản, cờ demo/approved, ngày snapshot và ngưỡng độ mới.

Số lượng đã tạo ngẫu nhiên trong khoảng **1–999** và được lưu tại `stock_qty`. Reload app hoặc hỏi chatbot không tạo lại số. Chỉ chạy [randomize_stock.sql](randomize_stock.sql) khi muốn tạo bộ số mới cho cả 200 loại demo; script từ chối catalog không phải demo. Sau khi bán hết, có thể đặt số lượng 0 và trạng thái `out_of_stock` trong cùng một UPDATE.

Ví dụ cập nhật demo có kiểm soát (thay giá/số lượng theo ý bạn):

```sql
USE ShopChatbotDemo;
UPDATE dbo.products
SET price_vnd = 45000,
    stock_qty = 123,
    availability = 'available',
    price_updated_at = (SELECT as_of FROM dbo.catalog_metadata WHERE id = 1),
    stock_updated_at = (SELECT as_of FROM dbo.catalog_metadata WHERE id = 1)
WHERE id = N'DEMO-187';
```

Demo dùng ngày snapshot cố định; ví dụ trên cập nhật theo snapshot đó. Dữ liệu thật dùng thời điểm thực có múi giờ và ngưỡng do shop duyệt. Không đổi `demo`/`approved` chỉ để vượt validation. Các trường `_json` phải là JSON đúng schema; nhãn chưa xác minh giữ `unknown`.

Giá và tồn kho được đọc lại từ SQL Server mỗi lượt, không lấy từ vector. Đổi tên/mô tả/FAQ: dừng chat, chạy lệnh dưới từ thư mục ứng dụng rồi khởi động lại:

```powershell
python app.py --rebuild-shop --mode demo
```

`chatbot/shop_rag.py` tiếp tục dùng embedding/index/retrieval gốc trong `app.py`. Index gồm **204 records**: 200 sản phẩm và 4 FAQ; kết quả giữ `sql_table`, `sql_id`, `source` để đối chiếu bảng. Tối đa 20 sản phẩm mỗi trang UI, tìm kiếm trên toàn bộ catalog.

Trên máy mới, cài requirements/ODBC 17/SQL Server rồi mở [catalog_200.sql](catalog_200.sql) bằng SSMS và Execute. Script tạo riêng `ShopChatbotDemo`; giữ dữ liệu nếu catalog 200 loại đã có, từ chối ghi đè catalog khác. `schema.sql` dùng để tạo bảng trong database trống cho import dữ liệu đã duyệt. Sao lưu database bằng SSMS trước khi chỉnh dữ liệu hàng loạt.

Sau khi có dữ liệu sản phẩm, chạy `python -m orders.commerce` từ thư mục ứng dụng hoặc chạy [commerce.sql](../../orders/commerce.sql) trong đúng database catalog. Bước này bổ sung các bảng giá sỉ và đơn hàng, đồng thời giữ các hệ số giá sỉ đã có.

Connection string ở `.env` chỉ dùng Windows Authentication local. `TrustServerCertificate=yes` phục vụ SQL Server local; khi triển khai máy chủ thật, cấu hình chứng chỉ phù hợp. SQLite của hàng đợi hỗ trợ tách biệt với catalog SQL Server.
