"""Customer entry point. Run: python -m streamlit run streamlit_app.py"""
from uuid import uuid4

import streamlit as st

from chatbot.shop import ALLERGENS, create_engine, handle_message, normalize, money
from orders.commerce_chat import order_text
from ui.ui_components import (apply_theme, brand, criteria_summary, page_header,
                           render_outcome, render_product, section_title)

st.set_page_config(page_title="Mộc · Trợ lý bánh & kẹo", page_icon="🍪", layout="wide", initial_sidebar_state="auto")
apply_theme()


def queue_message(text, changes=None, confirm_removal=False, *, commerce_action=None, request_id=None):
    if "pending" not in st.session_state:
        st.session_state.pending = {"message": text, "request_id": request_id or uuid4().hex,
                                    "criteria_changes": changes, "confirm_removal": confirm_removal, 'commerce_action': commerce_action}


def chat_submitted():
    text = st.session_state.get("customer_message")
    if text:
        queue_message(text)


if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = uuid4().hex
    st.session_state.messages = []
    st.session_state.last_result = None
if "shop_engine" not in st.session_state:
    try:
        st.session_state.shop_engine = create_engine(load_on_start=False)
    except Exception:
        page_header("Trợ lý cửa hàng", "Tư vấn bánh kẹo theo nhu cầu của bạn", "CHƯA SẴN SÀNG")
        st.error("Bộ dữ liệu cửa hàng chưa sẵn sàng. Vui lòng liên hệ người vận hành để kiểm tra trước khi tư vấn.")
        st.stop()
engine = st.session_state.shop_engine
catalog = None
try:
    catalog = engine.refresh()
except ValueError:
    st.error("Dữ liệu cửa hàng hiện không hợp lệ. Chưa sử dụng snapshot cũ để tư vấn.")

def process_pending():
    pending = st.session_state.pop("pending")
    with st.spinner("Mộc đang suy nghĩ và soạn câu trả lời…" if engine.llm_enabled else "Đang đối chiếu thông tin…"):
        result = handle_message(pending["message"], st.session_state.conversation_id,
                                pending["request_id"], engine=engine,
                                criteria_changes=pending["criteria_changes"], confirm_removal=pending["confirm_removal"],
                                commerce_action=pending.get('commerce_action'))
    if pending["message"] == "/reset" and result["status"] == "answered":
        st.session_state.messages = []
        st.session_state.last_result = None
        st.session_state.catalog_browsing = False
        st.session_state.pop("retry", None)
        st.toast("Đã xóa lịch sử và tiêu chí phiên hiện tại.")
    else:
        st.session_state.messages.append({"question": pending["message"], "result": result})
        st.session_state.last_result = result
        st.session_state.catalog_browsing = False
        if result["status"] == "error":
            # A stored error would replay forever under the same request ID.
            st.session_state.retry = {**pending, "request_id": pending["request_id"] if result["reason"] == "queue_failure" else None}
        else:
            st.session_state.pop("retry", None)
    st.rerun()

try:
    state = engine.state(st.session_state.conversation_id)
except Exception:
    st.error("Chưa đọc được trạng thái hội thoại. Vui lòng thử lại sau.")
    st.stop()
criteria = state["criteria"]
last_result = st.session_state.last_result

