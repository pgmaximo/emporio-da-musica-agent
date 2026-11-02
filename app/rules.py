"""Regras de negócio do manual de políticas, em código determinístico.

O LLM nunca faz conta: ele chama uma tool que usa estas funções e repete o resultado.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

PIX_DISCOUNT = Decimal("0.05")
FREE_SHIPPING_FROM = Decimal("500")  # §5.1 "acima de R$ 500": o valor exato não está definido, assumimos grátis a partir de R$ 500
FLAT_SHIPPING = Decimal("35")
REGRET_DAYS = 7  # §4.1 arrependimento, dias corridos após o recebimento
DEFECT_SWAP_DAYS = 30  # §4.2 troca por defeito, dias corridos após a compra
LEGAL_WARRANTY_DAYS = 90  # §8.1 garantia legal, a partir do recebimento
METRO_CITIES = {"campo grande"}  # shortcut: a lista oficial da região metropolitana não está no manual
# Feriados nacionais de data fixa; os móveis (Carnaval, Sexta Santa, Corpus Christi) não estão cobertos.
HOLIDAYS = {(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (11, 20), (12, 25)}
OPENING = {0: (time(9), time(18)), 1: (time(9), time(18)), 2: (time(9), time(18)),
           3: (time(9), time(18)), 4: (time(9), time(18)), 5: (time(9), time(13))}
WEEKDAYS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]


def money(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"), ROUND_HALF_UP)


def brl(v) -> str:
    s = f"{money(v):,.2f}"
    return "R$ " + s.replace(",", "_").replace(".", ",").replace("_", ".")


def pix_price(price, promo_active: bool) -> dict:
    """§3 e §6.2: PIX dá 5%, mas não acumula com preço promocional."""
    if promo_active:
        return {"preco_pix": brl(price), "desconto_pix_aplicado": False,
                "motivo": "o desconto de PIX não se acumula com preço promocional (§6.2)"}
    return {"preco_pix": brl(money(price) * (1 - PIX_DISCOUNT)), "desconto_pix_aplicado": True,
            "motivo": "5% de desconto no PIX sobre o preço de tabela"}


def min_installment(n: int) -> int:
    return 50 if n <= 3 else 80 if n <= 6 else 100  # §3.1


def installment_options(total) -> list[dict]:
    """Parcelas sem juros válidas no crédito (§3.1). 1x sempre vale; de 2x em diante vale a parcela mínima da faixa."""
    total = money(total)
    out = [{"parcelas": 1, "valor_parcela": brl(total)}]
    for n in range(2, 13):
        if total / n < min_installment(n):
            break
        out.append({"parcelas": n, "valor_parcela": brl(total / n)})
    return out


def shipping(order_total, city: str) -> dict:
    """§5.1 e §5.2. Fora de Campo Grande o valor depende de CEP/peso e não temos tabela: não inventamos preço."""
    if city.strip().lower() in METRO_CITIES:
        total = money(order_total)
        free = total >= FREE_SHIPPING_FROM
        return {"regiao": "Campo Grande", "frete": brl(0 if free else FLAT_SHIPPING),
                "frete_gratis": free, "prazo": "1 a 3 dias úteis, entrega por motoboy próprio"}
    return {"regiao": city, "frete": "valor calculado pelo CEP de destino, peso e dimensões (não disponível aqui)",
            "frete_gratis": False,
            "modalidades": [{"nome": "PAC (Correios)", "prazo": "5 a 12 dias úteis"},
                            {"nome": "SEDEX (Correios)", "prazo": "2 a 5 dias úteis"},
                            {"nome": "Jadlog (.package)", "prazo": "3 a 8 dias úteis"}],
            "observacao": "todos com rastreio e seguro; instrumentos de grande porte têm cotação individual"}


def store_status(now: datetime) -> dict:
    """§2: seg-sex 9h-18h, sáb 9h-13h, domingo e feriados fechado."""

    def opens(d: date):
        return None if (d.month, d.day) in HOLIDAYS else OPENING.get(d.weekday())

    hours = opens(now.date())
    open_now = bool(hours and hours[0] <= now.time().replace(tzinfo=None) < hours[1])
    nxt = None
    if not open_now:
        d = now.date()
        for i in range(0, 9):
            day = d + timedelta(days=i)
            h = opens(day)
            if h and (i > 0 or now.time().replace(tzinfo=None) < h[0]):
                nxt = f"{'hoje' if i == 0 else WEEKDAYS[day.weekday()]} às {h[0]:%H:%M}"
                break
    return {"aberta_agora": open_now, "agora": f"{WEEKDAYS[now.weekday()]}, {now:%d/%m/%Y %H:%M}",
            "proxima_abertura": nxt,
            "horario": "seg a sex 09:00-18:00, sábado 09:00-13:00, domingo e feriados fechado"}


def return_assessment(status: str, order_date: str, delivery: str, today: date) -> dict:
    """Aplica §4 e §8 a um pedido. A data de recebimento real não existe nos dados: usamos a previsão de entrega."""
    iso = lambda s: date.fromisoformat(s) if s else None  # noqa: E731
    bought, received = iso(order_date), iso(delivery)
    if status == "cancelled":
        return {"situacao": "pedido_cancelado", "elegivel_arrependimento": False,
                "explicacao": "O pedido já está cancelado, não há o que devolver."}
    if status in ("pending", "confirmed"):
        return {"situacao": "ainda_nao_enviado", "elegivel_arrependimento": None,
                "explicacao": "O pedido ainda não foi despachado. O prazo de arrependimento (§4.1) só começa no recebimento; "
                              "o cancelamento antes do envio não está descrito no manual, então deve ser feito por um atendente."}
    if status == "shipped":
        return {"situacao": "em_transporte", "elegivel_arrependimento": None,
                "explicacao": f"O pedido ainda não foi entregue (previsão {received:%d/%m/%Y}). Os 7 dias corridos de arrependimento "
                              "contam a partir do recebimento (§4.1)."}
    limit_regret = received + timedelta(days=REGRET_DAYS)
    limit_defect = bought + timedelta(days=DEFECT_SWAP_DAYS)
    limit_warranty = received + timedelta(days=LEGAL_WARRANTY_DAYS)
    ok = today <= limit_regret
    return {
        "situacao": "entregue",
        "recebimento_considerado": f"{received:%d/%m/%Y} (previsão de entrega, o sistema não guarda a data real)",
        "dias_desde_recebimento": (today - received).days,
        "elegivel_arrependimento": ok,
        "prazo_arrependimento_ate": f"{limit_regret:%d/%m/%Y}",
        "condicoes_arrependimento": "compra online, produto na embalagem original, sem sinais de uso e com acessórios/manuais; "
                                    "reembolso na forma de pagamento original em até 10 dias úteis; frete de devolução por conta da loja",
        "troca_por_defeito_ate": f"{limit_defect:%d/%m/%Y}" + (" (ainda no prazo)" if today <= limit_defect else " (prazo encerrado)"),
        "garantia_legal_ate": f"{limit_warranty:%d/%m/%Y}" + (" (vigente)" if today <= limit_warranty else " (encerrada)"),
        "garantia_do_fabricante": "a maioria dos fabricantes dá de 6 meses a 2 anos (ver o certificado que acompanha o produto); depois dos 30 dias de troca "
                                  "o cliente aciona o fabricante e a loja pode intermediar se ele pedir (§4.2 e §8.2)",
        "explicacao": ("Dentro do prazo de arrependimento de 7 dias." if ok else
                       "Fora do prazo de arrependimento de 7 dias. Se o motivo for defeito de fabricação, valem a troca em 30 dias "
                       "(§4.2) e a garantia legal de 90 dias (§8.1)."),
    }
