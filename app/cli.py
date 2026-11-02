"""CLI do atendimento. Exemplos:
    python -m app.cli --contatos                      # lista os telefones de teste
    python -m app.cli --phone "(67) 99812-3456"       # conversa como esse cliente
    python -m app.cli --phone 67999999999 --bastidores
"""
import argparse
import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from app import data, service  # noqa: E402  (depois do .env, que define modelo e chave)


def contacts() -> None:
    for c in data.list_customers():
        orders = f"pedidos {', '.join(c['pedidos'])}" if c["pedidos"] else "sem pedidos"
        print(f"{c['telefone']:<16} {c['nome']:<28} {c['cidade']:<14} {orders}")


async def chat(phone: str, show_backstage: bool) -> None:
    who = data.customer_by_phone(phone)
    print(f"\nConversando como {who['nome'] if who else 'número não cadastrado'} ({phone}). Comandos: /novo apaga o histórico, /sair encerra.\n")
    store = service.get_store()
    for m in (await store.messages(data.normalize_phone(phone)))[-6:]:
        print(("você> " if m["role"] == "user" else "loja> ") + m["text"])
    while True:
        try:
            text = (await asyncio.to_thread(input, "você> ")).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text == "/sair":
            break
        if text == "/novo":
            await store.reset(data.normalize_phone(phone))
            print("(histórico apagado)")
            continue
        if not text:
            continue
        try:
            out = await service.handle_message(phone, text)
        except service.AgentUnavailable as exc:
            print(f"(assistente indisponível: {exc})")
            continue
        print(f"loja> {out['reply'] or '(conversa com um atendente humano; o bot não responde)'}")
        if show_backstage and out.get("meta"):
            m = out["meta"]
            print(f"      [{' -> '.join(m['agentes'])} | tools: {[t['tool'] for t in m['tools']]} | guardrail: {m['guardrail']} | "
                  f"{m['tokens']['total']} tokens | {m['latencia_ms']} ms]")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Atendimento Empório da Música (CLI)")
    ap.add_argument("--phone", help="telefone do cliente, ex.: '(67) 99812-3456'")
    ap.add_argument("--contatos", action="store_true", help="lista os clientes de teste")
    ap.add_argument("--bastidores", action="store_true", help="mostra agente, tools e tokens de cada resposta")
    args = ap.parse_args()
    if args.contatos or not args.phone:
        contacts()
        return
    asyncio.run(chat(args.phone, args.bastidores))


if __name__ == "__main__":
    main()
