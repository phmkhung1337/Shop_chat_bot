"""Presentation only: native Streamlit controls and escaped catalog content."""
from html import escape
from pathlib import Path

import streamlit as st

from chatbot.shop import ALLERGENS, checked_product, money

CSS_PATH = Path(__file__).with_name("shop.css")


def apply_theme():
    st.markdown(f"<style>{CSS_PATH.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


def brand():
    st.markdown('''<div class="brand"><div class="brand-mark">m<span>·</span></div>
        <div><strong>Mộc</strong><small>BÁNH &amp; KẸO</small></div></div>''', unsafe_allow_html=True)


def page_header(title, subtitle, badge="LOCAL DEMO"):
    st.markdown(f'''<div class="page-heading"><div>
        <h1>{escape(title)}</h1>{f'<p>{escape(subtitle)}</p>' if subtitle else ''}</div>
        <span class="mode-badge"><i></i>{escape(badge)}</span></div>''', unsafe_allow_html=True)


def section_title(title, caption=None):
    st.markdown(f'<h3 class="section-title">{escape(title)}</h3>' +
                (f'<p class="section-caption">{escape(caption)}</p>' if caption else ''), unsafe_allow_html=True)


def summary_item(label, value):
    st.markdown(f'<div class="summary-item"><span>{escape(label)}</span><strong>{escape(str(value))}</strong></div>', unsafe_allow_html=True)


def criteria_summary(criteria):
    scope = {"unit": "/ sản phẩm", "total": "tổng"}.get(criteria.get("budget_scope"), "chưa rõ phạm vi")
    budget = (("Tối đa " if criteria.get("budget_inclusive", True) else "Dưới ") + money(criteria["budget_vnd"]) + " · " + scope) if criteria.get("budget_vnd") else "Chưa xác định"
    summary_item("Loại sản phẩm", criteria.get("category") or "Chưa xác định")
    summary_item("Ngân sách", budget)
    summary_item("Số lượng", str(criteria["quantity"]) if criteria.get("quantity") else "Chưa xác định")
    summary_item("Quy cách", criteria.get("packaging") or "Chưa xác định")
    if criteria.get("brand"):
        summary_item("Thương hiệu bắt buộc", criteria["brand"])
    if criteria.get("flavor"):
        summary_item("Hương vị bắt buộc", criteria["flavor"])
    if criteria.get("excluded_flavors"):
        summary_item("Không chọn vị", ", ".join(criteria["excluded_flavors"]))
    restrictions = criteria.get("avoid_allergens", [])
    if restrictions:
        st.markdown('<div class="restriction-title">ĐIỀU KIỆN THÀNH PHẦN</div><div class="chips">' + ''.join(
            f'<span>Tránh {escape(ALLERGENS[x])}</span>' for x in restrictions) + '</div>', unsafe_allow_html=True)
        st.caption("Đối chiếu bằng chứng nhãn, không cam kết an toàn dị ứng.")
    else:
        st.caption("Chưa có điều kiện thành phần được xác nhận.")
    if criteria.get("avoid_ingredients"):
        summary_item("Thành phần khác cần tránh", ", ".join(criteria["avoid_ingredients"]))
    if criteria.get("preferences"):
        summary_item("Sở thích", ", ".join(criteria["preferences"]))


def render_product(product, catalog, *, criteria=None, direct=False, key="", on_ask=None):
    # Recheck the current snapshot on every rerun, including historical cards.
    current = catalog.product(product["id"])
    if current is None:
        st.warning("Sản phẩm này đã được gỡ khỏi catalog hiện tại.")
        return
    p = checked_product(current, catalog, criteria, direct)
    if not direct and not p["verified"]:
        st.warning(f"{p['name']}: dữ liệu hiện tại cần kiểm tra lại; chưa hiển thị như lựa chọn phù hợp.")
        return
    palette = {"Bánh": "oat", "Kẹo": "berry", "Chocolate": "cocoa"}.get(p["category"], "oat")
    availability = {"available": "Có hàng theo snapshot", "out_of_stock": "Đã xác nhận hết hàng", "unknown": "Chưa xác minh tồn kho"}[p["availability"]]
    if p["status"] == "discontinued":
        availability = "Đã ngừng kinh doanh"
    with st.container(border=True, key=f"product_{key}_{p['id']}"):
        st.markdown(f'''<div class="product-top"><div class="product-art {palette}" aria-hidden="true">
            <div class="package"><span>MỘC</span><b>{escape(p['category'].upper())}</b><i></i></div></div>
            <div class="product-info"><div class="product-category">{escape(p['brand'])}</div>
            <h4>{escape(p['name'])}</h4><p>{escape(p['packaging'])}</p>
            <div class="price">{escape(money(p['price_vnd'])) if p['price_vnd'] is not None else 'Cần xác minh giá'}<small> / {escape(p['unit'])}</small></div></div></div>
            <div class="product-bottom"><span class="stock-label {escape(p['availability'])}">{escape(availability)}</span><span>{escape(p['id'])}</span></div>''', unsafe_allow_html=True)
        if criteria and criteria.get("avoid_allergens") and p["verified"]:
            st.caption("Nhãn xác minh không chứa: " + ", ".join(ALLERGENS[a] for a in criteria["avoid_allergens"]) + ".")
        if p["not_suitable"] and direct:
            st.caption("Chưa phù hợp để gợi ý: " + "; ".join(p["not_suitable"]) + ".")
        detail_col, ask_col = st.columns(2, gap="small")
        with detail_col, st.popover("Chi tiết", width="stretch"):
            if on_ask is not None:
                quantity = st.number_input('Số lượng muốn mua', min_value=1, max_value=10000, value=1, key=f'buy_qty_{key}_{p["id"]}')
                wholesale = st.checkbox('Mua sỉ', key=f'buy_wholesale_{key}_{p["id"]}')
                st.button('Thêm vào giỏ', key=f'add_cart_{key}_{p["id"]}', width='stretch', on_click=on_ask,
                          args=('Thêm ' + p['name'] + ' vào giỏ.',), kwargs={'commerce_action': {'action': 'add', 'items': [dict(product_id=p['id'], quantity=int(quantity), wholesale=wholesale)]}})
                st.button('Xem giá sỉ theo số lượng', key=f'quote_wholesale_{key}_{p["id"]}', width='stretch', on_click=on_ask,
                          args=('Hỏi giá sỉ ' + p['id'] + '.',), kwargs={'commerce_action': {'action': 'wholesale', 'items': [dict(product_id=p['id'], quantity=int(quantity))]}})
                st.divider()
            st.text(p["description"])
            st.text("Nhãn: " + (p["ingredients"].get("label") or "Chưa có nhãn đã xác minh."))
            st.caption("Nguồn nhãn: " + (p["ingredients"].get("source") or "Chưa xác định"))
            st.caption("Ngày xác minh nhãn: " + (p["ingredients"].get("verified_at") or "Chưa xác định"))
            st.text("Bảo quản: " + (p.get("storage") or "Chưa có thông tin được duyệt."))
            st.text("Hạn dùng theo lô: " + (p.get("expiry") or "Chưa xác minh; cần xem nhãn lô thực tế."))
            st.caption(f"Nguồn {p['source']} · phiên bản {p['version']}")
            st.caption(f"Giá: {p.get('price_updated_at') or 'Chưa biết'} · Tồn kho: {p.get('stock_updated_at') or 'Chưa biết'}")
            for note in p["uncertainties"]:
                st.warning(note)
            if p.get("url"):
                st.link_button("Xem thông tin sản phẩm", p["url"], icon=":material/open_in_new:")
        if on_ask is not None:
            with ask_col:
                st.button("Hỏi trợ lý", key=f"ask_{key}_{p['id']}", width="stretch",
                          on_click=on_ask, args=("Cho tôi xem chi tiết " + p["id"],))


def render_outcome(result):
    labels = {"answered": "Đã đối chiếu", "clarify": "Cần làm rõ", "unresolved": "Cần kiểm tra thêm",
              "partially_answered": "Đã trả lời một phần", "out_of_scope": "Ngoài phạm vi tư vấn", "error": "Chưa thể xử lý"}
    if result["status"] != "answered":
        st.caption(labels.get(result["status"], result["status"]))
    st.markdown(result["reply"] or "Yêu cầu công khai đang được giữ để kiểm tra.")
    if result.get("source_ids"):
        with st.expander("Nguồn đối chiếu"):
            if result.get("source_ids"):
                st.caption(" · ".join(result["source_ids"]))
            if result.get("checked_at"):
                st.caption(result["checked_at"] + (" · dữ liệu demo" if result.get("demo") else ""))
