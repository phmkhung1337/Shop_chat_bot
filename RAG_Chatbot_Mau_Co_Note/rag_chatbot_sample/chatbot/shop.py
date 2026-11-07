"""Shop facts stay in Python. Only language interpretation is delegated to a model."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import unicodedata
from urllib.parse import urlparse
from uuid import uuid4
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tickets.support import SupportStore, dumps, utc_now
from orders.wholesale import RequestedItem

PROJECT_DIR = Path(__file__).resolve().parents[1]
ALLERGENS = {"milk": "sữa", "peanut": "đậu phộng", "tree_nuts": "hạt cây", "gluten": "gluten", "egg": "trứng", "soy": "đậu nành"}
ALLERGEN_TERMS = {"milk": ("sua", "lactose"), "peanut": ("dau phong", "lac"),
                  "tree_nuts": ("hat cay", "hanh nhan", "hat dieu"), "gluten": ("gluten", "lua mi"),
                  "egg": ("trung",), "soy": ("dau nanh",)}
INGREDIENT_NOTE = "Chỉ đối chiếu bằng chứng nhãn đã xác minh; đây không phải cam kết an toàn dị ứng."
CRITERIA_KEYS = {"category", "brand", "flavor", "excluded_flavors", "packaging", "budget_vnd", "budget_scope",
                 "budget_inclusive", "quantity", "avoid_allergens", "avoid_ingredients", "preferences", "requested_id"}
INTENTS = {"recommendation", "specific_product", "product_fact", "faq", "conversation", "staff", "out_of_scope", "clarify", "wholesale", "cart_add", "cart_view", "checkout", "payment", "order_status"}

ShortText = Annotated[str, Field(min_length=1, max_length=500)]
PositiveMoney = Annotated[int, Field(ge=1, le=10**12)]


class CriteriaPatch(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)
    category: ShortText | None = None
    brand: ShortText | None = None
    flavor: ShortText | None = None
    packaging: ShortText | None = None
    requested_id: ShortText | None = None
    budget_vnd: PositiveMoney | None = None
    quantity: Annotated[int, Field(ge=1, le=10000)] | None = None
    budget_scope: Literal["unit", "total"] | None = None
    budget_inclusive: bool = True
    avoid_allergens: list[Literal["milk", "peanut", "tree_nuts", "gluten", "egg", "soy"]] = Field(default_factory=list, max_length=6)
    avoid_ingredients: list[ShortText] = Field(default_factory=list, max_length=30)
    excluded_flavors: list[ShortText] = Field(default_factory=list, max_length=100)
    preferences: list[ShortText] = Field(default_factory=list, max_length=100)


class Extraction(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    intent: Literal["recommendation", "specific_product", "product_fact", "faq", "conversation", "staff", "out_of_scope", "clarify", "wholesale", "cart_add", "cart_view", "checkout", "payment", "order_status"]
    changes: CriteriaPatch = Field(default_factory=CriteriaPatch)
    evidence: dict[str, str] = Field(default_factory=dict)
    faq: bool = False
    product: bool = False
    cheaper: bool = False
    question: Annotated[str, Field(max_length=500)] | None = None
    commerce_items: list[RequestedItem] = Field(default_factory=list, max_length=200)


def normalize(text):
    return " ".join("".join(c for c in unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
                             if unicodedata.category(c) != "Mn").split())


def contains(text, term):
    return bool(re.search(r"(?<!\w)" + re.escape(normalize(term)) + r"(?!\w)", normalize(text)))


def ingredient_topic(text):
    return bool(re.search(r"\b(?:thanh phan|di ung|co (?:the )?chua|khong chua|it (?:duong|ngot))\b", normalize(text)))


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Thời điểm dữ liệu phải là chuỗi ISO có múi giờ.")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Thời điểm dữ liệu không hợp lệ.") from None
    if result.tzinfo is None:
        raise ValueError("Thời điểm dữ liệu phải có múi giờ.")
    return result.astimezone(timezone.utc)


def integer(value, *, minimum=0):
    return type(value) is int and minimum <= value <= 10**12


def strings(value):
    return isinstance(value, list) and len(value) <= 100 and all(isinstance(x, str) and 0 < len(x) <= 500 for x in value)


def valid_url(value):
    if value is None:
        return True
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username and not parsed.password


def money(value):
    return f"{value:,}".replace(",", ".") + " ₫"


@dataclass
class Catalog:
    products: list
    faqs: list
    version: str
    demo: bool
    as_of: datetime
    max_age_hours: dict
    clock: object = None
    database_path: str | None = None

    @property
    def now(self):
        # shortcut: demo clock is frozen to the labelled fixture date; real catalogs always use UTC now.
        return self.clock() if self.clock else (self.as_of if self.demo else datetime.now(timezone.utc))

    def fresh(self, date, fact):
        if date is None:
            return False
        age = self.now - timestamp(date)
        return timedelta(minutes=-5) <= age <= timedelta(hours=self.max_age_hours[fact])

    def product(self, product_id):
        return next((p for p in self.products if p["id"] == product_id), None)

    def active_faqs(self):
        return [
            faq
            for faq in self.faqs
            if faq["approved"]
            and timestamp(faq["effective_from"]) <= self.now
            and (faq["effective_to"] is None or self.now <= timestamp(faq["effective_to"]))
        ]

    def signature(self):
        # Mutable price/stock facts are deliberately absent from vector documents.
        return hashlib.sha256(dumps(self.records()).encode()).hexdigest()

    def records(self):
        result = []
        for p in self.products:
            if p["status"] == "active":
                result.append({"id": p["id"], "record_type": "product", "product_id": p["id"],
                               "source": f"sql:products/{p['id']}" if self.database_path else p["source"], "version": p["version"],
                               **({"sql_table": "products", "sql_id": p["id"], "source_reference": p["source"]} if self.database_path else {}),
                               "content": f"{p['name']}. {p['description']} Quy cách: {p['packaging']}."})
        for faq in self.active_faqs():
            record = {
                "id": faq["id"],
                "record_type": "faq",
                "faq_id": faq["id"],
                "source": faq["source"],
                "version": faq["version"],
                "content": (
                    f"{faq['question']} {faq['answer']} "
                    f"Điều kiện: {dumps(faq['conditions'])}"
                ),
            }
            if self.database_path:
                record.update(
                    source=f"sql:faqs/{faq['id']}",
                    sql_table="faqs",
                    sql_id=faq["id"],
                    source_reference=faq["source"],
                )
            result.append(record)
        return result


def load_catalog(data_dir=None, *, mode="demo", clock=None, catalog_db=None):
    if mode not in {"demo", "connected"}:
        raise ValueError("SHOP_MODE chỉ nhận demo hoặc connected.")
    if data_dir is not None and catalog_db is not None:
        raise ValueError("Chọn một nguồn catalog: SQL hoặc JSON đầu vào, không dùng cả hai.")
    if data_dir is None:
        backend = os.getenv("SHOP_CATALOG_BACKEND", "sqlite").strip().lower()
        if catalog_db is None and backend == "sqlserver":
            from catalog.catalog_sqlserver import read_catalog
            return read_catalog(mode=mode, clock=clock)
        if catalog_db is None and backend != "sqlite":
            raise ValueError("SHOP_CATALOG_BACKEND chỉ nhận sqlserver hoặc sqlite.")
        from catalog.catalog_sql import default_catalog_path, read_catalog
        return read_catalog(catalog_db or default_catalog_path(mode), mode=mode, clock=clock)
    # JSON remains an import/fixture format; the running app reads SQL by default.
    folder = Path(data_dir)
    try:
        raw = json.loads((folder / "products.json").read_text(encoding="utf-8"))
        faq_data = json.loads((folder / "faq.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("Không đọc được catalog/FAQ JSON hợp lệ; chưa dùng snapshot cũ.") from None
    return catalog_from_data(raw, faq_data, mode=mode, clock=clock)


def catalog_from_data(raw, faq_data, *, mode="demo", clock=None):
    if mode not in {"demo", "connected"}:
        raise ValueError("Chế độ catalog không hợp lệ.")
    if not isinstance(raw, dict) or not isinstance(faq_data, dict):
        raise ValueError("Catalog/FAQ phải là đối tượng JSON.")
    demo = mode == "demo"
    if raw.get("demo") is not demo or faq_data.get("demo") is not demo:
        raise ValueError("Dữ liệu demo và dữ liệu thật phải tách riêng.")
    if raw.get("approved") is not True or faq_data.get("approved") is not True:
        raise ValueError("Chưa có dữ liệu được duyệt. Cần catalog/FAQ và quy định độ mới trước khi dùng connected.")
    for root in (raw, faq_data):
        if not isinstance(root.get("version"), str) or not root["version"]:
            raise ValueError("Thiếu phiên bản snapshot.")
    as_of = timestamp(raw.get("as_of"))
    ages = raw.get("max_age_hours")
    if not isinstance(ages, dict) or any(not integer(ages.get(k), minimum=1) for k in ("price", "stock", "ingredients")):
        raise ValueError("Cần giới hạn độ mới cho giá, tồn kho và nhãn thành phần.")
    products, faqs = raw.get("products"), faq_data.get("faqs")
    if not isinstance(products, list) or not products or not isinstance(faqs, list):
        raise ValueError("Catalog cần ít nhất một sản phẩm và FAQ cần một danh sách.")
    ids = set()
    for p in products:
        if not isinstance(p, dict):
            raise ValueError("Sản phẩm phải là đối tượng JSON.")
        for key in ("id", "name", "category", "brand", "flavor", "description", "packaging", "unit", "source", "version"):
            if not isinstance(p.get(key), str) or not p[key].strip():
                raise ValueError(f"Sản phẩm thiếu trường {key}.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", p["id"]) or p["id"] in ids:
            raise ValueError("SKU không hợp lệ hoặc bị trùng.")
        ids.add(p["id"])
        if not strings(p.get("aliases")) or p.get("status") not in {"active", "discontinued"}:
            raise ValueError("Tên thay thế hoặc trạng thái sản phẩm không hợp lệ.")
        if p.get("price_vnd") is not None and not integer(p["price_vnd"]):
            raise ValueError("Giá phải là số nguyên VND không âm, hoặc null nếu chưa biết.")
        if p.get("stock_qty") is not None and not integer(p["stock_qty"]):
            raise ValueError("Số lượng tồn phải là số nguyên không âm, hoặc null.")
        if p.get("availability") not in {"available", "out_of_stock", "unknown"}:
            raise ValueError("Trạng thái tồn kho không hợp lệ.")
        if ((p["availability"] == "available" and p.get("stock_qty") == 0)
                or (p["availability"] == "out_of_stock" and p.get("stock_qty") not in {0, None})):
            raise ValueError("Số lượng và trạng thái tồn kho mâu thuẫn.")
        for field in ("price_updated_at", "stock_updated_at"):
            if p.get(field) is not None:
                timestamp(p[field])
        if not valid_url(p.get("url")):
            raise ValueError("URL sản phẩm phải dùng http/https và không chứa thông tin đăng nhập.")
        ingredient = p.get("ingredients")
        if not isinstance(ingredient, dict) or not isinstance(ingredient.get("allergens"), dict):
            raise ValueError("Thiếu cấu trúc bằng chứng thành phần.")
        if any(k not in ALLERGENS or v not in {"absent", "present", "may_contain", "unknown"}
               for k, v in ingredient["allergens"].items()):
            raise ValueError("Trạng thái chất gây dị ứng không hợp lệ.")
        for field in ("label", "source"):
            if ingredient.get(field) is not None and not isinstance(ingredient[field], str):
                raise ValueError("Nhãn thành phần không hợp lệ.")
        if ingredient.get("verified_at") is not None:
            timestamp(ingredient["verified_at"])
        if not strings(ingredient.get("verified_absent", [])):
            raise ValueError("Danh sách thành phần xác minh không có phải là danh sách văn bản.")
    faq_ids = set()
    normalized_faqs = []
    for faq in faqs:
        if not isinstance(faq, dict):
            raise ValueError("FAQ phải là đối tượng JSON.")
        for key in ("id", "question", "source", "version", "owner"):
            if not isinstance(faq.get(key), str) or not faq[key].strip():
                raise ValueError(f"FAQ thiếu trường {key}.")

        answer = faq.get("answer")
        if strings(answer) and all(line.strip() for line in answer):
            answer = " ".join(line.strip() for line in answer)
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("Câu trả lời FAQ cần là văn bản hoặc danh sách dòng văn bản.")

        if faq["id"] in faq_ids or faq["id"] in ids:
            raise ValueError("ID nguồn bị trùng.")
        faq_ids.add(faq["id"])
        if type(faq.get("approved")) is not bool or not strings(faq.get("aliases")):
            raise ValueError("FAQ có phê duyệt hoặc alias không hợp lệ.")

        conditions = faq.get("conditions")
        if not isinstance(conditions, dict) or set(conditions) - {"region", "min_order_vnd"}:
            raise ValueError("Điều kiện FAQ chưa được hỗ trợ; cần xác minh thay vì bỏ qua.")
        if "region" in conditions and (not isinstance(conditions["region"], str) or not conditions["region"]):
            raise ValueError("Vùng áp dụng FAQ không hợp lệ.")
        if "min_order_vnd" in conditions and not integer(conditions["min_order_vnd"]):
            raise ValueError("Giá trị đơn tối thiểu của FAQ không hợp lệ.")
        start = timestamp(faq.get("effective_from"))
        end = faq.get("effective_to")
        if end is not None and timestamp(end) < start:
            raise ValueError("Khoảng hiệu lực FAQ không hợp lệ.")
        normalized_faqs.append({**faq, "answer": answer})

    version = f"{raw['version']} / {faq_data['version']}"
    return Catalog(products, normalized_faqs, version, demo, as_of, ages, clock)


def import_catalog(source_dir, destination=None, *, mode="connected"):
    """Import validated JSON into an atomic SQL snapshot, preserving the old database as backup."""
    if destination is None and os.getenv("SHOP_CATALOG_BACKEND", "sqlite").strip().lower() == "sqlserver":
        from catalog.catalog_sqlserver import import_json_catalog
        return import_json_catalog(source_dir, mode=mode)
    from catalog.catalog_sql import default_catalog_path, import_json_catalog
    return import_json_catalog(source_dir, destination or default_catalog_path(mode), mode=mode)


def validate_criteria(patch):
    try:
        return CriteriaPatch.model_validate(patch).model_dump(exclude_unset=True)
    except ValidationError:
        raise ValueError("Tiêu chí không hợp lệ: kiểm tra số tiền, số lượng, phạm vi và thành phần.") from None


def eligibility(p, criteria, catalog):
    """Return hard failures and uncertain facts separately; neither is a confirmed match."""
    failures, unknown = [], []
    if p["status"] != "active":
        failures.append("ngừng kinh doanh")
    for key in ("category", "brand", "flavor", "packaging"):
        if criteria.get(key) and not contains(p[key], criteria[key]):
            failures.append(f"không khớp {key}")
    if any(contains(p["flavor"], flavor) for flavor in criteria.get("excluded_flavors", [])):
        failures.append("vị đang loại trừ")
    if not catalog.fresh(p.get("stock_updated_at"), "stock"):
        unknown.append("tồn kho cần cập nhật")
    elif p["availability"] == "out_of_stock" or p["stock_qty"] == 0:
        failures.append("đã xác nhận hết hàng")
    elif p["availability"] == "unknown":
        unknown.append("tồn kho chưa xác minh/cần cập nhật")
    elif p["stock_qty"] is None and criteria.get("quantity"):
        unknown.append("chưa biết số lượng tồn")
    elif p["stock_qty"] is not None and criteria.get("quantity", 1) > p["stock_qty"]:
        failures.append("không đủ số lượng")
    if p["price_vnd"] is None or not catalog.fresh(p.get("price_updated_at"), "price"):
        unknown.append("giá chưa xác minh/cần cập nhật")
    elif criteria.get("budget_vnd"):
        cost = p["price_vnd"] * (criteria.get("quantity", 1) if criteria.get("budget_scope") == "total" else 1)
        limit = criteria["budget_vnd"]
        if cost > limit or (cost == limit and not criteria.get("budget_inclusive", True)):
            failures.append("vượt ngân sách")
    evidence = p["ingredients"]
    for allergen in criteria.get("avoid_allergens", []):
        status = evidence["allergens"].get(allergen, "unknown")
        if status in {"present", "may_contain"}:
            failures.append(f"nhãn có/có thể chứa {ALLERGENS[allergen]}")
        elif (status != "absent" or not evidence.get("label") or not evidence.get("source")
              or not catalog.fresh(evidence.get("verified_at"), "ingredients")):
            unknown.append(f"chưa có nhãn xác minh không chứa {ALLERGENS[allergen]}")
        if status == "absent" and any(re.search(r"(?:co the chua|may contain|co chua)\s+[^.;]{0,40}(?<!\w)" + re.escape(term) + r"(?!\w)", normalize(evidence.get("label") or "")) for term in ALLERGEN_TERMS[allergen]):
            unknown.append(f"bằng chứng {ALLERGENS[allergen]} mâu thuẫn với cảnh báo nhãn")
    for ingredient in criteria.get("avoid_ingredients", []):
        if (normalize(ingredient) not in {normalize(x) for x in evidence.get("verified_absent", [])}
                or not evidence.get("label") or not evidence.get("source")
                or not catalog.fresh(evidence.get("verified_at"), "ingredients")):
            unknown.append(f"chưa có bằng chứng xác minh không chứa {ingredient}")
    return failures, unknown


def checked_product(p, catalog, criteria=None, direct=False):
    failures, unknown = eligibility(p, criteria or {}, catalog)
    price_fresh = p["price_vnd"] is not None and catalog.fresh(p.get("price_updated_at"), "price")
    stock_fresh = catalog.fresh(p.get("stock_updated_at"), "stock")
    availability = p["availability"] if stock_fresh else "unknown"
    return {**deepcopy(p), "price_vnd": p["price_vnd"] if price_fresh else None,
            "availability": availability, "verified": not failures and not unknown,
            "uncertainties": unknown, "not_suitable": failures, "direct": direct,
            "checked_at": catalog.now.isoformat(), "snapshot_version": catalog.version}


def exact_products(question, catalog):
    # Long aliases win so a parent product cannot silently select a neighboring variant.
    matches = []
    for p in catalog.products:
        names = [p["id"], p["name"], *p["aliases"]]
        score = max((len(normalize(n)) for n in names if contains(question, n)), default=0)
        if score:
            matches.append((score, p))
    best = max((score for score, _ in matches), default=0)
    return [p for score, p in matches if score == best]


def ingredient_changes(normalized_question, criteria):
    avoid, other_ingredients = [], []
    pattern = r"(?:khong chua|khong co|khong dung|di ung(?: voi)?|tranh|kieng)\s+([^,;.?!]+)"
    for match in re.finditer(pattern, normalized_question):
        for fragment in re.split(r"\s+(?:va|hoac)\s+", match[1]):
            fragment = re.split(r"\s+(?:nhung|voi ngan sach|duoi)\s+", fragment)[0].strip()
            known = [
                allergen for allergen, terms in ALLERGEN_TERMS.items()
                if any(contains(fragment, term) for term in terms)
            ]
            avoid.extend(known)
            if fragment and not known and fragment not in {"hang", "san pham", "loai nao"}:
                other_ingredients.append(fragment)
    patch = {}
    if avoid:
        patch["avoid_allergens"] = sorted(set(criteria.get("avoid_allergens", []) + avoid))
    if other_ingredients:
        patch["avoid_ingredients"] = sorted(set(criteria.get("avoid_ingredients", []) + other_ingredients))
    return patch


def parse_demo(question, criteria, catalog):
    """Small explicit Vietnamese grammar for offline fixtures, never presented as an LLM."""
    q = normalize(question)
    patch, notices = {}, []
    matched = exact_products(question, catalog)
    if len(matched) == 1:
        patch["requested_id"] = matched[0]["id"]
    sku = re.search(r"\b[A-Za-z]{2,10}-\d{2,10}\b", question)
    if sku and not matched:
        patch["requested_id"] = sku[0].upper()
    if len(matched) > 1:
        return {"intent": "clarify", "changes": {}, "question": "Bạn muốn SKU/biến thể nào? " + ", ".join(p["id"] for p in matched)}
    for key in ("category", "brand", "flavor"):
        values = sorted({p[key] for p in catalog.products}, key=len, reverse=True)
        for value in values:
            if contains(q, value) and not (key == "flavor" and any(contains(q, term) for terms in ALLERGEN_TERMS.values() for term in terms if normalize(value) == term)):
                if key == "flavor" and re.search(r"(?:khong|bo|tranh|tru)(?: lay| chon| an| vi)*\s+(?:vi\s+)?" + re.escape(normalize(value)), q):
                    patch["excluded_flavors"] = sorted(set(criteria.get("excluded_flavors", []) + [value]))
                elif key != "flavor" or contains(q, "vị " + value) or contains(q, "loại " + value):
                    patch[key] = value
                break
    for packaging in ("hộp", "gói", "túi", "hũ"):
        if re.search(r"(?:dang|loai|quy cach|dong|moi|mot)\s+" + normalize(packaging), q):
            patch["packaging"] = packaging
    quantity = re.search(r"(?<![\w.,])(-?\d+)\s*(hop|goi|tui|hu|cai|san pham)(?!\w)", q)
    if quantity:
        if not 1 <= int(quantity[1]) <= 10000:
            return {"intent": "clarify", "changes": {}, "question": "Bạn cần số lượng bao nhiêu? Phiên tư vấn hỗ trợ từ 1 đến 10.000 đơn vị."}
        patch["quantity"] = int(quantity[1])
        if quantity[2] in {"hop", "goi", "tui", "hu"}:
            patch["packaging"] = {"hop": "hộp", "goi": "gói", "tui": "túi", "hu": "hũ"}[quantity[2]]
    amount = re.search(r"(?<!\w)(\d+(?:[.,]\d+)*)\s*(k|nghin|ngan|trieu|tr|vnd|dong|d|₫)(?!\w)", q)
    bare_amount = re.search(r"(?:ngan sach|duoi|toi da|khong qua|tong)\s+(\d+(?:[.,]\d+)*)(?!\w)", q) if not amount else None
    if (amount or bare_amount) and (quantity or re.search(r"duoi|toi da|khong qua|ngan sach|tam|khoang|trong|gia|re hon|tong", q)):
        number, unit = amount.groups() if amount else (bare_amount[1], "vnd")
        if unit in {"k", "nghin", "ngan", "trieu", "tr"} and not re.fullmatch(r"\d+(?:[.,]\d{1,2})?", number):
            return {"intent": "clarify", "changes": {}, "question": "Cách viết số tiền chưa rõ. Bạn vui lòng ghi bằng số nguyên VND, ví dụ 100000đ."}
        if unit in {"vnd", "dong", "d", "₫"} and ("." in number or "," in number) and not re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", number):
            return {"intent": "clarify", "changes": {}, "question": "Giá VND cần là số nguyên. Bạn có thể ghi rõ ngân sách, ví dụ 100000đ không?"}
        if unit in {"k", "nghin", "ngan", "trieu", "tr"}:
            # Money arithmetic is integral, including decimal millions/thousands.
            from decimal import Decimal
            patch["budget_vnd"] = int(Decimal(number.replace(",", ".")) * (1000000 if unit in {"trieu", "tr"} else 1000))
        else:
            patch["budget_vnd"] = int(number.replace(".", "").replace(",", ""))
        patch["budget_inclusive"] = not bool(re.search(r"duoi|nho hon|re hon", q))
        patch["budget_scope"] = None
    if re.search(r"moi\s+(?:hop|goi|tui|mon|san pham)|mot\s+(?:hop|goi|tui)|/\s*(?:hop|goi|tui)|tung mon|moi mon", q):
        patch["budget_scope"] = "unit"
    elif re.search(r"tong|tat ca|ca\s+\d|cho\s+\d|toan bo", q):
        patch["budget_scope"] = "total"
    if re.fullmatch(r"\s*(?:ca|tong|cho ca)\s*\d*\s*(?:hop|goi|tui)?\s*", q):
        patch["budget_scope"] = "total"
    removing = bool(re.search(r"bo (?:dieu kien|han che|rang buoc)|khong can (?:tranh|kieng)|cho phep (?:co|chua)", q))
    if removing:
        return {"intent": "clarify", "changes": {}, "question": "Để bỏ hạn chế thành phần, hãy dùng form tiêu chí và xác nhận rõ điều kiện cần bỏ."}
    patch.update(ingredient_changes(q, criteria))
    faq_signals = ("giao hang", "ship", "doi tra", "doi hang", "hoan tien", "thanh toan", "bao quan", "han su dung", "khuyen mai", "giam gia", "mien phi", "chinh sach", "mo cua")
    faq = any(contains(q, term) for term in faq_signals)
    product_intent = bool(matched or patch or any(contains(q, x) for x in ("banh", "keo", "chocolate", "san pham", "goi y", "tu van", "re hon", "tim", "mua")))
    if faq and not matched and not patch and not any(contains(q, x) for x in ("tìm", "gợi ý", "mua", "tư vấn")):
        product_intent = False
    if any(contains(q, x) for x in ("nhan vien", "nguoi that", "ho tro truc tiep", "gap chu shop")):
        intent = "staff"
    elif re.search(r"combo|gio hang|dat hang|chot don|thanh toan giup", q):
        intent = "out_of_scope"
        notices.append("Bạn có thể quản lý giỏ và xác nhận đơn trong trang cửa hàng.")
    elif matched or patch.get("requested_id"):
        fact = ingredient_topic(q) or any(contains(q, x) for x in ("gia", "bao nhieu", "con hang", "con khong", "chi tiet"))
        intent = "product_fact" if fact else "specific_product"
    elif product_intent or (patch and criteria):
        intent = "recommendation"
    elif faq:
        intent = "faq"
    elif q in {"xin chao", "chao", "hello", "hi", "cam on", "ok"}:
        intent = "clarify"
        notices.append("Mình giúp bạn tìm bánh kẹo, xem thông tin sản phẩm hoặc hỏi chính sách. Bạn đang cần loại nào?")
    else:
        intent = "out_of_scope"
    return {"intent": intent, "changes": patch, "faq": faq, "product": product_intent,
            "cheaper": "re hon" in q and amount is None, "question": notices[0] if notices else None}


def validate_extraction(value, question, *, explicit_changes=None):
    try:
        parsed = Extraction.model_validate(value)
    except ValidationError:
        raise ValueError("Không đọc được ý định hợp lệ. Bạn có thể nêu rõ loại hàng, ngân sách và số lượng không?") from None
    patch = parsed.changes.model_dump(exclude_unset=True)
    evidence = parsed.evidence
    for field in patch:
        quote = evidence.get(field)
        if not isinstance(quote, str) or not quote.strip() or normalize(quote) not in normalize(question):
            # The existing grammar can independently prove explicit numeric/allergen constraints.
            if explicit_changes is None or field not in explicit_changes or patch[field] != explicit_changes[field]:
                raise ValueError("Chưa xác minh được tiêu chí. Bạn vui lòng nói rõ điều kiện muốn thay đổi.")
    return {**parsed.model_dump(), "changes": patch}


class ShopEngine:
    def __init__(self, *, mode="demo", data_dir=None, store=None, rag=None, clock=None,
                 queue_monitored=False, public_hold=False, catalog_db=None, llm_enabled=None,
                 commerce=None, load_on_start=True):
        if mode not in {"demo", "connected"}:
            raise ValueError("Chế độ không hợp lệ.")
        self.mode, self.data_dir, self.clock = mode, data_dir, clock
        self.catalog_db = catalog_db
        self.store = store or SupportStore(PROJECT_DIR / ("shop_demo.sqlite3" if mode == "demo" else "shop_support.sqlite3"))
        self.rag = rag
        self.commerce = commerce
        self.llm_enabled = mode == "connected" if llm_enabled is None else llm_enabled
        if mode == "connected" and not self.llm_enabled:
            raise ValueError("Chế độ connected cần bật LLM.")
        self.queue_monitored, self.public_hold = queue_monitored, public_hold
        self.catalog = None
        self.data_error = None
        if load_on_start:
            self.refresh()

    def refresh(self):
        try:
            updated = load_catalog(self.data_dir, mode=self.mode, clock=self.clock, catalog_db=self.catalog_db)
        except ValueError as exc:
            self.data_error = str(exc)
            raise
        self.catalog = updated
        self.data_error = None
        return updated

    def state(self, conversation_id):
        return self.store.state(conversation_id)

    def commerce_store(self):
        if self.commerce is None:
            if os.getenv('SHOP_CATALOG_BACKEND', 'sqlite').strip().lower() != 'sqlserver':
                raise ValueError('Giá sỉ và đơn hàng cần nguồn SQL Server; không ghi đơn khi đang dùng catalog offline.')
            from orders.commerce import CommerceStore
            self.commerce = CommerceStore()
        return self.commerce

    def quote_cart(self, conversation_id):
        from orders.wholesale import quote_items
        state = self.state(conversation_id)
        return quote_items(self.refresh(), state.get('cart', []), self.commerce_store().rules(), criteria=state['criteria'])

    def _faq(self, question, catalog, *, covered_topics=()):
        hits = [
            faq
            for faq in catalog.active_faqs()
            if any(contains(question, alias) for alias in [faq["question"], *faq["aliases"]])
        ]
        if not hits:
            return [], "Chưa có chính sách đã duyệt khớp với câu hỏi này.", "missing_policy"
        groups = {}
        for faq in hits:
            groups.setdefault(normalize(faq["question"]), []).append(faq)
        answers = []
        for group in groups.values():
            conditions_seen = {}
            for faq in group:
                conditions_key = dumps(faq["conditions"])
                previous_answer = conditions_seen.get(conditions_key)
                if previous_answer is not None and previous_answer != faq["answer"]:
                    return (
                        [],
                        "Chính sách đang có thông tin mâu thuẫn; cần nhân viên kiểm tra.",
                        "conflicting_policy",
                    )
                conditions_seen[conditions_key] = faq["answer"]
            for i, first in enumerate(group):
                for second in group[i + 1:]:
                    first_region = first["conditions"].get("region")
                    second_region = second["conditions"].get("region")
                    overlaps = (
                        not first_region
                        or not second_region
                        or normalize(first_region) == normalize(second_region)
                    )
                    if overlaps and first["answer"] != second["answer"]:
                        return (
                            [],
                            "Chính sách có điều kiện chồng lấn nhưng câu trả lời khác nhau; cần xác minh.",
                            "conflicting_policy",
                        )
            answers.extend(group)

        answer_titles = [" ".join([faq["question"], *faq["aliases"]]) for faq in answers]
        missing = []
        for terms in self.FAQ_TOPICS:
            if terms[0] in covered_topics or not any(contains(question, term) for term in terms):
                continue
            if not any(contains(title, term) for title in answer_titles for term in terms):
                missing.append(terms[0])
        if missing:
            return answers, "Chưa có chính sách đã duyệt cho: " + ", ".join(missing) + ".", "missing_policy"
        return answers, None, None

    FAQ_TOPICS = (
        ("giao hàng", "ship", "miễn phí"),
        ("đổi trả", "đổi hàng", "hoàn tiền"),
        ("thanh toán",),
        ("bảo quản",),
        ("hạn sử dụng",),
        ("khuyến mại", "khuyến mãi", "giảm giá"),
        ("mở cửa", "giờ hỗ trợ"),
    )

    def _append_faq(self, question, catalog, result, *, covered_topics=()):
        answers, error, reason = self._faq(question, catalog, covered_topics=covered_topics)
        if answers:
            replies = []
            for faq in answers:
                reply = faq["answer"]
                conditions = [
                    f"khu vực {value}" if key == "region" else f"giá trị đơn từ {money(value)}"
                    for key, value in faq["conditions"].items()
                ]
                if conditions:
                    reply += "\nĐiều kiện áp dụng: " + "; ".join(conditions)
                replies.append(reply)
                result["source_ids"].append(f"{faq['id']}@{faq['version']}")
            result["parts"].append({
                "kind": "faq",
                "status": "answered",
                "reply": "\n\n".join(replies),
            })
            result["skip_generation"] = True
        if error:
            result["parts"].append({"kind": "faq", "status": "unresolved", "reply": error})
            result.update(reason=reason, ticket_needed=True)

    def _products(self, question, state, catalog, interpretation):
        criteria = state["criteria"]
        direct = interpretation["intent"] in {"specific_product", "product_fact"}
        requested = catalog.product(criteria.get("requested_id")) if direct else None
        if direct and requested is None:
            return [], "Không tìm thấy SKU/biến thể được hỏi trong catalog.", "missing_product", []
        if requested and interpretation["intent"] == "product_fact":
            product = checked_product(requested, catalog, criteria, direct=True)
            text = f"{requested['name']} · {requested['id']}\n\n"
            text += f"Giá {money(product['price_vnd'])}/{requested['unit']}. " if product["price_vnd"] is not None else "Giá đang cần xác minh. "
            text += {"available": "Có hàng theo snapshot; đây không phải tồn kho trực tiếp.", "out_of_stock": "Đã xác nhận hết hàng theo snapshot.", "unknown": "Tồn kho chưa xác minh."}[product["availability"]]
            if requested["status"] == "discontinued":
                text += " Sản phẩm đã ngừng kinh doanh."
            if ingredient_topic(question) or criteria.get("avoid_allergens") or criteria.get("avoid_ingredients"):
                evidence = requested["ingredients"]
                if evidence.get("label") and catalog.fresh(evidence.get("verified_at"), "ingredients"):
                    text += "\n\nNhãn thành phần: " + evidence["label"]
                else:
                    product["uncertainties"].append("nhãn thành phần cần xác minh")
                    text += "\n\nChưa có nhãn thành phần được xác minh còn hiệu lực."
                if criteria.get("avoid_allergens"):
                    text += " " + INGREDIENT_NOTE
            return [product], text, "unverified_facts" if product["uncertainties"] else None, [requested["id"]]
        candidates, uncertain = [], []
        considered = [p["id"] for p in catalog.products]
        requested_note = ""
        if requested:
            failed, unknown = eligibility(requested, criteria, catalog)
            if not failed and not unknown:
                candidates = [requested]
            else:
                requested_note = f"{requested['name']} ({requested['id']}): " + "; ".join(failed + unknown) + ".\n\n"
        if not candidates:
            for p in catalog.products:
                failed, unknown = eligibility(p, criteria, catalog)
                if not failed and not unknown:
                    candidates.append(p)
                elif not failed and unknown:
                    uncertain.extend(unknown)
        def rank(p):
            terms = [normalize(t) for t in re.findall(r"\w+", question) if len(t) >= 3]
            relevance = sum(term in normalize(p["name"] + " " + p["description"]) for term in terms)
            return (-relevance, p["price_vnd"], p["id"])
        candidates.sort(key=rank)
        if self.llm_enabled and candidates:
            ranked = self.rag.retrieve(catalog, question, "product", [p["id"] for p in candidates])
            positions = {r["product_id"]: i for i, r in enumerate(ranked)}
            candidates.sort(key=lambda p: (positions.get(p["id"], 999), rank(p)))
        products = [checked_product(p, catalog, criteria) for p in candidates[:3]]
        if not products:
            if uncertain:
                return [], requested_note + "Chưa xác minh được lựa chọn phù hợp: " + "; ".join(sorted(set(uncertain))) + ".", "unverified_facts", considered
            return [], requested_note + "Chưa có sản phẩm đáp ứng đầy đủ tiêu chí hiện tại. Bạn có thể đổi ngân sách hoặc điều kiện; mình sẽ không tự nới điều kiện.", "no_match", considered
        text = requested_note + ("Một số lựa chọn thay thế, giữ nguyên điều kiện của bạn:" if requested_note else f"Mình tìm thấy {len(products)} lựa chọn phù hợp với tiêu chí đã xác nhận.")
        if requested_note:
            text += " Quy cách/vị/nhãn của từng lựa chọn có thể khác sản phẩm bạn hỏi; xem chi tiết bên dưới."
        if criteria.get("avoid_allergens"):
            text += " " + INGREDIENT_NOTE
        return products, text, None, considered

    def _process(self, question, state, catalog, changes, confirm_removal, *, commerce_action=None, conversation_id=None):
        result = {"status": "answered", "reply": "", "product_ids": [], "source_ids": [],
                  "products": [], "parts": [], "reason": None, "ticket_needed": False,
                  "checked_product_ids": [], "model_calls": 0}
        if question == "/reset":
            state.update(criteria={}, history=[], last_products=[])
            result["reply"] = "Đã bắt đầu lại: tiêu chí và lịch sử phiên đã được xóa. Ticket và audit vẫn được giữ."
            return result
        if state["takeover"]:
            result.update(status="unresolved", reply="Nhân viên đang tiếp quản hội thoại. Nội dung mới được lưu trong hàng đợi; bot sẽ tiếp tục khi nhân viên bàn giao lại.", reason="staff_takeover", ticket_needed=True)
            return result
        if self.llm_enabled and self.rag is None:
            from chatbot.shop_rag import ShopRAG
            self.rag = ShopRAG()
        from orders.commerce_chat import parse_commerce, handle_commerce
        action = commerce_action if commerce_action is not None else (parse_commerce(question, state, catalog) if changes is None else None)
        if action is not None:
            if action['action'] in {'clarify', 'clarify_quantity'}:
                return handle_commerce(self, action, state, catalog, conversation_id=conversation_id)
            state['criteria'].update(ingredient_changes(normalize(question), state['criteria']))
            return handle_commerce(self, action, state, catalog, conversation_id=conversation_id)
        if changes is not None:
            try:
                patch = validate_criteria(changes)
            except ValueError:
                result.update(status="clarify", reply="Tiêu chí chưa hợp lệ. Hãy kiểm tra lại số tiền, số lượng và thành phần cần tránh.", reason="invalid_criteria")
                return result
            interpretation = {"intent": "recommendation", "changes": patch}
        elif self.llm_enabled:
            result["model_calls"] = 1
            try:
                extracted = self.rag.extract(question, state, catalog)
                explicit = parse_demo(question, state["criteria"], catalog)
                interpretation = validate_extraction(extracted, question, explicit_changes=explicit["changes"])
            except ValueError as exc:
                result.update(status="clarify", reply=str(exc), reason="ambiguous_extraction")
                return result
            patch = interpretation["changes"]
            for key in ("avoid_allergens", "avoid_ingredients"):
                if key in patch:
                    patch[key] = sorted(set(state["criteria"].get(key, []) + patch[key]))
            # Numeric/allergen constraints require lexical evidence as well as valid model JSON.
            fixed_fields = ("budget_vnd", "quantity", "budget_scope", "budget_inclusive", "avoid_allergens",
                            "avoid_ingredients", "requested_id", "packaging", "category", "brand", "flavor", "excluded_flavors")
            for key in fixed_fields:
                extracted_value, explicit_value = patch.get(key), explicit["changes"].get(key)
                same = (normalize(extracted_value) == normalize(explicit_value) if isinstance(extracted_value, str)
                        and isinstance(explicit_value, str) else extracted_value == explicit_value)
                if key in patch and key in explicit["changes"] and not same:
                    result.update(status="clarify", reply="Mình chưa chắc đã hiểu đúng giá, số lượng hoặc điều kiện thành phần. Bạn có thể nêu rõ lại không?", reason="ambiguous_extraction")
                    return result
            for key in fixed_fields:
                if key in explicit["changes"]:
                    patch[key] = explicit["changes"][key]
        else:
            interpretation = parse_demo(question, state["criteria"], catalog)
            patch = validate_criteria(interpretation["changes"])
        current = state["criteria"]
        removing_restriction = any(key in patch and set(current.get(key, [])) - set(patch[key]) for key in ("avoid_allergens", "avoid_ingredients"))
        if removing_restriction and not confirm_removal:
            result.update(status="clarify", reply="Bạn cần xác nhận rõ việc bỏ hạn chế thành phần trong form tiêu chí.", reason="confirm_restriction_removal")
            return result
        next_criteria = {**current, **patch}
        next_criteria = {k: v for k, v in next_criteria.items() if v is not None}
        if interpretation['intent'] == 'cart_add':
            result.update(status='clarify', reason='cart_intent',
                          reply='Bạn hãy nêu rõ SKU, số lượng và thao tác thêm vào giỏ, hoặc dùng nút Thêm vào giỏ.')
            return result
        commerce_intents = {'wholesale': 'wholesale', 'cart_view': 'review',
                            'checkout': 'review', 'payment': 'payment', 'order_status': 'orders'}
        if interpretation['intent'] in commerce_intents:
            from orders.wholesale import validate_cart
            items = interpretation.get('commerce_items', [])
            if items:
                try:
                    items = validate_cart(items)
                except ValueError:
                    result.update(status='clarify', reason='commerce_quantity', reply='Bạn cho mình SKU và số lượng bằng số để kiểm tra lại nhé.')
                    return result
                question_numbers = re.findall(
                    r'(?<![\w.,+-])[+-]?\d+(?:[.,]\d+)*(?!\w|[.,]\d)',
                    re.sub(r'\b[A-Za-z]{2,10}-\d+\b', '', question),
                )
                known_ids = {p['id'] for p in exact_products(question, catalog)} | {p['id'] for p in state.get('last_products', [])}
                known_ids.update(item['product_id'] for item in state.get('last_wholesale_items', []))
                if any(item['product_id'] not in known_ids or str(item['quantity']) not in question_numbers for item in items):
                    result.update(status='clarify', reason='commerce_quantity', reply='Bạn xác nhận giúp tên/SKU và số lượng mỗi loại trước khi mình tính giá hoặc thêm giỏ nhé.')
                    return result
            state['criteria'] = next_criteria
            outcome = handle_commerce(self, dict(action=commerce_intents[interpretation['intent']], items=items), state, catalog, conversation_id=conversation_id)
            outcome['model_calls'] = result['model_calls']
            return outcome
        if patch.get("budget_scope", "unchanged") is None:
            next_criteria.pop("budget_scope", None)
        if interpretation.get("cheaper"):
            last = state.get("last_products", [])
            if patch.get("requested_id"):
                last = [p for p in last if p["id"] == patch["requested_id"]]
            if len(last) != 1:
                result.update(status="clarify", reply="Bạn muốn rẻ hơn sản phẩm nào? Hãy nêu SKU trong các sản phẩm vừa xem.", reason="ambiguous_reference")
                return result
            next_criteria.update(budget_vnd=last[0]["price_vnd"], budget_scope="unit", budget_inclusive=False)
            interpretation["intent"] = "recommendation"
        if interpretation["intent"] == "clarify":
            result.update(status="clarify", reply=interpretation.get("question") or "Bạn muốn tìm loại sản phẩm nào và có điều kiện bắt buộc gì?", reason="missing_detail")
            return result
        if interpretation["intent"] == "staff":
            result.update(status="unresolved", reply="Yêu cầu hỗ trợ sẽ được lưu vào hàng đợi local.", reason="staff_requested", ticket_needed=True)
            return result
        if interpretation["intent"] == "out_of_scope":
            result.update(status="out_of_scope", reply="Mình hỗ trợ bánh kẹo, giá sỉ theo số lượng, giỏ nhiều SKU, chốt đơn và xem trạng thái thanh toán. Bạn cần hỗ trợ mặt hàng nào?", reason="out_of_scope")
            return result
        if interpretation["intent"] == "conversation":
            result["reply"] = "Mình là trợ lý của cửa hàng Mộc, có thể tư vấn bánh kẹo và giải đáp thông tin cửa hàng."
            return result
        # Persist explicit, validated changes even when one budget detail still needs clarification.
        state["criteria"] = next_criteria
        if next_criteria.get("budget_vnd") and next_criteria.get("quantity", 1) > 1 and not next_criteria.get("budget_scope"):
            result.update(status="clarify", reply=f"Ngân sách {money(next_criteria['budget_vnd'])} là cho mỗi sản phẩm hay tổng {next_criteria['quantity']} sản phẩm?", reason="budget_scope")
            return result
        if next_criteria.get("budget_vnd") and next_criteria.get("budget_scope") == "total" and not next_criteria.get("quantity"):
            result.update(status="clarify", reply="Bạn cần bao nhiêu sản phẩm để mình đối chiếu đúng tổng ngân sách?", reason="missing_quantity")
            return result
        if interpretation["intent"] != "faq":
            products, text, reason, considered = self._products(question, state, catalog, interpretation)
            result.update(products=products, product_ids=[p["id"] for p in products], checked_product_ids=considered)
            result["parts"].append({"kind": "product", "status": "unresolved" if reason else "answered", "reply": text})
            if reason:
                result.update(reason=reason, ticket_needed=True)
        if interpretation["intent"] == "faq" or interpretation.get("faq"):
            self._append_faq(question, catalog, result)
        result["source_ids"].extend(f"{p['id']}@{p['version']}" for p in result["products"])
        statuses = {p["status"] for p in result["parts"]}
        result["status"] = "partially_answered" if len(statuses) > 1 else ("unresolved" if "unresolved" in statuses else "answered")
        result["reply"] = "\n\n".join(part["reply"] for part in result["parts"])
        if result["products"]:
            state["last_products"] = [{"id": p["id"], "price_vnd": p["price_vnd"]} for p in result["products"] if p["price_vnd"] is not None]
        return result

    def handle_message(self, message, conversation_id, request_id=None, *, channel="private", criteria_changes=None,
                       confirm_removal=False, commerce_action=None):
        request_id = request_id or uuid4().hex
        base = {"request_id": request_id, "status": "error", "publish": True, "reply": None,
                "product_ids": [], "source_ids": [], "ticket_id": None, "reason": None,
                "products": [], "parts": [], "criteria": {}, "support_status": "none", "model_calls": 0}
        if (not isinstance(message, str) or not message.strip() or len(message) > 4000
                or not isinstance(conversation_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", conversation_id)
                or not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", request_id)
                or channel not in {"private", "public"} or type(confirm_removal) is not bool):
            return {**base, "reply": "Nội dung trống/quá dài hoặc mã phiên/yêu cầu không hợp lệ.", "reason": "invalid_input"}
        if channel == "public":
            # No public adapter is enabled in the local pilot.
            base["publish"] = False
        if commerce_action is not None and not isinstance(commerce_action, dict):
            return {**base, 'reply': 'Thao tác giỏ hàng không hợp lệ.', 'reason': 'invalid_input'}
        fingerprint_data = [message, channel, criteria_changes, confirm_removal]
        if commerce_action is not None:
            from orders.wholesale import CommerceAction
            try:
                commerce_action = CommerceAction.model_validate(commerce_action).model_dump()
            except ValidationError:
                return {**base, 'reply': 'Thao tác giỏ hàng, SKU hoặc số lượng chưa hợp lệ.', 'reason': 'invalid_input'}
            fingerprint_data.append(commerce_action)
        fingerprint = hashlib.sha256(dumps(fingerprint_data).encode()).hexdigest()
        started = time.perf_counter()
        committed_result = None
        try:
            # shortcut: serialize local pilot turns in SQLite; use per-conversation workers before multi-host deployment.
            with self.store.transaction() as db:
                previous = self.store.previous(request_id, conversation_id, fingerprint, db)
                if previous is not None:
                    return previous
                state = self.store.state(conversation_id, db)
                try:
                    question = message.strip()
                    staff_request = criteria_changes is None and commerce_action is None and re.fullmatch(
                        r"(?:(?:toi|minh|em) muon )?gap nhan vien(?: ho tro)?[.!]?",
                        normalize(question),
                    )
                    catalog = None
                    if staff_request and not state["takeover"]:
                        outcome = dict(status="unresolved", reason="staff_requested", ticket_needed=True,
                                       reply="Yêu cầu hỗ trợ sẽ được lưu vào hàng đợi local.")
                    else:
                        if not state["takeover"] and question != "/reset":
                            catalog = self.refresh()
                        outcome = self._process(question, state, catalog, criteria_changes, confirm_removal,
                                                commerce_action=commerce_action, conversation_id=conversation_id)
                    if (commerce_action is None and outcome["status"] == "answered"
                            and any(part["kind"] == "commerce" for part in outcome.get("parts", []))
                            and any(contains(question, term) for terms in self.FAQ_TOPICS
                                    if terms[0] != "thanh toán" for term in terms)):
                        covered_topics = ("thanh toán",) if "orders" in outcome else ()
                        self._append_faq(question, catalog, outcome, covered_topics=covered_topics)
                        outcome["reply"] = "\n\n".join(part["reply"] for part in outcome["parts"])
                        if outcome["ticket_needed"]:
                            outcome["status"] = "partially_answered"
                    criteria = state["criteria"]
                    use_checked_reply = (outcome.get('skip_generation') or criteria.get("avoid_allergens")
                                         or criteria.get("avoid_ingredients") or ingredient_topic(message))
                    if self.llm_enabled and outcome["status"] == "answered" and message.strip() != "/reset" and not use_checked_reply:
                        outcome["reply"], calls = self.rag.answer(message, state, catalog, outcome)
                        outcome["model_calls"] += calls
                        outcome["response_origin"] = "llm"
                    result = {**base, **outcome, "criteria": deepcopy(state["criteria"]), "takeover": state["takeover"]}
                    if catalog is not None:
                        result.update(data_version=catalog.version, demo=catalog.demo, checked_at=catalog.now.isoformat())
                    if result.get('order'):
                        committed_result = result
                except Exception as exc:
                    # Only a fixed public message is emitted; provider exceptions may contain request data/secrets.
                    print(f"Shop processing failure: {type(exc).__name__}", file=sys.stderr)
                    state = self.store.state(conversation_id, db)
                    result = {**base, "reply": "Tạm thời chưa thể kiểm tra dữ liệu hoặc kết nối. Câu hỏi của bạn được giữ; vui lòng thử lại hoặc yêu cầu hỗ trợ.",
                              "reason": "technical_failure", "ticket_needed": True, "criteria": deepcopy(state["criteria"])}
                if result.pop("ticket_needed", False) or (channel == "public" and result["status"] == "out_of_scope"):
                    ticket = self.store.ticket(db, request_id=request_id, conversation_id=conversation_id,
                        question=message, channel=channel, reason=result["reason"], criteria=state["criteria"],
                        product_ids=result.get("checked_product_ids", result["product_ids"]), source_ids=result["source_ids"])
                    result.update(ticket_id=ticket, support_status="recorded_local")
                    if result["reason"] == "staff_takeover":
                        result["reply"] += f"\n\nĐã lưu nội dung vào yêu cầu {ticket}."
                    else:
                        result["reply"] += f"\n\nĐã lưu yêu cầu {ticket} vào hàng đợi local. " + (
                            "Nhân viên theo dõi hàng đợi này; chưa gửi thông báo ra kênh ngoài." if self.queue_monitored else
                            "Chưa cấu hình người theo dõi hoặc thông báo ra ngoài; chưa xác nhận nhân viên đã nhận.")
                if channel == "public":
                    result["publish"] = False
                    if self.public_hold and result["status"] in {"unresolved", "partially_answered", "error"}:
                        result["reply"] = None
                if result["status"] != "error" and message.strip() != "/reset":
                    state["history"].extend([{"role": "user", "content": message},
                                             {"role": "assistant", "content": result["reply"] or "Nội dung công khai được giữ để kiểm tra."}])
                    del state["history"][:-6]
                self.store.save(db, conversation_id, request_id, fingerprint, state, result,
                                round((time.perf_counter() - started) * 1000))
                return result
        except (sqlite3.Error, OSError):
            print("Shop support persistence failure", file=sys.stderr)
            if committed_result is not None:
                return {**committed_result, 'history_saved': False, 'reply': committed_result['reply'] + '\n\nĐơn đã lưu trong SQL Server; chưa lưu được lịch sử hội thoại. Không chốt lại đơn mới, hãy kiểm tra tab Đơn hàng hoặc liên hệ nhân viên bằng mã đơn trên.'}
            return {**base, "reply": "Chưa lưu được yêu cầu hỗ trợ. Không có xác nhận chuyển nhân viên. Hãy thử lại khi bộ nhớ hoạt động.", "reason": "queue_failure"}
        except ValueError:
            return {**base, "reply": "Mã yêu cầu bị trùng với nội dung khác. Hãy gửi một yêu cầu mới.", "reason": "request_conflict"}


def handle_message(message, conversation_id, request_id=None, *, engine, **kwargs):
    """Single contract used by Streamlit, CLI and future authenticated adapters."""
    return engine.handle_message(message, conversation_id, request_id, **kwargs)


def create_engine(mode=None, *, load_on_start=True):
    # Reuse the sample's .env loading without importing the embedding model into demo mode.
    from dotenv import load_dotenv
    load_dotenv(PROJECT_DIR / ".env")
    mode = mode or os.getenv("SHOP_MODE", "demo")
    return ShopEngine(mode=mode,
                      llm_enabled=os.getenv("SHOP_LLM_ENABLED", "true").strip().lower() == "true",
                      store=SupportStore(os.getenv("SHOP_DB_PATH") or PROJECT_DIR / ("shop_demo.sqlite3" if mode == "demo" else "shop_support.sqlite3")),
                      queue_monitored=os.getenv("SHOP_QUEUE_MONITORED", "false").lower() == "true",
                      load_on_start=load_on_start)
