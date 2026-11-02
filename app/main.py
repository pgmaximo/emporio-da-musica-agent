"""API do simulador de canal (e, em milestone posterior, do webhook do WhatsApp). Serve também a página em public/."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from app.core import clock, data, rules, service  # noqa: E402

log = logging.getLogger("emporio")
PUBLIC = Path(__file__).resolve().parents[1] / "public"
# Limites diários da demo pública (protegem a chave da OpenAI); configuráveis por env.
LIMIT_PHONE, LIMIT_IP, LIMIT_GLOBAL = (int(os.getenv(k, d)) for k, d in (("LIMIT_PHONE", 40), ("LIMIT_IP", 120), ("LIMIT_GLOBAL", 1500)))
# Um cliente por situação de pedido, para o avaliador ver cada comportamento sem decorar telefones.
DEMO_ORDERS = [(7, "pedido entregue"), (8, "pedido a caminho"), (9, "pedido confirmado"), (13, "pedido pendente"),
               (17, "pedido cancelado"), (6, "entregue em Dourados"), (1, "dois pedidos")]
NEW_NUMBER = {"id": 0, "nome": "Número novo", "telefone": "(67) 90000-0000", "rotulo": "não cadastrado", "cidade": ""}


@asynccontextmanager
async def lifespan(_: FastAPI):
    await service.get_store().init()
    yield


app = FastAPI(title="Empório da Música · Simulador de canal", lifespan=lifespan)


class Incoming(BaseModel):
    phone: str = Field(max_length=32)
    text: str = Field(max_length=service.MAX_INPUT_CHARS)


def _ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    return (forwarded.split(",")[0].strip() if forwarded else request.client.host if request.client else "?")


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/contacts")
async def contacts():
    by_id = {c["id"]: c for c in data.list_customers()}
    order_owner = {int(o): c["id"] for c in by_id.values() for o in c["pedidos"]}
    out = [{**by_id[order_owner[o]], "rotulo": label} for o, label in DEMO_ORDERS] + [NEW_NUMBER]
    return {"contacts": out, "relogio": rules.store_status(clock.now())["agora"], "relogio_simulado": bool(os.getenv("EMPORIO_NOW"))}


@app.get("/api/conversations/{phone}")
async def conversation(phone: str):
    key = data.normalize_phone(phone)
    store = service.get_store()
    return {"messages": await store.messages(key), **await store.state(key)}


@app.post("/api/messages")
async def message(body: Incoming, request: Request):
    key, store = data.normalize_phone(body.phone), service.get_store()
    for bucket, limit in ((f"phone:{key}", LIMIT_PHONE), (f"ip:{_ip(request)}", LIMIT_IP), ("global", LIMIT_GLOBAL)):
        if not await store.hit(bucket, limit):
            raise HTTPException(429, "Limite diário de mensagens da demo atingido. Volte amanhã ou rode localmente (veja o README).")
    if len(key) < 10 or not body.text.strip():
        raise HTTPException(422, "telefone ou mensagem inválidos")
    try:
        return await service.handle_message(key, body.text)
    except service.AgentUnavailable:
        raise HTTPException(503, "O assistente está indisponível no momento. Tente novamente em instantes.")


@app.post("/api/conversations/{phone}/reset")
async def reset(phone: str):
    await service.get_store().reset(data.normalize_phone(phone))
    return {"ok": True}


@app.post("/api/conversations/{phone}/resume")
async def resume(phone: str):
    """Demo: devolve a conversa ao assistente depois de uma transferência para humano."""
    await service.get_store().set_state(data.normalize_phone(phone), "bot")
    return {"ok": True}


# Por último: as rotas /api acima têm prioridade. Na Vercel, public/ é servido pela CDN (a doc pede para não montá-lo); o mount vale só localmente.
if PUBLIC.exists() and not os.getenv("VERCEL"):
    app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="public")
