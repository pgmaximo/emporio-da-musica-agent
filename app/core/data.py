"""Acesso aos CSVs da loja. Tudo em memória: 65 produtos, 50 clientes, 20 pedidos."""
from __future__ import annotations

import csv
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from .rules import brl, money

DATA_DIR = Path(os.getenv("EMPORIO_DATA_DIR") or Path(__file__).resolve().parents[2] / "data")

STATUS_PT = {
    "delivered": "entregue",
    "shipped": "enviado, a caminho do cliente",
    "confirmed": "confirmado, ainda não despachado",
    "pending": "pendente, aguardando confirmação",
    "cancelled": "cancelado",
}
PAYMENT_PT = {"pix": "PIX", "debit": "cartão de débito", "boleto": "boleto bancário"}
STOPWORDS = {"de", "do", "da", "dos", "das", "um", "uma", "o", "a", "os", "as", "para", "com", "e", "em", "no", "na", "que",
             "quanto", "custa", "preco", "valor", "tem", "voces", "quais", "qual", "opcoes", "ate", "por", "me", "mais"}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower().strip()


def normalize_phone(raw: str) -> str:
    """Devolve DDD + número só com dígitos. Aceita '(67) 99812-3456', '+55 67 99812-3456' e o formato do WhatsApp sem o 9."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("55") and len(digits) in (12, 13):
        digits = digits[2:]
    if len(digits) == 10 and digits[2] in "6789":  # celular antigo sem o nono dígito
        digits = digits[:2] + "9" + digits[2:]
    return digits


def _read(name: str) -> list[dict]:
    with open(DATA_DIR / f"desafio_tecnico_ai_eng - {name}.csv", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


@dataclass(frozen=True)
class Product:
    id: int
    name: str
    price: float
    category_id: int
    category: str
    description: str
    stock: int
    status: str
    specs: dict


@lru_cache(maxsize=1)
def categories() -> dict[int, str]:
    return {int(r["category_id"]): r["name"] for r in _read("categories")}


@lru_cache(maxsize=1)
def products() -> dict[int, Product]:
    cats = categories()
    return {int(r["product_id"]): Product(
        id=int(r["product_id"]), name=r["name"], price=float(r["price_brl"]), category_id=int(r["category_id"]),
        category=cats[int(r["category_id"])], description=r["description"], stock=int(r["stock_quantity"]),
        status=r["status"], specs=json.loads(r["specs"] or "{}")) for r in _read("products")}


@lru_cache(maxsize=1)
def _active_promotions() -> dict[int, dict]:
    """Só is_active=1 vale (§7.3: promoção vencida nunca é prometida). Mais de uma ativa: vale a maior, pois não acumulam (§6.2)."""
    out: dict[int, dict] = {}
    for r in _read("promotions"):
        if r["is_active"] == "1":
            pid, pct = int(r["product_id"]), float(r["discount_percent"])
            if pid not in out or pct > out[pid]["percent"]:
                out[pid] = {"percent": pct, "name": r["description"]}
    return out


@lru_cache(maxsize=1)
def _expired_promotions() -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for r in _read("promotions"):
        if r["is_active"] != "1":
            out.setdefault(int(r["product_id"]), []).append(f"{r['description']} ({r['discount_percent'].rstrip('0').rstrip('.')}%)")
    return out


def availability(p: Product) -> str:
    if p.status == "discontinued":
        return "descontinuado"
    if p.status == "coming_soon":
        return "em breve (ainda não disponível para compra)"
    return "disponível" if p.stock > 0 else "esgotado no momento"


def current_price(p: Product) -> float:
    promo = _active_promotions().get(p.id)
    return float(money(p.price * (1 - promo["percent"] / 100))) if promo else p.price


def product_view(p: Product, detail: bool = False) -> dict:
    promo = _active_promotions().get(p.id)
    view = {"id": p.id, "nome": p.name, "categoria": p.category, "disponibilidade": availability(p)}
    if p.status == "active" and p.stock > 0:
        view["unidades_em_estoque"] = p.stock
    if promo:
        view.update(preco_original=brl(p.price), preco_promocional=brl(current_price(p)),
                    desconto=f"{promo['percent']:g}%", promocao=promo["name"])
    else:
        view["preco"] = brl(p.price)
        if p.id in _expired_promotions():
            view["promocao_anterior_encerrada"] = ", ".join(_expired_promotions()[p.id])
    if detail:
        view.update(descricao=p.description, especificacoes=p.specs)
    return view


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", normalize(text)) if t not in STOPWORDS]


def _score(p: Product, tokens: list[str]) -> float:
    name_toks = _tokens(p.name)
    haystack = normalize(f"{p.description} {json.dumps(p.specs, ensure_ascii=False)} {p.category}")
    total = 0.0
    for t in tokens:
        if any(nt.startswith(t) or t.startswith(nt) and len(nt) > 2 for nt in name_toks):
            total += 1
        elif any(SequenceMatcher(None, t, nt).ratio() >= 0.85 for nt in name_toks):
            total += 0.8
        elif t in haystack:
            total += 0.4
    return total / len(tokens)


def sold_categories() -> list[str]:
    """Categorias que têm ao menos um produto à venda (o manual cita sopro e cordas, mas o catálogo não tem)."""
    return sorted({p.category for p in products().values() if p.status == "active" and p.stock > 0})


def alternatives(p: Product, n: int = 3) -> list[Product]:
    """Mesma categoria e à venda; primeiro o que o nome tem em comum (ex.: '7 cordas nylon', sem contar a marca), depois o preço mais próximo."""
    traits = set(_tokens(p.name)[1:])
    pool = [q for q in products().values() if q.id != p.id and q.category_id == p.category_id and q.status == "active" and q.stock > 0]
    return sorted(pool, key=lambda q: (-len(traits & set(_tokens(q.name)[1:])), abs(current_price(q) - current_price(p))))[:n]


def search_products(query: str = "", category: str | None = None, min_price: float | None = None,
                    max_price: float | None = None, limit: int = 8) -> dict:
    """Sem texto (navegação): só itens à venda. Com texto (busca de um modelo): inclui indisponíveis, para a loja poder dizer o status."""
    tokens = _tokens(query)
    hits = []
    for p in products().values():
        if category and p.category != category:
            continue
        price = current_price(p)
        if (min_price is not None and price < min_price) or (max_price is not None and price > max_price):
            continue
        for_sale = p.status == "active" and p.stock > 0
        if not tokens:
            if for_sale:
                hits.append((0.0, price, p))
            continue
        if (score := _score(p, tokens)) >= 0.5:
            hits.append((-score, price, p))
    hits.sort(key=lambda h: (h[0], h[1]))
    top = [h[2] for h in hits[:limit]]
    result = {"total_encontrado": len(hits), "produtos": [product_view(p) for p in top]}
    if tokens and top and not (top[0].status == "active" and top[0].stock > 0):
        result["alternativas_disponiveis"] = [product_view(q) for q in alternatives(top[0])]
    return result


def get_product(product_id: int) -> Product | None:
    return products().get(product_id)


@lru_cache(maxsize=1)
def _customers() -> dict[int, dict]:
    return {int(r["customer_id"]): r for r in _read("customers")}


def customer_by_phone(phone: str) -> dict | None:
    wanted = normalize_phone(phone)
    for cid, r in _customers().items():
        if normalize_phone(r["phone"]) == wanted:
            return {"id": cid, "nome": r["name"], "primeiro_nome": r["name"].split()[0], "cidade": r["city"],
                    "telefone": r["phone"]}
    return None


def list_customers() -> list[dict]:
    return [{"id": cid, "nome": r["name"], "telefone": r["phone"], "cidade": r["city"],
             "pedidos": [o["order_id"] for o in _orders() if int(o["customer_id"]) == cid]} for cid, r in _customers().items()]


@lru_cache(maxsize=1)
def _orders() -> list[dict]:
    return _read("orders")


@lru_cache(maxsize=1)
def _items() -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for r in _read("order_items"):
        out.setdefault(int(r["order_id"]), []).append(r)
    return out


def _payment(code: str) -> str:
    if code.startswith("credit_"):
        return f"cartão de crédito em {code.split('_')[1]}"
    return PAYMENT_PT.get(code, code)


def _order_view(o: dict) -> dict:
    d = lambda s: f"{s[8:10]}/{s[5:7]}/{s[:4]}" if s else None  # noqa: E731
    view = {"numero": int(o["order_id"]), "data_do_pedido": d(o["order_date"]), "status": STATUS_PT[o["status"]],
            "itens": [{"produto": products()[int(i["product_id"])].name, "quantidade": int(i["quantity"])} for i in _items()[int(o["order_id"])]],
            "total_pago": brl(o["total_brl"]), "forma_de_pagamento": _payment(o["payment_method"])}
    if o["tracking_code"]:
        view["codigo_de_rastreio"] = o["tracking_code"]
    if o["estimated_delivery"]:
        view["previsao_de_entrega" if o["status"] != "delivered" else "entregue_em_aprox"] = d(o["estimated_delivery"])
    if o["notes"]:
        view["observacoes"] = o["notes"]
    return view


def orders_for_customer(customer_id: int) -> list[dict]:
    return [_order_view(o) for o in _orders() if int(o["customer_id"]) == customer_id]


def raw_order(customer_id: int, order_id: int) -> dict | None:
    """Pedido bruto, só se pertencer ao cliente. Pedido inexistente e pedido de terceiro são indistinguíveis (não vaza existência)."""
    return next((o for o in _orders() if int(o["order_id"]) == order_id and int(o["customer_id"]) == customer_id), None)


def order_view(o: dict) -> dict:
    return _order_view(o)
