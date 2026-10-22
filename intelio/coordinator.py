"""Front door that routes one task to one client specialist.

The router is deterministic so a test can pin it. A task that names two
clients is refused. A task that names none stays with the owner and loads no
pack. The handoff context is that one client's pack.
"""

from __future__ import annotations

from typing import Optional

from intelio.clients import CLIENTS
from intelio.knowledge import excerpt_for_client, foreign_markers, load_pack

_SPECIALISTS = (
    ("sops", ("sop", "procedure", "how do we")),
    ("policies", ("policy", "policies", "allowed", "forbidden")),
    ("contacts", ("contact", "who handles", "phone", "email address")),
    ("templates", ("template", "status note")),
    ("decisions", ("decide", "decision", "approve")),
)


def _named_clients(task: str) -> list[str]:
    lowered = task.lower()
    return [client for client in CLIENTS if client.lower() in lowered]


def _specialist(task: str) -> str:
    lowered = task.lower()
    for name, words in _SPECIALISTS:
        if any(word in lowered for word in words):
            return name
    return "general"


def route(task: str) -> dict:
    named = _named_clients(task)
    if len(named) > 1:
        return {
            "ok": False,
            "client": None,
            "specialist": None,
            "error": "name one client; refusing to mix clients",
        }
    return {
        "ok": True,
        "client": named[0] if named else None,
        "specialist": _specialist(task),
        "error": "",
    }


def handoff(task: str, *, owner: str = "Hayden") -> dict:
    """Route ``task`` and attach only that client's pack. Results name ``owner``."""
    decision = route(task)
    base = {
        "ok": decision["ok"],
        "client": decision["client"],
        "specialist": decision["specialist"],
        "for": owner,
        "error": decision["error"],
        "pack_version": None,
        "context": "",
    }
    if not decision["ok"] or not decision["client"]:
        base["result"] = f"Hold for {owner}. No client pack loaded."
        return base
    client = decision["client"]
    pack = load_pack(client)
    context = excerpt_for_client(client)
    for marker in foreign_markers(client):
        if marker in context:
            raise RuntimeError(f"{client} handoff included {marker}")
    base["pack_version"] = pack["version"]
    base["context"] = context
    base["result"] = (
        f"Hand back to {owner}: {client} / {decision['specialist']}. Not sent."
    )
    return base


def front_door_text(owner: Optional[str] = None) -> str:
    who = owner or "Hayden"
    names = ", ".join(CLIENTS)
    return (
        f"You are {who}'s front door. Route each task to one of {names} and one "
        f"specialist (decisions, sops, templates, contacts, policies, general). "
        f"Never load another client's knowledge pack. Hand the result back to {who}. "
        f"Do not send messages."
    )
