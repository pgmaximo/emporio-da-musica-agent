"""Persistência do histórico por telefone: SQLite local (padrão) ou Postgres via DATABASE_URL (Supabase em produção).

Guardamos só texto + metadados de UI, não os itens crus da SDK: tool calls órfãs quebram quando a janela de histórico corta
uma chamada no meio, e as tools são re-executadas a cada turno, então nada se perde.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, MetaData, String, Table, Text, delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

meta = MetaData()
messages = Table("messages", meta, Column("id", Integer, primary_key=True, autoincrement=True), Column("phone", String(20), index=True),
                 Column("role", String(12)), Column("text", Text), Column("ts", String(32)), Column("meta", Text))
conversations = Table("conversations", meta, Column("phone", String(20), primary_key=True), Column("status", String(12), default="bot"),
                      Column("handoff", Text), Column("updated_at", String(32)))
usage = Table("usage", meta, Column("day", String(10), primary_key=True), Column("key", String(64), primary_key=True), Column("count", Integer))


def _url() -> str:
    url = os.getenv("DATABASE_URL") or "sqlite+aiosqlite:///emporio.db"
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix):]
    return url


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, url: str | None = None):
        self.url = url or _url()
        postgres = self.url.startswith("postgresql")
        # Serverless + pooler do Supabase (pgbouncer): sem pool local e sem prepared statements.
        self.engine: AsyncEngine = create_async_engine(
            self.url, **({"poolclass": NullPool, "connect_args": {"statement_cache_size": 0}} if postgres else {}))
        self._ready = False

    async def init(self) -> None:
        if not self._ready:
            async with self.engine.begin() as conn:
                await conn.run_sync(meta.create_all)
            self._ready = True

    async def add(self, phone: str, role: str, text: str, info: dict | None = None) -> None:
        await self.init()
        async with self.engine.begin() as conn:
            await conn.execute(insert(messages).values(phone=phone, role=role, text=text, ts=_now(), meta=json.dumps(info or {}, ensure_ascii=False)))
            await self._touch(conn, phone)

    async def history(self, phone: str, limit: int = 20) -> list[dict]:
        """Últimas mensagens no formato de entrada do modelo (mais antigas primeiro)."""
        await self.init()
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(messages.c.role, messages.c.text).where(messages.c.phone == phone, messages.c.role.in_(("user", "assistant")))
                                       .order_by(messages.c.id.desc()).limit(limit))).all()
        return [{"role": r.role, "content": r.text} for r in reversed(rows)]

    async def messages(self, phone: str) -> list[dict]:
        await self.init()
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(messages).where(messages.c.phone == phone).order_by(messages.c.id))).all()
        return [{"id": r.id, "role": r.role, "text": r.text, "ts": r.ts, "meta": json.loads(r.meta or "{}")} for r in rows]

    async def state(self, phone: str) -> dict:
        await self.init()
        async with self.engine.connect() as conn:
            row = (await conn.execute(select(conversations).where(conversations.c.phone == phone))).first()
        return {"status": row.status if row else "bot", "handoff": json.loads(row.handoff) if row and row.handoff else None}

    async def set_state(self, phone: str, status: str, handoff: dict | None = None) -> None:
        await self.init()
        async with self.engine.begin() as conn:
            await self._touch(conn, phone)
            await conn.execute(update(conversations).where(conversations.c.phone == phone)
                               .values(status=status, handoff=json.dumps(handoff, ensure_ascii=False) if handoff else None))

    async def reset(self, phone: str) -> None:
        await self.init()
        async with self.engine.begin() as conn:
            await conn.execute(delete(messages).where(messages.c.phone == phone))
            await conn.execute(delete(conversations).where(conversations.c.phone == phone))

    async def hit(self, key: str, limit: int) -> bool:
        """Conta uma mensagem no limite diário de `key`. False quando o limite já foi atingido (a mensagem não é contada)."""
        await self.init()
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        upsert = pg_insert if self.url.startswith("postgresql") else sqlite_insert
        async with self.engine.begin() as conn:
            current = (await conn.execute(select(usage.c.count).where(usage.c.day == day, usage.c.key == key))).scalar() or 0
            if current >= limit:
                return False
            stmt = upsert(usage).values(day=day, key=key, count=1)
            await conn.execute(stmt.on_conflict_do_update(index_elements=["day", "key"], set_={"count": usage.c.count + 1}))
        return True

    @staticmethod
    async def _touch(conn, phone: str) -> None:
        upsert = pg_insert if str(conn.engine.url).startswith("postgresql") else sqlite_insert
        await conn.execute(upsert(conversations).values(phone=phone, status="bot", updated_at=_now())
                           .on_conflict_do_update(index_elements=["phone"], set_={"updated_at": _now()}))

    async def close(self) -> None:
        await self.engine.dispose()
