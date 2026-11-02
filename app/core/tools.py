"""Tools dos agentes. Cada uma é uma função fina sobre data.py/rules.py e devolve JSON.

Toda tool registra (nome, args, resultado) em ctx.context.trace: alimenta o painel "Bastidores"
e o guardrail de preços (app/core/guardrails.py).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal, get_args

from agents import RunContextWrapper, function_tool

from . import clock, data, rules

Category = Literal["Guitarras", "Baixos", "Baterias e Percussão", "Teclados e Pianos", "Violões",
                   "Instrumentos de Sopro (Madeiras)", "Instrumentos de Sopro (Metais)", "Cordas Orquestrais", "Ukuleles"]
CATEGORIES = get_args(Category)

Topic = Literal["sobre_a_loja", "horario", "pagamento", "trocas_e_devolucoes", "frete_e_entregas", "promocoes",
                "atendimento_whatsapp", "garantia", "privacidade"]
TOPIC_SECTION = dict(zip(get_args(Topic), "123456789"))  # ordem das seções 1-9 do manual
TOPIC_NOTES = {
    "sobre_a_loja": "Contatos oficiais: WhatsApp/telefone (67) 3341-4444 e contato@emporiodamusica.com.br. "
                    "O catálogo online tem menos itens do que o texto sugere: confirme sempre com buscar_produtos.",
    "pagamento": "Para valores, use calcular_pagamento. Para parcelamento vale a regra por faixas da seção 3.1.",
    "atendimento_whatsapp": "Número oficial para o cliente: (67) 3341-4444 (o manual cita outro número nesta seção; ignore).",
}


@dataclass
class ChatContext:
    phone: str
    customer: dict | None
    user_text: str = ""
    trace: list[dict] = field(default_factory=list)
    human_handoff: dict | None = None


def _out(ctx: RunContextWrapper[ChatContext], name: str, args: dict, payload: dict) -> str:
    text = json.dumps(payload, ensure_ascii=False)
    ctx.context.trace.append({"tool": name, "args": args, "result": text})
    return text


@function_tool
def buscar_produtos(ctx: RunContextWrapper[ChatContext], consulta: str = "", categoria: Category | None = None,
                    preco_min: float | None = None, preco_max: float | None = None) -> str:
    """Busca no catálogo. Use `consulta` para um modelo/marca específico (ex.: 'Takamine GD20') e os filtros para navegar
    (ex.: categoria='Violões', preco_max=1000). Sem `consulta` lista só itens à venda, do mais barato ao mais caro.
    Com `consulta` inclui itens indisponíveis, descontinuados ou esgotados (com alternativas)."""
    args = {"consulta": consulta, "categoria": categoria, "preco_min": preco_min, "preco_max": preco_max}
    return _out(ctx, "buscar_produtos", args, data.search_products(consulta, categoria, preco_min, preco_max))


@function_tool
def detalhes_produto(ctx: RunContextWrapper[ChatContext], produto_id: int) -> str:
    """Descrição completa e especificações de um produto, a partir do id retornado por buscar_produtos."""
    p = data.get_product(produto_id)
    payload = data.product_view(p, detail=True) if p else {"erro": "produto não encontrado"}
    return _out(ctx, "detalhes_produto", {"produto_id": produto_id}, payload)


@function_tool
def calcular_pagamento(ctx: RunContextWrapper[ChatContext], produto_id: int, quantidade: int = 1) -> str:
    """Valor total, preço no PIX e parcelas possíveis no cartão de crédito para um produto. NUNCA faça essas contas de cabeça."""
    p = data.get_product(produto_id)
    if not p:
        return _out(ctx, "calcular_pagamento", {"produto_id": produto_id}, {"erro": "produto não encontrado"})
    promo = data.product_view(p).get("preco_promocional") is not None
    unit = data.current_price(p)
    total = rules.money(unit) * quantidade
    payload = {"produto": p.name, "quantidade": quantidade, "disponibilidade": data.availability(p),
               "total_cartao_ou_boleto": rules.brl(total), "em_promocao": promo,
               **rules.pix_price(total, promo), "parcelas_sem_juros": rules.installment_options(total),
               "combinar_formas_de_pagamento": "permitido para compras acima de R$ 2.000,00"}
    return _out(ctx, "calcular_pagamento", {"produto_id": produto_id, "quantidade": quantidade}, payload)


@function_tool
def calcular_frete(ctx: RunContextWrapper[ChatContext], valor_pedido: float, cidade: str | None = None) -> str:
    """Frete e prazo. Se a cidade não for informada, usa a do cadastro do cliente; se não houver nenhuma, peça a cidade."""
    city = cidade or (ctx.context.customer or {}).get("cidade")
    if not city:
        return _out(ctx, "calcular_frete", {"valor_pedido": valor_pedido}, {"erro": "cidade desconhecida: pergunte ao cliente"})
    return _out(ctx, "calcular_frete", {"valor_pedido": valor_pedido, "cidade": city}, rules.shipping(valor_pedido, city))


@function_tool
def meus_pedidos(ctx: RunContextWrapper[ChatContext]) -> str:
    """Pedidos do cliente que está conversando agora (identificado pelo número de WhatsApp). Não aceita parâmetros de
    propósito: só é possível ver os próprios pedidos."""
    customer = ctx.context.customer
    if not customer:
        return _out(ctx, "meus_pedidos", {}, {"erro": "cliente não identificado: este número não está no cadastro da loja"})
    return _out(ctx, "meus_pedidos", {}, {"cliente": customer["primeiro_nome"], "pedidos": data.orders_for_customer(customer["id"])})


@function_tool
def avaliar_devolucao(ctx: RunContextWrapper[ChatContext], numero_pedido: int) -> str:
    """Aplica as regras de devolução, arrependimento, troca e garantia a um pedido do cliente."""
    customer = ctx.context.customer
    order = data.raw_order(customer["id"], numero_pedido) if customer else None
    if not order:
        payload = {"erro": "não encontrei esse pedido na conta deste número"}
    else:
        payload = {"pedido": data.order_view(order),
                   "avaliacao": rules.return_assessment(order["status"], order["order_date"], order["estimated_delivery"], clock.today())}
    return _out(ctx, "avaliar_devolucao", {"numero_pedido": numero_pedido}, payload)


@lru_cache(maxsize=1)
def _sections() -> dict[str, str]:
    text = (data.DATA_DIR / "politicas.md").read_text(encoding="utf-8")
    return {part.split(".", 1)[0]: "## " + part.strip() for part in re.split(r"^## ", text, flags=re.M)[1:]}


@function_tool
def consultar_politica(ctx: RunContextWrapper[ChatContext], topico: Topic) -> str:
    """Texto do manual da loja sobre um tópico: endereço/contatos, horários, pagamento, trocas e devoluções, frete, promoções, garantia."""
    payload = {"topico": topico, "texto": _sections()[TOPIC_SECTION[topico]]}
    if topico in TOPIC_NOTES:
        payload["nota"] = TOPIC_NOTES[topico]
    return _out(ctx, "consultar_politica", {"topico": topico}, payload)


@function_tool
def status_loja_agora(ctx: RunContextWrapper[ChatContext]) -> str:
    """Se a loja está aberta neste momento, data/hora atuais e próxima abertura."""
    return _out(ctx, "status_loja_agora", {}, rules.store_status(clock.now()))


@function_tool
def transferir_para_humano(ctx: RunContextWrapper[ChatContext], motivo: str, resumo: str) -> str:
    """Passa a conversa para um atendente humano. Use quando o cliente pedir, quando houver reclamação, quando faltar informação
    para responder com segurança ou quando a política não cobrir o caso (ex.: cancelar pedido não enviado). `resumo` é para o
    atendente: o que o cliente quer, o que já foi dito e os números de pedido envolvidos."""
    ctx.context.human_handoff = {"motivo": motivo, "resumo": resumo}
    return _out(ctx, "transferir_para_humano", {"motivo": motivo, "resumo": resumo},
                {"ok": True, "prazo_de_retorno": "até 24 horas úteis; avise o cliente que um atendente vai assumir a conversa"})
