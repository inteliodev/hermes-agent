"""Front-door prompt. Re-injected once after compaction, like client scope.

The system-prompt section is rebuilt with the volatile tier. ``post_compaction``
only sets a flag; the next ``pre_llm_call`` returns the text as user context.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_PENDING: set[tuple[str, str]] = set()


def _section_config() -> dict:
    try:
        from hermes_cli.config import load_config
        section = load_config().get("intelio") or {}
        return section if isinstance(section, dict) else {}
    except Exception:
        logger.debug("coordinator config unreadable", exc_info=True)
        return {}


def _enabled(section: Optional[dict] = None) -> bool:
    cfg = _section_config() if section is None else section
    flag = cfg.get("coordinator")
    return flag is True or str(flag).strip().lower() in {"1", "true", "yes", "on"}


def _owner(section: dict) -> str:
    name = str(section.get("owner") or "Hayden").strip()
    return name or "Hayden"


def render_front_door(section: Optional[dict] = None) -> str:
    cfg = _section_config() if section is None else section
    if not _enabled(cfg):
        return ""
    from intelio.coordinator import front_door_text
    return front_door_text(_owner(cfg))


def _scope_key(session_id: str) -> tuple[str, str]:
    from hermes_constants import hermes_home_key
    return (hermes_home_key(), session_id or "")


def _on_post_compaction(session_id: str = "", **_: Any) -> None:
    if not _enabled():
        return
    with _LOCK:
        _PENDING.add(_scope_key(session_id or ""))


def _on_pre_llm_call(session_id: str = "", **_: Any) -> Optional[dict[str, str]]:
    text = render_front_door()
    if not text:
        return None
    key = _scope_key(session_id or "")
    with _LOCK:
        pending = key in _PENDING
        if pending:
            _PENDING.discard(key)
    if not pending:
        return None
    return {"context": text}


def register(ctx) -> None:
    ctx.register_hook("post_compaction", _on_post_compaction)
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_system_prompt_section(
        "intelio.coordinator",
        lambda _session=None: render_front_door(),
        position="after_memory",
        max_chars=2000,
    )