with st.sidebar:
    brand()
    category_filter = st.radio("Danh mục", ["Tất cả"] + (sorted({p["category"] for p in catalog.products}) if catalog else []))
    with st.expander("Chỉnh tiêu chí", icon=":material/tune:"):
        with st.form("criteria_form"):
            categories = ["Chưa xác định"] + (sorted({p["category"] for p in catalog.products}) if catalog else [])
            current_category = criteria.get("category") or "Chưa xác định"
            if current_category not in categories:
                categories.append(current_category)
            category = st.selectbox("Loại sản phẩm", categories, index=categories.index(current_category))
            budget = st.number_input("Ngân sách (VND; 0 = chưa xác định)", min_value=0, max_value=1000000000,
                                     value=criteria.get("budget_vnd", 0), step=10000)
            scope_options = {"Chưa xác định": None, "Mỗi sản phẩm": "unit", "Tổng số lượng": "total"}
            scope_values = list(scope_options.values())
            scope = st.selectbox("Phạm vi ngân sách", list(scope_options), index=scope_values.index(criteria.get("budget_scope")))
            inclusive = st.checkbox("Cho phép bằng mức ngân sách", value=criteria.get("budget_inclusive", True))
            quantity = st.number_input("Số lượng (0 = chưa xác định)", min_value=0, max_value=10000, value=criteria.get("quantity", 0))
            packages = ["Chưa xác định", "hộp", "gói", "túi", "hũ"]
            pack = criteria.get("packaging") or "Chưa xác định"
            if pack not in packages:
                packages.append(pack)
            packaging = st.selectbox("Quy cách", packages, index=packages.index(pack))
            avoid = st.multiselect("Thành phần cần tránh", list(ALLERGENS), default=criteria.get("avoid_allergens", []),
                                   format_func=lambda key: ALLERGENS[key])
            other_avoid = st.text_input("Thành phần khác cần tránh (phân cách bằng dấu phẩy)", value=", ".join(criteria.get("avoid_ingredients", [])))
            brand_filter = st.text_input("Thương hiệu bắt buộc", value=criteria.get("brand", ""))
            flavor_filter = st.text_input("Hương vị bắt buộc", value=criteria.get("flavor", ""))
            excluded = st.text_input("Vị không chọn (phân cách bằng dấu phẩy)", value=", ".join(criteria.get("excluded_flavors", [])))
            confirmed = st.checkbox("Tôi xác nhận nếu bỏ điều kiện thành phần đã lưu")
            submitted = st.form_submit_button("Áp dụng tiêu chí", width="stretch", type="primary")
        if submitted:
            changes = {"category": None if category == "Chưa xác định" else category,
                       "budget_vnd": int(budget) or None, "budget_scope": scope_options[scope],
                       "budget_inclusive": inclusive, "quantity": int(quantity) or None,
                       "packaging": None if packaging == "Chưa xác định" else packaging,
                       "avoid_allergens": avoid, "avoid_ingredients": [x.strip() for x in other_avoid.split(",") if x.strip()], "brand": brand_filter.strip() or None,
                       "flavor": flavor_filter.strip() or None,
                       "excluded_flavors": [x.strip() for x in excluded.split(",") if x.strip()]}
            queue_message("Áp dụng tiêu chí tư vấn từ form.", changes, confirmed)
            st.rerun()
    with st.expander("Bắt đầu lại", icon=":material/restart_alt:"):
        st.caption("Xóa lịch sử và tiêu chí phiên hiện tại; giữ ticket và audit đã lưu.")
        confirm_reset = st.checkbox("Tôi đồng ý xóa lịch sử và tiêu chí")
        st.button("Xác nhận bắt đầu lại", disabled=not confirm_reset, width="stretch",
                  on_click=queue_message, args=("/reset",))
        st.caption("Tải lại trang tạo phiên mới; ticket vẫn được lưu.")
    if engine.mode == "demo":
        st.caption("Sản phẩm minh họa, không bán thật.")

page_header("Cửa hàng Mộc", "", "LOCAL DEMO" if engine.mode == "demo" else "CỬA HÀNG")

