"""Relógio do agente. EMPORIO_NOW (ISO) fixa a data/hora em demos e testes; sem ele, usa o relógio real."""
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Campo_Grande")


def now() -> datetime:
    fixed = os.getenv("EMPORIO_NOW")
    if fixed:
        return datetime.fromisoformat(fixed).replace(tzinfo=TZ)
    return datetime.now(TZ)


def today() -> date:
    return now().date()
