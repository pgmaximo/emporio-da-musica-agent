from app.guardrails import claims_transfer, unverified_prices


def test_prices_must_come_from_tools_or_the_customer():
    tools = ['{"preco": "R$ 2.199,00", "pix": "R$ 2.089,05"}']
    assert unverified_prices("Sai por R$ 2.199,00 ou R$ 2.089,05 no PIX.", tools) == []
    assert unverified_prices("Sai por R$ 2.199 à vista.", tools) == []                      # sem centavos é o mesmo valor
    assert unverified_prices("Sai por R$ 1.999,00.", tools) == ["R$ 1.999,00"]
    assert unverified_prices("Dá para ficar em R$ 1.000.", [], "tenho até 1000 reais") == []  # orçamento dito pelo cliente
    assert unverified_prices("Dá para ficar em R$ 1.000.", [], "tenho 500 reais") == ["R$ 1.000,00"]


def test_transfer_claims_vs_offers():
    assert claims_transfer("Claro, vou passar você para um atendente humano agora.")
    assert claims_transfer("Já acionei um atendente humano pra você.")
    assert not claims_transfer("Se quiser, posso te passar para um atendente humano.")
    assert not claims_transfer("Seu pedido já foi enviado e está a caminho.")
