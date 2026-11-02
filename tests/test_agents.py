"""Orquestração ponta a ponta com ScriptedModel da SDK: sem rede, sem chave."""
import asyncio

from agents import handoff
from agents.testing import ScriptedModel, assistant_message, function_call

from app.core import service
from app.core.agents import build_agents

LUCAS = "(67) 99812-3456"
ANA = "(67) 99654-7821"


def go(coro):
    return asyncio.run(coro)


def agent_with(router_steps, specialist_steps):
    router, spec = ScriptedModel(router_steps), ScriptedModel(specialist_steps)
    return build_agents(router, spec), router, spec


def to(agent, name):
    return handoff(next(a for a in agent.handoffs if a.name == name)).tool_name


def test_price_question_routes_to_catalog_and_uses_tool(store):
    root = build_agents("x", "y")
    agent, _, _ = agent_with(
        [[function_call(to(root, "Catalogo"), {}, call_id="h1")]],
        [[function_call("buscar_produtos", {"consulta": "Takamine GD20"}, call_id="t1")],
         [assistant_message("O Takamine GD20 está R$ 2.199,00 e temos 5 em estoque!")]])
    out = go(service.handle_message("(67) 90000-0000", "Quanto custa o Takamine GD20?", store=store, agent=agent))
    assert out["meta"]["agentes"] == ["Triagem", "Catalogo"] and out["meta"]["guardrail"] == "ok"
    assert [t["tool"] for t in out["meta"]["tools"]] == ["buscar_produtos"]
    assert "R$ 2.199,00" in out["meta"]["tools"][0]["result"]


def test_invented_price_is_rewritten_once_then_passes(store):
    root = build_agents("x", "y")
    agent, _, _ = agent_with(
        [[function_call(to(root, "Catalogo"), {}, call_id="h1")], [function_call(to(root, "Catalogo"), {}, call_id="h2")]],
        [[assistant_message("Sai por R$ 1.500,00!")],                                    # inventado
         [function_call("buscar_produtos", {"consulta": "Takamine GD20"}, call_id="t1")],
         [assistant_message("Sai por R$ 2.199,00.")]])
    out = go(service.handle_message(LUCAS, "preço do GD20?", store=store, agent=agent))
    assert out["reply"] == "Sai por R$ 2.199,00." and out["meta"]["guardrail"] == "reescrito"
    assert out["meta"]["agentes"] == ["Triagem", "Catalogo"]  # o caminho da 1ª tentativa não se perde na reescrita


def test_invented_price_twice_is_blocked_and_handed_to_human(store):
    root = build_agents("x", "y")
    agent, _, _ = agent_with(
        [[function_call(to(root, "Catalogo"), {}, call_id="h1")], [function_call(to(root, "Catalogo"), {}, call_id="h2")]],
        [[assistant_message("R$ 1,00!")], [assistant_message("R$ 2,00!")]])
    out = go(service.handle_message(LUCAS, "preço?", store=store, agent=agent))
    assert out["reply"] == service.SAFE_FALLBACK and out["state"] == "human" and out["meta"]["guardrail"] == "bloqueado"


def test_unknown_number_gets_no_order_data(store):
    root = build_agents("x", "y")
    agent, _, _ = agent_with(
        [[function_call(to(root, "Pedidos"), {}, call_id="h1")]],
        [[function_call("meus_pedidos", {}, call_id="t1")], [assistant_message("Não consigo consultar pedidos deste número.")]])
    out = go(service.handle_message("(67) 90000-0000", "status do pedido 5?", store=store, agent=agent))
    result = out["meta"]["tools"][0]["result"]
    assert "erro" in result and "numero" not in result and "Rafael" not in result


def test_identified_customer_sees_only_own_orders(store):
    root = build_agents("x", "y")
    agent, _, _ = agent_with(
        [[function_call(to(root, "Pedidos"), {}, call_id="h1")]],
        [[function_call("meus_pedidos", {}, call_id="t1")], [assistant_message("Seu pedido 8 está a caminho.")]])
    out = go(service.handle_message(ANA, "cadê meu pedido?", store=store, agent=agent))
    result = out["meta"]["tools"][0]["result"]
    assert "BRJL5544332BR" in result and "BRAB1234567BR" not in result  # só o rastreio da Ana


def test_history_persists_per_phone_and_window_feeds_the_model(store):
    root = build_agents("x", "y")
    agent, router, _ = agent_with([[assistant_message("Oi! Como posso ajudar?")], [assistant_message("Tudo certo!")]], [])
    go(service.handle_message(LUCAS, "oi", store=store, agent=agent))
    go(service.handle_message(LUCAS, "valeu", store=store, agent=agent))
    seen = str(router.calls[-1].input)
    assert "Oi! Como posso ajudar?" in seen and "valeu" in seen
    assert [m["role"] for m in go(store.messages("67998123456"))] == ["user", "assistant", "user", "assistant"]
    assert go(store.messages("67996547821")) == []                                     # outro telefone, outro histórico


def test_human_handoff_silences_the_bot_until_resumed(store):
    root = build_agents("x", "y")
    agent, _, _ = agent_with(
        [[function_call(to(root, "Politicas"), {}, call_id="h1")]],
        [[function_call("transferir_para_humano", {"motivo": "reclamação", "resumo": "Cliente irritado com atraso"}, call_id="t1")],
         [assistant_message("Vou chamar um atendente!")]])
    out = go(service.handle_message(ANA, "quero falar com uma pessoa", store=store, agent=agent))
    assert out["state"] == "human" and out["handoff"]["motivo"] == "reclamação" and out["meta"]["agentes"] == ["Triagem", "Politicas"]
    again = go(service.handle_message(ANA, "alô?", store=store, agent=agent))        # sem steps no modelo: se chamasse, falharia
    assert again["reply"] is None and again["state"] == "human"


def test_triage_cannot_transfer_to_human_itself():
    triage = build_agents("x", "y")
    assert "transferir_para_humano" not in [t.name for t in triage.tools]
