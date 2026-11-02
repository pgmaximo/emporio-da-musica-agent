"""Núcleo independente de canal: handle_message(telefone, texto) -> resposta. CLI, simulador e webhook chamam só isto."""
from __future__ import annotations

import logging
import os
import time

from agents import Agent, RunConfig, Runner
from agents.items import HandoffOutputItem

from . import data, guardrails
from .agents import build_agents
from .store import Store
from .tools import ChatContext

log = logging.getLogger("emporio")

MAX_INPUT_CHARS = 1000
HISTORY_MESSAGES = 20
SAFE_FALLBACK = ("Desculpa, não consegui confirmar essa informação com segurança agora. "
                 "Vou chamar um atendente para te ajudar, tudo bem? Retorno em até 24 horas úteis.")
CORRECTION = ("[verificação interna] Sua resposta citou valores em reais que NÃO vieram das ferramentas: {bad}. "
              "Reescreva a resposta usando somente valores retornados pelas ferramentas (chame-as de novo se precisar).")

_store: Store | None = None
_agent: Agent | None = None


class AgentUnavailable(Exception):
    """O modelo ou a API falhou; o chamador decide como avisar o cliente."""


def get_store() -> Store:
    global _store
    _store = _store or Store()
    return _store


def get_agent() -> Agent:
    global _agent
    _agent = _agent or build_agents()
    return _agent


def _trace_view(ctx: ChatContext) -> list[dict]:
    return [{"tool": t["tool"], "args": t["args"], "result": t["result"][:1500]} for t in ctx.trace]


async def _run(agent: Agent, history: list[dict], text: str, ctx: ChatContext, correction: str | None = None):
    items = [*history, {"role": "user", "content": text}]
    if correction:
        items.append({"role": "developer", "content": correction})
    config = RunConfig(workflow_name="Empório da Música · atendimento", trace_metadata={"phone_suffix": ctx.phone[-4:]},
                       tracing_disabled=os.getenv("TRACING_DISABLED") == "1")
    return await Runner.run(agent, items, context=ctx, max_turns=8, run_config=config)


async def handle_message(phone: str, text: str, *, store: Store | None = None, agent: Agent | None = None) -> dict:
    store, agent = store or get_store(), agent or get_agent()
    phone, text = data.normalize_phone(phone), (text or "").strip()[:MAX_INPUT_CHARS]
    if not text:
        raise ValueError("mensagem vazia")

    state = await store.state(phone)
    if state["status"] == "human":  # um atendente assumiu: o bot não responde
        await store.add(phone, "user", text)
        return {"reply": None, "state": "human", "handoff": state["handoff"]}

    started = time.perf_counter()
    customer = data.customer_by_phone(phone)
    ctx = ChatContext(phone=phone, customer=customer, user_text=text)
    history = await store.history(phone, HISTORY_MESSAGES)
    try:
        result = await _run(agent, history, text, ctx)
        reply, check = str(result.final_output or "").strip(), "ok"
        bad = guardrails.unverified_prices(reply, [t["result"] for t in ctx.trace], text)
        if bad:  # uma chance de corrigir; se persistir, não deixa um preço não verificado chegar ao cliente
            result = await _run(agent, history, text, ctx, CORRECTION.format(bad=", ".join(bad)))
            reply = str(result.final_output or "").strip()
            still = guardrails.unverified_prices(reply, [t["result"] for t in ctx.trace], text)
            check = "reescrito" if not still else "bloqueado"
            if still:
                log.warning("guardrail bloqueou resposta com valores %s", still)
                reply, ctx.human_handoff = SAFE_FALLBACK, {"motivo": "guardrail de preços", "resumo": f"Cliente: {text!r}. Valores não verificados: {still}"}
    except Exception as exc:  # noqa: BLE001 - qualquer falha de API/modelo vira indisponibilidade; nada é persistido
        log.exception("falha ao executar o agente")
        raise AgentUnavailable(str(exc)) from exc

    path = [agent.name]
    path += [i.target_agent.name for i in result.new_items if isinstance(i, HandoffOutputItem)]
    usage = result.context_wrapper.usage
    meta = {"agentes": path, "tools": _trace_view(ctx), "guardrail": check, "latencia_ms": round((time.perf_counter() - started) * 1000),
            "tokens": {"entrada": usage.input_tokens, "saida": usage.output_tokens, "total": usage.total_tokens, "chamadas": usage.requests},
            "cliente": customer["primeiro_nome"] if customer else None}
    await store.add(phone, "user", text)
    await store.add(phone, "assistant", reply, meta)
    if ctx.human_handoff:
        await store.set_state(phone, "human", ctx.human_handoff)
        meta["handoff"] = ctx.human_handoff
    return {"reply": reply, "state": "human" if ctx.human_handoff else "bot", "meta": meta, "handoff": ctx.human_handoff}
