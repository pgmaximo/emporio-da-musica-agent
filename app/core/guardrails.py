"""Verificação determinística de preços: todo 'R$ x' da resposta precisa ter vindo de uma tool ou do próprio cliente.

Versão mínima, sem custo de LLM, do "agente que confere o agente" usado por operações de WhatsApp em produção.
"""
from __future__ import annotations

import re
from decimal import Decimal

MONEY = re.compile(r"R\$\s*(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?")
NUMBER = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?")


def _value(whole: str, cents: str | None) -> Decimal:
    return Decimal(whole.replace(".", "")) + Decimal(f"0.{(cents or '0').ljust(2, '0')}")


def money_values(text: str) -> set[Decimal]:
    return {_value(m.group(1), m.group(2)) for m in MONEY.finditer(text)}


def _plain_numbers(text: str) -> set[Decimal]:
    out = set()
    for m in NUMBER.finditer(text):
        raw = m.group(0)
        if "," in raw or re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):  # "1.000,50" e "1.000" usam o ponto como milhar
            raw = raw.replace(".", "").replace(",", ".")
        out.add(Decimal(raw))
    return out


def unverified_prices(reply: str, tool_outputs: list[str], user_text: str = "") -> list[str]:
    """Valores em reais citados na resposta que não aparecem em nenhuma tool nem na fala do cliente."""
    allowed: set[Decimal] = set()
    for out in tool_outputs:
        allowed |= money_values(out)
    allowed |= _plain_numbers(user_text)  # o cliente pode citar um orçamento e o agente repeti-lo
    bad = [v for v in money_values(reply) if v not in allowed]
    return sorted(f"R$ {v:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".") for v in bad)


# "Vou passar você para um atendente" / "já acionei um atendente": promessa de ação que só é verdadeira se a tool foi chamada.
_CLAIM = re.compile(r"\b(vou|irei|já|acabei de|estou)\b[^.!?\n]{0,40}\b(pass\w+|encaminh\w+|transfer\w+|acion\w+|cham\w+)\b[^.!?\n]{0,40}\b(atendente|humano|pessoa|equipe)\b", re.I)
_OFFER = re.compile(r"\b(posso|se quiser|quer que|caso queira|se preferir|gostaria)\b", re.I)


def claims_transfer(reply: str) -> bool:
    return bool(_CLAIM.search(reply)) and not _OFFER.search(reply)
