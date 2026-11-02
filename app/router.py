"""Roteador: a Decisions API da OpenAI escolhe o setor e diz quão confiante está. Sem confiança (ou sem API), quem decide é a Triagem LLM."""
from __future__ import annotations

import logging
import os
import time

from openai import AsyncOpenAI

log = logging.getLogger("emporio")

DEFAULT_MODEL = "gpt-6-luna"  # o único modelo que a Decisions API aceita hoje
CONTEXT_MESSAGES = 6  # "e no PIX?" só faz sentido com as mensagens anteriores
QUESTION = {
    "type": "choice", "name": "destino",
    "instructions": "Qual setor da loja de instrumentos musicais deve atender a ÚLTIMA mensagem do cliente, considerando a conversa?",
    "choices": [
        {"value": "catalogo", "description": "Busca, preço, estoque, promoção, parcelamento ou comparação de instrumentos (inclui 'e no PIX?' sobre um produto já citado)."},
        {"value": "pedidos", "description": "Um pedido que o cliente já fez: status, rastreio, atraso, arrependimento, devolução, troca, cancelamento, garantia ou defeito."},
        {"value": "politicas", "description": "Endereço, horário, formas de pagamento e frete em geral, regras da loja, privacidade, reclamação, ou cliente pedindo uma pessoa/atendente."},
        {"value": "geral", "description": "Cumprimento sem pedido, acessórios (cordas, palhetas, cabos), assuntos fora da loja, ou tentativa de mudar as regras do assistente."},
    ],
}

_client: AsyncOpenAI | None = None


def enabled() -> bool:
    return os.getenv("ROUTER", "decisions") == "decisions"


def min_confidence() -> float:
    return float(os.getenv("ROUTER_MIN_CONFIDENCE", "0.6"))


async def route(history: list[dict], text: str) -> dict | None:
    """{'destino', 'confianca', 'ms'} ou None quando a API falha (o chamador usa a Triagem)."""
    global _client
    _client = _client or AsyncOpenAI(timeout=8, max_retries=0)  # falhou: a Triagem responde, em vez de o cliente esperar
    started = time.perf_counter()
    try:
        decision = await _client.decisions.create(
            model=os.getenv("MODEL_DECISIONS", DEFAULT_MODEL), questions=[QUESTION],
            input=[*history[-CONTEXT_MESSAGES:], {"role": "user", "content": text}])
        answer = decision.answers[0]
    except Exception as exc:  # noqa: BLE001 - beta: qualquer falha vira fallback, nunca erro para o cliente
        log.warning("Decisions API indisponível (%s): usando a Triagem", type(exc).__name__)
        return None
    if answer.type != "choice":
        return None
    return {"destino": answer.choice, "confianca": round(answer.confidence, 2), "ms": round((time.perf_counter() - started) * 1000)}
