"""Client names shared by the fork plugins and the eval grader.

Desk codes are synthetic. They match ``evals/intelio_grader/seeds.py``.
"""

from __future__ import annotations

CLIENTS = ("intelio", "PRC", "Alignment", "HHP", "ARLP")

DESK = {
    "intelio": "1842",
    "PRC": "2753",
    "Alignment": "3064",
    "HHP": "4175",
    "ARLP": "5286",
}

SECTIONS = ("decisions", "sops", "templates", "contacts", "policies")


def pack_token(client: str) -> str:
    return f"PACKTOKEN_{client}"
