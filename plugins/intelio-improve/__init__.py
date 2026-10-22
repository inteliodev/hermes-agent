"""Turn an explicit correction into a pending skill or memory proposal.

The log is ``$HERMES_HOME/intelio-improve/proposals.json`` and is written only
when that variable is already set. Approving and applying stay explicit.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    try:
        from hermes_cli.config import load_config
        section = load_config().get("intelio") or {}
    except Exception:
        logger.debug("improve config unreadable", exc_info=True)
        return False
    flag = section.get("improve")
    return flag is True or str(flag).strip().lower() in {"1", "true", "yes", "on"}


def _log_path() -> Optional[Path]:
    raw = os.environ.get("HERMES_HOME", "").strip()
    if not raw:
        return None
    return Path(raw).expanduser() / "intelio-improve" / "proposals.json"


def _on_pre_llm_call(user_message: str = "", **_: Any) -> Optional[dict[str, str]]:
    if not _enabled():
        return None
    path = _log_path()
    if path is None:
        return None
    from intelio.improve import ImproveLog, proposal_from_message
    proposal = proposal_from_message(str(user_message or ""), ImproveLog(path), now=time.time())
    if proposal is None:
        return None
    return {
        "context": (
            f"Proposed a {proposal['target']} update for Hayden to approve. "
            "It is not applied and nothing was sent."
        ),
    }


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
