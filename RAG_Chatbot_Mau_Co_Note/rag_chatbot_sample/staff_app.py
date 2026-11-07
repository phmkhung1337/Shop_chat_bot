"""Separate LOCAL staff app. Never mount this entry point in a public customer app."""
from pathlib import Path

from dotenv import load_dotenv
import os
import streamlit as st

from chatbot.shop import ALLERGENS, load_catalog
from tickets.support import SupportStore, STATUSES
from ui.ui_components import apply_theme, brand, criteria_summary, page_header, section_title

st.set_page_config(page_title="Mộc · Hàng đợi hỗ trợ", page_icon="📋", layout="wide")
apply_theme()
load_dotenv(Path(__file__).parent / ".env")
mode = os.getenv("SHOP_MODE", "demo")
db_path = os.getenv("SHOP_DB_PATH") or Path(__file__).parent / ("shop_demo.sqlite3" if mode == "demo" else "shop_support.sqlite3")

try:
    store = SupportStore(db_path)
except Exception:
    st.error("Không mở được hàng đợi SQLite. Kiểm tra quyền ghi và đường dẫn dữ liệu.")
    st.stop()

with st.sidebar:
    brand()
    st.caption("KHÔNG GIAN NHÂN VIÊN · LOCAL")
    st.info("Chỉ sử dụng trên máy local. Chưa có xác thực để mở trang này ra Internet.")
    status_filter = st.selectbox("Lọc trạng thái", ["Tất cả", *STATUSES])
    st.button("Làm mới hàng đợi", icon=":material/refresh:", width="stretch")
    st.caption("Chưa có adapter gửi tin. Kết quả xử lý chỉ được lưu để xem hoặc sao chép.")

page_header("Hàng đợi hỗ trợ", "Tiếp nhận, xác minh và bàn giao hội thoại.", "STAFF · LOCAL")
if "staff_notice" in st.session_state:
    st.success(st.session_state.pop("staff_notice"))
try:
    all_tickets = store.list_tickets()
except Exception:
    st.error("Chưa đọc được ticket. Vui lòng kiểm tra bộ nhớ và thử lại.")
    st.stop()
counts = {status: sum(t["status"] == status for t in all_tickets) for status in STATUSES}
cols = st.columns(3)
for col, label, status in zip(cols, ("Chờ tiếp nhận", "Đang xử lý", "Đã giải quyết"), STATUSES):
    col.metric(label, counts[status])

left, right = st.columns([1.65, 1], gap="large")
with left:
    section_title("Yêu cầu cần xử lý")
    tickets = [t for t in all_tickets if status_filter == "Tất cả" or t["status"] == status_filter]
    if not tickets:
        st.info("Chưa có ticket trong bộ lọc này. Yêu cầu từ trang khách sẽ xuất hiện tại đây sau khi lưu thành công.")
    else:
        selected_id = st.selectbox("Chọn yêu cầu", [t["id"] for t in tickets],
                                   format_func=lambda tid: next(f"{t['id']} · {t['status']} · {t['question'][:65]}" for t in tickets if t["id"] == tid))
        ticket = next(t for t in tickets if t["id"] == selected_id)
        with st.container(border=True):
            st.subheader(ticket["id"])
            st.caption(f"{ticket['created_at']} · Kênh {ticket['channel']} · Lý do {ticket['reason']}")
            st.text(ticket["question"])
            with st.expander("Tiêu chí và bằng chứng đã kiểm tra"):
                criteria_summary(ticket["criteria"])
                st.text("SKU đã kiểm tra: " + (", ".join(ticket["product_ids"]) or "Chưa có"))
                st.text("Nguồn: " + (", ".join(ticket["source_ids"]) or "Chưa có"))
            with st.form("ticket_" + selected_id):
                status = st.selectbox("Trạng thái xử lý", STATUSES, index=STATUSES.index(ticket["status"]))
                assignee = st.text_input("Nhân viên phụ trách", value=ticket["assignee"], max_chars=100)
                notes = st.text_area("Ghi chú nội bộ", value=ticket["notes"], max_chars=4000)
                resolution = st.text_area("Kết quả / nội dung trả lời để sao chép", value=ticket["resolution"], max_chars=4000)
                return_to_bot = st.checkbox("Bàn giao hội thoại lại bot sau khi giải quyết")
                submitted = st.form_submit_button("Lưu kết quả xử lý", type="primary", width="stretch")
            if submitted:
                try:
                    store.update_ticket(selected_id, status=status, assignee=assignee, notes=notes,
                                        resolution=resolution, return_to_bot=return_to_bot)
                except ValueError as exc:
                    st.error(str(exc))
                except Exception:
                    st.error("Chưa lưu được thay đổi. Trạng thái trước vẫn được giữ.")
                else:
                    st.session_state.staff_notice = "Đã lưu cập nhật vào SQLite. Chưa gửi tin cho khách."
                    st.rerun()
            if ticket["resolution"]:
                st.caption("Nội dung để sao chép — chưa gửi ra ngoài")
                st.code(ticket["resolution"], language=None)

