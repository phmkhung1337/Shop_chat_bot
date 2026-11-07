"""200 distinct named demo products, never represented as approved real-shop data."""
import json
import random
from pathlib import Path

# These are different named varieties, not copies with different SKU/weight suffixes.
GROUPS = [
    ("Bánh", "Bánh quy", """
Bánh quy socola chip
Bánh quy hạnh nhân
Bánh quy bơ đậu phộng
Bánh quy mè đen
Bánh quy cà phê
Bánh quy gừng
Bánh quy quế
Bánh quy chanh
Bánh quy cam
Bánh quy mật ong
Bánh quy phô mai
Bánh quy khoai lang tím
Bánh quy bí đỏ
Bánh quy hạt chia
Bánh quy hạt lanh
Bánh quy nho khô
Bánh quy nam việt quất
Bánh quy mâm xôi
Bánh quy việt quất
Bánh quy mắc ca
Bánh quy hồ đào
Bánh quy hạt dẻ cười
Bánh quy dừa dứa
Bánh quy caramel muối
Bánh quy lúa mạch
"""),
    ("Bánh", "Bánh ngọt", """
Bánh brownie socola
Bánh blondie vani
Bánh muffin chuối
Bánh muffin việt quất
Bánh muffin socola
Bánh cupcake vani
Bánh cupcake red velvet
Bánh bông lan cuộn kem
Bánh bông lan trứng muối
Bánh bông lan lá dứa
Bánh bông lan vị cam
Bánh bông lan phô mai
Bánh madeleine
Bánh financier hạnh nhân
Bánh pound cake chanh
Bánh tart trứng
Bánh tart phô mai
Bánh su kem
Bánh choux giòn
Bánh donut đường
Bánh donut nhân mứt
Bánh churros quế
Bánh croissant bơ
Bánh pain au chocolat
Bánh palmier
"""),
    ("Bánh", "Bánh truyền thống", """
Bánh đậu xanh
Bánh pía sầu riêng
Bánh pía khoai môn
Bánh in đậu xanh
Bánh khảo
Bánh cáy
Bánh cốm
Bánh phu thê
Bánh gai
Bánh dẻo đậu xanh
Bánh nướng thập cẩm
Bánh nướng hạt sen
Bánh chả
Bánh tai heo
Bánh nhãn
Bánh men
Bánh thuẫn
Bánh phục linh
Bánh tét chuối
Bánh ít lá gai
Bánh bò nướng
Bánh da lợn
Bánh chuối hấp
Bánh khoai mì nướng
Bánh tổ
"""),
    ("Bánh", "Bánh xốp và cracker", """
Bánh xốp vani
Bánh xốp socola
Bánh xốp sữa dừa
Bánh xốp trà xanh
Bánh xốp dâu tây
Bánh xốp phô mai
Bánh xốp tiramisu
Bánh xốp kem chanh
Bánh ốc quế nhân cacao
Bánh ốc quế nhân sữa
Bánh que phủ socola
Bánh que phủ dâu
Bánh que phủ matcha
Bánh cracker phô mai
Bánh cracker lúa mì
Bánh cracker rau củ
Bánh cracker mè
Bánh gạo vị mật ong
Bánh gạo vị phô mai
Bánh gạo vị tôm
"""),
    ("Kẹo", "Kẹo cứng", """
Kẹo chanh
Kẹo cam
Kẹo dứa
Kẹo dưa hấu
Kẹo nho
Kẹo táo xanh
Kẹo đào
Kẹo vải
Kẹo xoài
Kẹo me
Kẹo gừng
Kẹo quế
Kẹo cà phê
Kẹo trà xanh
Kẹo mật ong
Kẹo bạc hà chanh
Kẹo thảo mộc
Kẹo mạch nha
Kẹo đường phèn
Kẹo gậy Giáng sinh
"""),
    ("Kẹo", "Kẹo dẻo và thạch", """
Kẹo dẻo hình gấu
Kẹo dẻo sâu chua
Kẹo dẻo cola
Kẹo dẻo vị sữa chua
Kẹo dẻo đào
Kẹo dẻo xoài
Kẹo dẻo nho
Kẹo dẻo cam
Kẹo dẻo chanh
Kẹo dẻo dâu
Kẹo dẻo việt quất
Kẹo dẻo vải
Kẹo dẻo chanh dây
Kẹo dẻo kiwi
Kẹo dẻo nam việt quất
Kẹo thạch dừa
Kẹo thạch nha đam
Kẹo thạch konjac
Kẹo marshmallow vani
Kẹo marshmallow phủ socola
"""),
    ("Chocolate", "Socola", """
Socola sữa nguyên bản
Socola trắng vani
Socola đen 85%
Socola đen 90%
Socola đen 99%
Socola ruby
Socola nhân rượu
Socola nhân caramel
Socola nhân kem bạc hà
Socola nhân cà phê
Socola nhân mứt cam
Socola nhân mâm xôi
Socola nhân dâu
Socola nhân chanh dây
Socola hạt phỉ
Socola mắc ca
Socola hạt dẻ cười
Socola hạt điều
Socola giòn hạt quinoa
Socola gạo nổ
"""),
    ("Kẹo", "Kẹo sữa và caramel", """
Kẹo sữa bò
Kẹo sữa dê
Kẹo sữa dừa
Kẹo sữa cacao
Kẹo sữa matcha
Kẹo caramel bơ
Kẹo caramel muối biển
Kẹo caramel cà phê
Kẹo toffee hạnh nhân
Kẹo fudge vani
Kẹo fudge socola
Kẹo nougat hạt
"""),
    ("Kẹo", "Kẹo cao su", """
Kẹo cao su bạc hà
Kẹo cao su dâu
Kẹo cao su dưa hấu
Kẹo cao su nho
Kẹo cao su táo
Kẹo cao su quế
Kẹo cao su vị cola
Kẹo cao su đào
Kẹo cao su chanh
Kẹo cao su trái cây nhiệt đới
"""),
    ("Chocolate", "Kẹo socola và hạt phủ", """
Hạnh nhân phủ socola
Nho khô phủ socola
Vỏ cam phủ socola
Dâu tây sấy phủ socola
Bánh quy phủ socola trắng
Bánh pretzel phủ socola
Kẹo truffle cacao
Kẹo truffle trà xanh
Kẹo praline hạt phỉ
Kẹo gianduja
"""),
]


