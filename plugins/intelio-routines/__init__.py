"""Block sends while a routine draft is executing, and keep this profile's jobs on the ticker.

The cron jobs live in the active profile's cron store. ``intelio.digests`` and
``intelio.routines_enabled`` are each off until that profile sets them. Drafts
stay local.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _section() -> dict:
    try:
        from hermes_cli.config import load_config
        section = load_config().get("intelio") or {}
    except Exception:
        logger.debug("routines config unreadable", exc_info=True)
        return {}
    return section if isinstance(section, dict) else {}


def _enabled() -> bool:
    from intelio.routines import flag_on
    section = _section()
    return flag_on(section, "routines_enabled") or flag_on(section, "digests")


def _on_pre_tool_call(tool_name: str = "", **_: Any) -> Optional[dict[str, str]]:
    if not _enabled():
        return None
    from intelio.routines import send_blocked
    return send_blocked(tool_name)


def _sync(**_: Any) -> None:
    try:
        from intelio.routines_schedule import sync_profile
        sync_profile()
    except Exception:
        logger.warning("intelio routine sync failed", exc_info=True)


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("on_session_start", _sync)
    _sync()
