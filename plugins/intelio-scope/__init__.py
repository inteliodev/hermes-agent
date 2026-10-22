"""Client scope for the intelio fork.

The system-prompt section rides the volatile tier, so it is rebuilt with the
rest of the prompt after compaction. ``post_compaction`` is observer-only, so
the same block is also queued and returned from the next ``pre_llm_call`` as
user-message context (never spliced into the cached system prompt mid-turn).
Jarvis read-only blocks mutating tools when ``intelio.jarvis_readonly`` is set
or the active profile is named ``jarvis``.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

CLIENTS = ("intelio", "PRC", "Alignment", "HHP", "ARLP")
_CLIENT_SET = frozenset(CLIENTS)

# Names the model must not call while Jarvis is read-only. ``cronjob`` is the
# toolset name; the callable tool is ``cronjob_manage``. ``send_message`` is
# blocked even when this process has no such tool.
JARVIS_BLOCKED_TOOLS = frozenset({
    "write_file",
    "patch",
    "skill_manage",
    "send_message",
    "cronjob",
    "cronjob_manage",
    "execute_code",
    "terminal",
    "process_manage",
    "delegate_task",
})

_LOCK = threading.Lock()
_PENDING: set[tuple[str, str]] = set()


def load_intelio_config() -> dict:
    try:
        from hermes_cli.config import load_config
        section = load_config().get("intelio") or {}
        return section if isinstance(section, dict) else {}
    except Exception:
        logger.debug("intelio config unreadable", exc_info=True)
        return {}


def active_client(cfg: Optional[dict] = None) -> str:
    raw = str((cfg if cfg is not None else load_intelio_config()).get("client") or "").strip()
    return raw if raw in _CLIENT_SET else ""


def jarvis_readonly(cfg: Optional[dict] = None) -> bool:
    section = cfg if cfg is not None else load_intelio_config()
    flag = section.get("jarvis_readonly")
    if flag is True or str(flag).strip().lower() in {"1", "true", "yes", "on"}:
        return True
    try:
        from hermes_cli.profiles import get_active_profile_name
        return get_active_profile_name().strip().lower() == "jarvis"
    except Exception:
        logger.debug("profile name unreadable for jarvis check", exc_info=True)
        return False


def render_block(cfg: Optional[dict] = None) -> str:
    """The scope block, or empty when no known client is configured."""
    section = cfg if cfg is not None else load_intelio_config()
    client = active_client(section)
    if not client:
        return ""
    others = ", ".join(name for name in CLIENTS if name != client)
    lines = [
        f"Active client profile: {client}.",
        f"Scope rule: stay inside {client}. Do not read, quote, store, or act on data "
        f"belonging to another client ({others}).",
    ]
    extra = str(section.get("scope_rules") or "").strip()
    if extra:
        lines.append(extra[:2000])
    if jarvis_readonly(section):
        lines.append(
            "Jarvis is read-only. Do not call tools that write files, execute commands, "
            "send messages, schedule jobs, manage skills, or delegate work. "
            "Answer from what you can read."
        )
    pack = _knowledge_excerpt(section, client)
    if pack:
        lines.append(pack)
    return "\n".join(lines)


def _knowledge_enabled(section: dict) -> bool:
    """Missing key auto-loads. An explicit false leaves the pack out."""
    if "knowledge" not in section:
        return True
    flag = section.get("knowledge")
    if flag is False:
        return False
    return str(flag).strip().lower() not in {"0", "false", "no", "off"}


def _knowledge_excerpt(section: dict, client: str) -> str:
    if not _knowledge_enabled(section):
        return ""
    try:
        from intelio.knowledge import excerpt_for_client
        return excerpt_for_client(client)
    except Exception:
        logger.debug("knowledge pack unreadable", exc_info=True)
        return ""


def _scope_key(session_id: str) -> tuple[str, str]:
    from hermes_constants import hermes_home_key
    return (hermes_home_key(), session_id or "")


def _on_post_compaction(session_id: str = "", **_: Any) -> None:
    """Observer. The return value is ignored; the next pre_llm_call re-injects."""
    if not active_client():
        return
    with _LOCK:
        _PENDING.add(_scope_key(session_id or ""))
    return


def _on_pre_llm_call(session_id: str = "", **_: Any) -> Optional[dict[str, str]]:
    block = render_block()
    if not block:
        return None
    key = _scope_key(session_id or "")
    with _LOCK:
        pending = key in _PENDING
        if pending:
            _PENDING.discard(key)
    if not pending:
        return None
    return {"context": block}


def _on_pre_tool_call(tool_name: str = "", **_: Any) -> Optional[dict[str, str]]:
    if tool_name not in JARVIS_BLOCKED_TOOLS or not jarvis_readonly():
        return None
    return {
        "action": "block",
        "message": (
            f"Jarvis is read-only and cannot call {tool_name}. "
            "Read and answer; do not write, execute, send, schedule, or delegate."
        ),
    }


def _section(_session: Any = None) -> str:
    return render_block()


def register(ctx) -> None:
    ctx.register_hook("post_compaction", _on_post_compaction)
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    # Scope rules are capped at 2000 characters and the pack excerpt at 1500,
    # so the section cap has to leave room for both plus the Jarvis paragraph.
    ctx.register_system_prompt_section(
        "intelio.client_scope", _section, position="after_memory", max_chars=6000,
    )