def build_demo_catalog():
    from chatbot.shop import ALLERGENS, normalize
    folder = Path(__file__).resolve().parents[1] / "data" / "demo"
    raw = json.loads((folder / "products.json").read_text(encoding="utf-8"))
    faqs = json.loads((folder / "faq.json").read_text(encoding="utf-8"))
    # Keep the existing usable demo SKUs/edge cases; the discontinued legacy fixture stays in JSON tests only.
    products = [p for p in raw["products"] if p["status"] == "active"]
    flavors = ("chanh dây", "nam việt quất", "việt quất", "mâm xôi", "dưa hấu", "bạc hà", "hạnh nhân",
               "đậu phộng", "sầu riêng", "khoai môn", "lá dứa", "phô mai", "cà phê", "caramel", "matcha",
               "trà xanh", "vani", "dâu", "chuối", "xoài", "vải", "đào", "chanh", "cam", "dứa", "nho",
               "táo", "gừng", "quế", "dừa", "cacao", "socola", "sữa", "mè")
    index = 0
    for category, family, names in GROUPS:
        for name in names.strip().splitlines():
            index += 1
            flavor = next((f.capitalize() for f in flavors if normalize(f) in normalize(name)), "Nguyên bản")
            unit, weight = ("túi", 100) if category == "Kẹo" else ("hộp", 150 if category == "Bánh" else 100)
            product_id = f"DEMO-{index:03d}"
            products.append({"id": product_id, "name": name, "aliases": [name, product_id],
                "category": category, "brand": "Mộc Demo", "flavor": flavor,
                "description": f"{name}, thuộc nhóm {family}. Bản ghi minh họa cho tìm kiếm bánh kẹo trong SQL; chưa đại diện sản phẩm của hãng hoặc hàng đang bán. Thành phần, hạn dùng và hướng dẫn bảo quản cần xác minh từ nhãn thật.",
                "packaging": f"{unit} {weight} g", "unit": unit,
                "price_vnd": 24000 + index * 7000 % 175000, "stock_qty": 12 + index * 17 % 70,
                "availability": "available", "status": "active", "price_updated_at": raw["as_of"],
                "stock_updated_at": raw["as_of"], "source": "DEMO-SQL-200", "version": "demo-content-1",
                "url": None, "storage": None, "expiry": None,
                "ingredients": {"label": None, "source": None, "verified_at": None,
                                "allergens": {key: "unknown" for key in ALLERGENS}, "verified_absent": []}})
    if len(products) != 200 or len({p["id"] for p in products}) != 200 or len({normalize(p["name"]) for p in products}) != 200:
        raise ValueError("Seed cần đúng 200 SKU và 200 tên loại khác nhau.")
    rng = random.Random(20261105)
    for product in products:
        product.update(stock_qty=rng.randint(1, 999), availability="available", stock_updated_at=raw["as_of"])
    raw.update(version="demo-sql-200-v1", products=products)
    return raw, faqs
