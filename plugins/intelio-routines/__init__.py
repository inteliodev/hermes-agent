"""Block sends while a routine draft is executing.

The store and the cron schedule live in ``intelio.routines``. This hook is the
approval gate: ``send_message`` during a routine returns a block. Enabling the
plugin without ``intelio.routines_enabled`` does nothing.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    try:
        from hermes_cli.config import load_config
        section = load_config().get("intelio") or {}
    except Exception:
        logger.debug("routines config unreadable", exc_info=True)
        return False
    flag = section.get("routines_enabled")
    return flag is True or str(flag).strip().lower() in {"1", "true", "yes", "on"}


def _on_pre_tool_call(tool_name: str = "", **_: Any) -> Optional[dict[str, str]]:
    if not _enabled():
        return None
    from intelio.routines import send_blocked
    return send_blocked(tool_name)


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
