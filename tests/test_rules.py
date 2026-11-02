from datetime import date, datetime

from app.core import rules
from app.core.clock import TZ


def test_pix_discount_only_without_promo():
    assert rules.pix_price(2199, promo_active=False)["preco_pix"] == "R$ 2.089,05"
    out = rules.pix_price(549, promo_active=True)
    assert out["preco_pix"] == "R$ 549,00" and out["desconto_pix_aplicado"] is False


def test_brl_format():
    assert rules.brl(1234.5) == "R$ 1.234,50"
    assert rules.brl(0) == "R$ 0,00"


def test_installments_follow_section_3_1():
    n = lambda total: rules.installment_options(total)[-1]["parcelas"]  # noqa: E731
    assert n(150) == 3      # 4x seria R$ 37,50, abaixo do mínimo de R$ 80
    assert n(400) == 5      # 5x = R$ 80,00 passa; 6x = R$ 66,67 não
    assert n(799.9) == 7    # 7x = R$ 114,27 passa; 8x = R$ 99,99 não
    assert n(2199) == 12    # 12x = R$ 183,25
    assert rules.installment_options(40) == [{"parcelas": 1, "valor_parcela": "R$ 40,00"}]


def test_shipping_campo_grande_threshold():
    assert rules.shipping(499.99, "Campo Grande")["frete"] == "R$ 35,00"
    assert rules.shipping(500, "Campo Grande")["frete_gratis"] is True
    out = rules.shipping(1000, "Dourados")  # nunca inventa preço fora da capital
    assert "R$" not in out["frete"] and len(out["modalidades"]) == 3


def at(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=TZ)


def test_store_status():
    assert rules.store_status(at(2026, 3, 2, 10, 0))["aberta_agora"] is True          # segunda
    assert rules.store_status(at(2026, 3, 7, 12, 59))["aberta_agora"] is True         # sábado, 1 min antes de fechar
    sat_close = rules.store_status(at(2026, 3, 7, 13, 0))
    assert sat_close["aberta_agora"] is False and sat_close["proxima_abertura"] == "segunda-feira às 09:00"
    assert rules.store_status(at(2026, 3, 8, 11, 0))["aberta_agora"] is False         # domingo
    assert rules.store_status(at(2026, 3, 2, 8, 0))["proxima_abertura"] == "hoje às 09:00"
    assert rules.store_status(at(2026, 11, 2, 10, 0))["aberta_agora"] is False        # Finados


def test_return_assessment():
    d = lambda s: date.fromisoformat(s)  # noqa: E731
    ok = rules.return_assessment("delivered", "2026-02-03", "2026-02-17", d("2026-02-20"))
    assert ok["elegivel_arrependimento"] is True and ok["prazo_arrependimento_ate"] == "24/02/2026"
    late = rules.return_assessment("delivered", "2026-02-03", "2026-02-17", d("2026-02-25"))
    assert late["elegivel_arrependimento"] is False and "defeito" in late["explicacao"]
    assert rules.return_assessment("delivered", "2026-02-03", "2026-02-17", d("2026-02-24"))["elegivel_arrependimento"] is True  # 7º dia ainda vale
    assert rules.return_assessment("shipped", "2026-02-15", "2026-02-28", d("2026-02-20"))["situacao"] == "em_transporte"
    assert rules.return_assessment("pending", "2026-03-12", "", d("2026-03-20"))["elegivel_arrependimento"] is None
    assert rules.return_assessment("cancelled", "2026-01-28", "", d("2026-03-20"))["situacao"] == "pedido_cancelado"
