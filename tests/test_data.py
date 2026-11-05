from app import data


def test_phone_normalization():
    assert data.normalize_phone("(67) 99812-3456") == "67998123456"
    assert data.normalize_phone("+55 67 99812-3456") == "67998123456"
    assert data.normalize_phone("556798123456") == "67998123456"  # WhatsApp antigo, sem o nono dígito


def test_customer_lookup_by_any_phone_format():
    assert data.customer_by_phone("5567998123456")["primeiro_nome"] == "Lucas"
    assert data.customer_by_phone("(67) 90000-0000") is None


def test_takamine_gd20_price():
    top = data.search_products("quanto custa o Takamine GD20")["produtos"][0]
    assert top["nome"].startswith("Takamine GD20") and top["preco"] == "R$ 2.199,00"


def test_browse_guitars_under_1000_only_available_and_cheap():
    res = data.search_products(category="Violões", max_price=1000)["produtos"]
    assert res and all(p["disponibilidade"] == "disponível" for p in res)
    prices = [float(p.get("preco_promocional", p.get("preco")).replace("R$ ", "").replace(".", "").replace(",", ".")) for p in res]
    assert prices == sorted(prices) and max(prices) <= 1000


def test_unavailable_product_is_reported_with_alternatives():
    res = data.search_products("Shelby SN-7C")
    assert res["produtos"][0]["disponibilidade"] == "descontinuado" and res["alternativas_disponiveis"]
    res = data.search_products("Giannini GF-3D")
    assert res["produtos"][0]["disponibilidade"] == "esgotado no momento"


def test_only_active_promotions_count():
    kalani = data.search_products("Kalani KAL-700T")["produtos"][0]  # promoção 12, ativa
    assert kalani["desconto"] == "10%" and kalani["preco_promocional"] == "R$ 323,10" and kalani["preco_original"] == "R$ 359,00"
    taylor = data.search_products("Taylor 110e")["produtos"][0]       # promoção 24, ativa (8%)
    assert taylor["desconto"] == "8%"
    expired = data.search_products("Yamaha C40")["produtos"][0]
    assert "preco_promocional" not in expired


def test_orders_are_scoped_to_the_customer():
    lucas = data.customer_by_phone("(67) 99812-3456")["id"]
    assert [o["numero"] for o in data.orders_for_customer(lucas)] == [4]
    assert data.raw_order(lucas, 5) is None          # pedido de outro cliente
    assert data.raw_order(lucas, 999) is None        # pedido inexistente: mesma resposta
    assert data.raw_order(lucas, 4)["status"] == "delivered"


def test_category_names_match_tool_literals():
    from app.tools import CATEGORIES
    assert set(CATEGORIES) == set(data.categories().values())


def test_alternatives_prefer_similar_instruments():
    alts = data.search_products("Shelby SN-7C")["alternativas_disponiveis"]
    assert all("7 Cordas" in a["nome"] for a in alts[:2])  # um violão de 7 cordas se substitui por outro de 7 cordas


def test_expired_promotion_only_shows_in_detail_view():
    p = data.search_products("Takamine GD20")["produtos"][0]
    assert "promocao_anterior_encerrada" not in p                       # busca comum não polui a resposta
    assert "promocao_anterior_encerrada" in data.product_view(data.get_product(p["id"]), detail=True)