products_col, assistant_col = st.columns([1.55, 1], gap="medium")
with products_col:
    with st.container(key="product_catalog"):
        products_tab, cart_tab, orders_tab, policies_tab = st.tabs(["Sản phẩm", "Giỏ hàng", "Đơn hàng", "Chính sách"])
        with products_tab:
            search = st.text_input("Tìm theo tên hoặc SKU", placeholder="Tìm sản phẩm…", label_visibility="collapsed")
            if catalog:
                suggestions = last_result.get("products", []) if last_result else []
                show_suggestions = bool(suggestions) and not st.session_state.get("catalog_browsing", False) and not search and category_filter == "Tất cả"
                if show_suggestions:
                    section_title("Sản phẩm đang tư vấn")
                    if st.button("Xem toàn bộ sản phẩm", key="browse_all", width="stretch"):
                        st.session_state.catalog_browsing = True
                        st.rerun()
                    visible = suggestions
                else:
                    visible = [p for p in catalog.products if p["status"] == "active"
                               and (category_filter == "Tất cả" or p["category"] == category_filter)
                               and normalize(search) in normalize(p["name"] + " " + p["id"])]
                if not visible:
                    st.info("Chưa có sản phẩm khớp tìm kiếm.")
                if len(visible) > 20:
                    pages = (len(visible) + 19) // 20
                    page = st.selectbox("Trang sản phẩm", range(1, pages + 1), key="catalog_page")
                    st.caption(f"{len(visible)} sản phẩm · Trang {page}/{pages}")
                    visible = visible[(page - 1) * 20:page * 20]
                for start in range(0, len(visible), 2):
                    for col, product in zip(st.columns(2, gap="small"), visible[start:start + 2]):
                        with col:
                            render_product(product, catalog,
                                           criteria=last_result.get("criteria", {}) if show_suggestions else None,
                                           direct=product.get("direct", False) if show_suggestions else True,
                                           key="catalog", on_ask=queue_message)
        with cart_tab:
            cart = state.get('cart', [])
            if not cart:
                st.info('Giỏ hàng đang trống. Mở Chi tiết sản phẩm để chọn số lượng hoặc nhắn trợ lý thêm vào giỏ.')
            else:
                cart_dirty = False
                try:
                    quote = engine.quote_cart(st.session_state.conversation_id)
                except ValueError as exc:
                    quote = None
                    st.warning(str(exc))
                for item in cart:
                    product = catalog.product(item['product_id']) if catalog else None
                    with st.expander((product['name'] if product else item['product_id']) + ' · ' + str(item['quantity'])):
                        quantity = st.number_input('Số lượng trong giỏ', min_value=1, max_value=10000, value=item['quantity'], key='cart_qty_' + item['product_id'])
                        wholesale = st.checkbox('Áp dụng giá sỉ', value=item['wholesale'], key='cart_wholesale_' + item['product_id'])
                        cart_dirty = cart_dirty or int(quantity) != item['quantity'] or wholesale != item['wholesale']
                        st.button('Cập nhật', key='cart_set_' + item['product_id'], on_click=queue_message,
                                  args=('Cập nhật giỏ hàng.',), kwargs={'commerce_action': {'action': 'set', 'items': [dict(product_id=item['product_id'], quantity=int(quantity), wholesale=wholesale)]}})
                        st.button('Bỏ khỏi giỏ', key='cart_remove_' + item['product_id'], on_click=queue_message,
                                  args=('Bỏ sản phẩm khỏi giỏ.',), kwargs={'commerce_action': {'action': 'remove', 'items': [dict(product_id=item['product_id'])]}})
                if quote:
                    if cart_dirty:
                        st.info('Có thay đổi chưa lưu. Chọn Cập nhật cho từng sản phẩm trước khi chốt đơn.')
                    st.dataframe([{'Sản phẩm': line['name'], 'Số lượng': line['quantity'], 'Loại giá': 'Sỉ' if line['wholesale'] else 'Lẻ',
                                   'Đơn giá': money(line['unit_price_vnd']), 'Thành tiền': money(line['line_total_vnd'])} for line in quote['lines']], hide_index=True, width='stretch')
                    st.markdown('**Tiền hàng: ' + money(quote['total_vnd']) + '**')
                    st.caption('Chưa gồm phí giao hàng nếu có. Chốt đơn giữ số lượng trong kho và tạo trạng thái chờ thanh toán; chưa thu tiền online.')
                    with st.form('checkout_' + state['cart_revision'] + '_' + quote['signature'][:12]):
                        agreed = st.checkbox('Tôi đã kiểm tra số lượng, loại giá và tổng tiền hàng')
                        confirm_order = st.form_submit_button('Xác nhận chốt đơn', type='primary', width='stretch', disabled=cart_dirty)
                    if confirm_order:
                        if not agreed:
                            st.warning('Hãy kiểm tra và xác nhận báo giá trước khi chốt đơn.')
                        else:
                            queue_message('Tôi xác nhận chốt đơn theo báo giá vừa xem.', commerce_action={'action': 'confirm', 'signature': quote['signature']})
                            st.rerun()
        with orders_tab:
            st.button('Tải đơn của phiên này', on_click=queue_message, args=('Xem đơn hàng của tôi.',), kwargs={'commerce_action': {'action': 'orders'}})
            if state.get('last_order_id') or state.get('orders_opened') or (last_result and last_result.get('order')):
                try:
                    orders = engine.commerce_store().orders(st.session_state.conversation_id)
                    if not orders:
                        st.info('Phiên này chưa có đơn hàng.')
                    for order in orders:
                        with st.expander(order['id'], expanded=order['id'] == state.get('last_order_id')):
                            st.write(order_text(order))
                            st.dataframe([{'Sản phẩm': line['name'], 'Số lượng': line['quantity'], 'Đơn giá': money(line['unit_price_vnd'])} for line in order['lines']], hide_index=True, width='stretch')
                except ValueError as exc:
                    st.warning(str(exc))
        with policies_tab:
            if catalog:
                for faq in catalog.active_faqs():
                    with st.expander(faq["question"]):
                        st.text(faq["answer"])
                        st.caption(f"Nguồn {faq['id']} · {faq['version']} · {faq['source']}")
                        conditions = [
                            f"{key}: {value}"
                            for key, value in faq["conditions"].items()
                        ]
                        if conditions:
                            st.caption("Điều kiện: " + "; ".join(conditions))

