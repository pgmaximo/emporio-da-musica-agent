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
EMPTY_FALLBACK = "Desculpa, tive um problema para responder agora. Pode repetir a sua pergunta?"
CORRECTIONS = {
    "empty": "Sua resposta veio vazia. Responda ao cliente.",
    "price": "Sua resposta citou valores em reais que NÃO vieram das ferramentas ({bad}). Use somente valores retornados pelas ferramentas (chame-as de novo se precisar).",
    "transfer": "Você disse que passou o cliente para um atendente, mas não chamou transferir_para_humano. Chame a ferramenta agora, com motivo e resumo, e então responda.",
}

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
                       tracing_disabled=os.getenv("TRACING_DISABLED") == "1", trace_include_sensitive_data=False)  # LGPD: o traço não leva o texto das conversas
    return await Runner.run(agent, items, context=ctx, max_turns=8, run_config=config)


def _problems(reply: str, ctx: ChatContext, text: str) -> dict[str, str]:
    found = {}
    if not reply:
        found["empty"] = ""
    elif bad := guardrails.unverified_prices(reply, [t["result"] for t in ctx.trace], text):
        found["price"] = ", ".join(bad)
    if reply and guardrails.claims_transfer(reply) and not ctx.human_handoff:
        found["transfer"] = ""
    return found


def _resolve(problems: dict, reply: str, ctx: ChatContext, text: str) -> tuple[str, str]:
    """Última linha de defesa depois de duas tentativas. Preço sem origem tem prioridade, depois resposta vazia."""
    if "price" in problems:
        ctx.human_handoff = {"motivo": "guardrail de preços", "resumo": f"Cliente: {text!r}. Valores não verificados: {problems['price']}"}
        return SAFE_FALLBACK, "bloqueado"
    if "empty" in problems:
        return EMPTY_FALLBACK, "bloqueado"
    # O modelo prometeu a transferência e não a fez: fazemos, e a promessa passa a ser verdadeira.
    ctx.human_handoff = {"motivo": "cliente pediu um atendente", "resumo": f"Cliente: {text!r}. A transferência foi concluída pelo sistema."}
    return reply, "corrigido"


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
    runs, correction = [], None  # uma execução por tentativa: os bastidores somam caminho e tokens de todas
    try:
        for attempt in (0, 1):
            runs.append(await _run(agent, history, text, ctx, correction))
            reply = str(runs[-1].final_output or "").strip()
            problems = _problems(reply, ctx, text)
            if not problems:
                check = "ok" if attempt == 0 else "reescrito"
                break
            correction = "[verificação interna] " + " ".join(CORRECTIONS[k].format(bad=problems.get("price", "")) for k in problems)
        else:  # a segunda tentativa também falhou: nada de preço sem origem, resposta vazia ou transferência falsa chega ao cliente
            log.warning("verificação falhou: %s", sorted(problems))
            reply, check = _resolve(problems, reply, ctx, text)
    except Exception as exc:  # noqa: BLE001 - qualquer falha de API/modelo vira indisponibilidade; nada é persistido
        log.exception("falha ao executar o agente")
        raise AgentUnavailable(str(exc)) from exc

    path = list(dict.fromkeys([agent.name] + [i.target_agent.name for r in runs for i in r.new_items if isinstance(i, HandoffOutputItem)]))
    usages = [r.context_wrapper.usage for r in runs]
    meta = {"agentes": path, "tools": _trace_view(ctx), "guardrail": check, "latencia_ms": round((time.perf_counter() - started) * 1000),
            "tokens": {"entrada": sum(u.input_tokens for u in usages), "saida": sum(u.output_tokens for u in usages),
                       "total": sum(u.total_tokens for u in usages), "chamadas": sum(u.requests for u in usages)},
            "cliente": customer["primeiro_nome"] if customer else None}
    await store.add(phone, "user", text)
    await store.add(phone, "assistant", reply, meta)
    if ctx.human_handoff:
        await store.set_state(phone, "human", ctx.human_handoff)
        meta["handoff"] = ctx.human_handoff
    return {"reply": reply, "state": "human" if ctx.human_handoff else "bot", "meta": meta, "handoff": ctx.human_handoff}
