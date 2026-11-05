from agents.testing import ScriptedModel, assistant_message
from fastapi.testclient import TestClient

from app import main
from app import service
from app.agents import build_agents


def client(store, replies):
    service._store = store
    service._agent = build_agents(ScriptedModel([[assistant_message(r)] for r in replies]), ScriptedModel())
    return TestClient(main.app)


def test_contacts_and_static_page(store):
    with client(store, []) as c:
        contacts = c.get("/api/contacts").json()
        assert [x["rotulo"] for x in contacts["contacts"]][-1] == "não cadastrado" and len(contacts["contacts"]) == 8
        assert contacts["relogio_simulado"] is True and "19/02/2026" in contacts["relogio"]
        assert "Simulador de canal" in c.get("/").text


def test_message_roundtrip_persists_and_reset_clears(store):
    with client(store, ["Oi, Letícia! Como posso ajudar?"]) as c:
        phone = "(67) 99789-0123"
        out = c.post("/api/messages", json={"phone": phone, "text": "oi"}).json()
        assert out["reply"].startswith("Oi, Letícia") and out["meta"]["cliente"] == "Letícia"
        assert [m["role"] for m in c.get(f"/api/conversations/{phone}").json()["messages"]] == ["user", "assistant"]
        c.post(f"/api/conversations/{phone}/reset")
        assert c.get(f"/api/conversations/{phone}").json()["messages"] == []


def test_daily_limit_returns_429(store, monkeypatch):
    monkeypatch.setattr(main, "LIMIT_PHONE", 1)
    with client(store, ["ok"]) as c:
        body = {"phone": "(67) 90000-0000", "text": "oi"}
        assert c.post("/api/messages", json=body).status_code == 200
        assert c.post("/api/messages", json=body).status_code == 429


def test_invalid_input_is_rejected(store):
    with client(store, []) as c:
        assert c.post("/api/messages", json={"phone": "123", "text": "oi"}).status_code == 422
        assert c.post("/api/messages", json={"phone": "(67) 90000-0000", "text": "x" * 1001}).status_code == 422