with assistant_col:
    with st.container(border=True, key="shopping_assistant"):
        st.markdown('<div class="assistant-heading"><span class="assistant-mark" aria-hidden="true">m·</span>'
                    '<div><h2>Mộc AI</h2><p>Trợ lý trò chuyện của bạn</p></div></div>', unsafe_allow_html=True)
        if state["takeover"]:
            st.info("Nhân viên đang tiếp quản. Bạn có thể bổ sung thông tin tại đây.")
        history = st.container(height=420, border=False, key="assistant_history")
        st.caption("Tin nhắn")
        st.chat_input("Nhắn cho Mộc…", key="customer_message", max_chars=4000,
                      on_submit=chat_submitted, disabled="pending" in st.session_state)
        if "retry" in st.session_state:
            retry = st.session_state.retry
            st.button("Thử gửi lại", on_click=queue_message,
                      args=(retry["message"], retry["criteria_changes"], retry["confirm_removal"]),
                      kwargs={"commerce_action": retry.get("commerce_action"), "request_id": retry.get("request_id")})
        with st.popover("Tùy chọn hội thoại", icon=":material/more_horiz:", width="stretch"):
            if criteria:
                criteria_summary(criteria)
            if last_result and last_result.get("products"):
                st.button("Tìm lựa chọn rẻ hơn", key="cheaper", width="stretch",
                          on_click=queue_message, args=("Có loại rẻ hơn không?",))
            st.button("Yêu cầu nhân viên", icon=":material/support_agent:", width="stretch",
                      on_click=queue_message, args=("Tôi muốn gặp nhân viên hỗ trợ.",))
        st.caption(("Trả lời bằng AI" if engine.llm_enabled else "Bản thử offline") +
                   (" · Sản phẩm minh họa" if catalog and catalog.demo else " · Hãy kiểm tra thông tin quan trọng"))
        with history:
            if not st.session_state.messages:
                with st.chat_message("assistant", avatar=":material/auto_awesome:"):
                    st.markdown("**Chào bạn, mình là Mộc.**\n\nBạn có thể hỏi về sản phẩm, nhờ gợi ý quà hoặc trao đổi thêm để cùng chọn món phù hợp.")
                if "pending" not in st.session_state:
                    st.button("Gợi ý quà cho đồng nghiệp", key="starter_0", width="stretch",
                              on_click=queue_message, args=("Mình muốn mua bánh kẹo làm quà cho đồng nghiệp, bạn tư vấn giúp mình nhé?",))
                    st.button("Chính sách đổi trả thế nào?", key="starter_1", width="stretch",
                              on_click=queue_message, args=("Chính sách đổi trả thế nào?",))
            for item in st.session_state.messages:
                with st.chat_message("user", avatar=":material/person:"):
                    st.text(item["question"])
                with st.chat_message("assistant", avatar=":material/auto_awesome:"):
                    render_outcome(item["result"])
            if "pending" in st.session_state:
                with st.chat_message("user", avatar=":material/person:"):
                    st.text(st.session_state.pending["message"])
                with st.chat_message("assistant", avatar=":material/auto_awesome:"):
                    process_pending()
