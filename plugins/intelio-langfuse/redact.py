"""Redact turn text when the turn touches finance, email, or texts."""

from __future__ import annotations

import re
from typing import Iterable

_TEXT_SIGNALS = {
    "finance": re.compile(
        r"\b(invoice|routing number|bank account|iban|ssn|tax return|account number)\b",
        re.IGNORECASE,
    ),
    "email": re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    "texts": re.compile(r"\b(sms|text message|imessage)\b", re.IGNORECASE),
}
_TOOL_MARKERS = {
    "finance": ("invoice", "stripe", "plaid", "quickbooks", "bank"),
    "email": ("gmail", "email", "mail"),
    "texts": ("sms", "imessage", "text_message"),
}


def matched_categories(text: str, tool_names: Iterable[str], enabled: Iterable[str]) -> list[str]:
    """Categories in ``enabled`` that this turn actually touches, in stable order."""
    wanted = [name for name in ("finance", "email", "texts") if name in set(enabled)]
    blob = text or ""
    tools = [name.lower() for name in tool_names if isinstance(name, str)]
    hit: list[str] = []
    for category in wanted:
        pattern = _TEXT_SIGNALS[category]
        markers = _TOOL_MARKERS[category]
        if pattern.search(blob) or any(any(marker in tool for marker in markers) for tool in tools):
            hit.append(category)
    return hit


def redact_text(text: str, categories: list[str]) -> str:
    if not categories:
        body = text or ""
        return body if len(body) <= 4000 else body[:4000] + "…"
    return "[redacted:" + ",".join(categories) + "]"