with right:
    with st.container(border=True, key="analysis"):
        section_title("Tình trạng dữ liệu")
        st.text("Chế độ: " + mode.upper())
        try:
            catalog = load_catalog(mode=mode)
            st.success(f"Đã đọc {len(catalog.products)} sản phẩm · {len(catalog.active_faqs())} FAQ đang hiệu lực")
            st.caption("Snapshot: " + catalog.version)
            st.caption("Mốc dữ liệu: " + catalog.as_of.isoformat())
            st.caption("Nguồn SQL: " + catalog.database_path)
            if mode == "demo":
                st.warning("Catalog minh họa, chưa phải dữ liệu kinh doanh đã xác minh.")
            if os.getenv("SHOP_LLM_ENABLED", "true").strip().lower() == "true":
                from chatbot.shop_rag import ShopRAG
                try:
                    ShopRAG().ensure_current(catalog)
                    st.success("Chỉ mục shop khớp nguồn hiện tại.")
                except Exception:
                    st.error("Chỉ mục chưa sẵn sàng/cần rebuild. Dừng chat trước khi cập nhật chỉ mục.")
        except ValueError as exc:
            st.error(str(exc))
        section_title("Kết nối model")
        st.caption("Không gọi model chỉ để tải trang. Nút dưới kiểm tra cấu hình, chưa kiểm tra mạng.")
        if st.button("Kiểm tra cấu hình kết nối", width="stretch"):
            try:
                from app import create_ollama_client
                with create_ollama_client():
                    pass
                st.info("Cấu hình có thể khởi tạo client. Chưa gửi yêu cầu tới provider.")
            except Exception:
                st.error("Cấu hình chưa hợp lệ. Kiểm tra .env trên máy; không đưa API key lên giao diện.")
        st.caption("Ticket, audit và tiêu chí được lưu local. Chủ shop cần chốt người trực và thời hạn lưu trước khi vận hành thật.")

st.divider()
if st.toggle('Quản lý đơn hàng và thanh toán', value=False):
    from orders.commerce import CommerceStore
    from orders.commerce_chat import order_text
    commerce = CommerceStore()
    try:
        orders = commerce.orders()
        if not orders:
            st.info('Chưa có đơn hàng trong SQL Server.')
        else:
            selected_order = st.selectbox('Chọn đơn hàng', [order['id'] for order in orders])
            order = next(order for order in orders if order['id'] == selected_order)
            st.write(order_text(order))
            st.dataframe(order['lines'], hide_index=True, width='stretch')
            if order.get('payment_reference'):
                st.caption('Mã giao dịch/phiếu thu: ' + order['payment_reference'])
            if order['status'] == 'confirmed' and order['payment_status'] == 'pending':
                with st.form('payment_' + order['id']):
                    action_label = st.selectbox('Thao tác đơn hàng', ['Ghi nhận đã thanh toán', 'Hủy đơn và hoàn lại tồn kho'])
                    reference = st.text_input('Mã giao dịch hoặc phiếu thu đã kiểm tra', max_chars=200)
                    verified = st.checkbox('Tôi đã đối chiếu giao dịch hoặc xác nhận yêu cầu hủy đơn')
                    submitted_payment = st.form_submit_button('Lưu trạng thái đơn')
                if submitted_payment:
                    if not verified:
                        st.warning('Cần đối chiếu và xác nhận trước khi lưu.')
                    else:
                        try:
                            commerce.update_payment(order['id'], action='paid' if action_label == 'Ghi nhận đã thanh toán' else 'cancel', reference=reference)
                        except ValueError as exc:
                            st.warning(str(exc))
                        else:
                            st.session_state.staff_notice = 'Đã lưu trạng thái đơn trong SQL Server. Không thực hiện thu tiền/hoàn tiền tự động.'
                            st.rerun()
    except ValueError as exc:
        st.warning(str(exc))
