import os

os.environ["TRACING_DISABLED"] = "1"
os.environ["EMPORIO_NOW"] = "2026-02-19T10:30:00"
os.environ.setdefault("OPENAI_API_KEY", "sk-test")  # os testes usam ScriptedModel; nenhuma chamada de rede

import pytest

from app.core.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
