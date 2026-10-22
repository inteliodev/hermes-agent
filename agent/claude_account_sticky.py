"""Keep one Hermes session on the Claude account it already warmed.

Anthropic prompt caches are per API key. ``CredentialPool.select`` is a strategy
(round-robin / least-used / first), so a gateway that rebuilds the agent each
request would hop healthy accounts and cold-start the cache. The pin is
process-local, keyed by profile home + session id. It never outranks a cooldown:
a rate-limited pin is not reclaimed, and the existing preferred-account revert
still runs after the hold so a recovered primary is taken back. The pin then
follows whichever account the turn actually kept.

No credentials are stored here. Labels are the pool's display label only.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_PINS: dict[tuple[str, str], str] = {}
_LABELS: dict[tuple[str, str], str] = {}
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,30}$")


def _scope_key(agent: Any) -> Optional[tuple[str, str]]:
    session_id = getattr(agent, "session_id", None)
    if not isinstance(session_id, str) or not session_id.strip():
        return None
    from hermes_constants import hermes_home_key

    return (hermes_home_key(), session_id)


def _anthropic(agent: Any) -> bool:
    return (getattr(agent, "provider", "") or "").strip().lower() == "anthropic"


def _safe_label(entry: Any) -> str:
    raw = str(getattr(entry, "label", "") or "").strip()
    if not raw or raw.lower().startswith(("sk-", "pk-")) or _LABEL_RE.match(raw) is None:
        return "configured account"
    return raw


def remember_current(agent: Any) -> None:
    """Pin the session to the Claude entry it is on right now."""
    if not _anthropic(agent):
        return
    key = _scope_key(agent)
    entry_id = getattr(agent, "_credential_pool_entry_id", None)
    if key is None or not isinstance(entry_id, str) or not entry_id:
        return
    pool = getattr(agent, "_credential_pool", None)
    label = "configured account"
    if pool is not None:
        try:
            current = next((e for e in pool.entries() if getattr(e, "id", None) == entry_id), None)
        except Exception:
            current = None
        if current is not None:
            label = _safe_label(current)
    with _LOCK:
        _PINS[key] = entry_id
        _LABELS[key] = label


def pinned_id(agent: Any) -> Optional[str]:
    key = _scope_key(agent)
    if key is None:
        return None
    with _LOCK:
        return _PINS.get(key)


def account_label_for_session(session_id: str) -> str:
    """Display label of the Claude account pinned to this session, or empty."""
    if not isinstance(session_id, str) or not session_id.strip():
        return ""
    from hermes_constants import hermes_home_key

    with _LOCK:
        return _LABELS.get((hermes_home_key(), session_id), "")


def _reclaim(pool: Any, credential_id: str, model: Any) -> Any:
    try:
        return pool.reclaim(credential_id, model=model)
    except Exception:
        logger.debug("Claude account reclaim failed for %s", credential_id, exc_info=True)
        return None


def hold_session_account(agent: Any) -> None:
    """Swap back to the pinned Claude entry when that entry is off cooldown.

    A no-op when there is no pin, the session is already on it, or the pin is
    still rate-limited. Does not remember: the caller remembers after preferred
    revert so a recovered primary becomes the new pin.
    """
    if not _anthropic(agent):
        return
    pin = pinned_id(agent)
    if not pin or getattr(agent, "_credential_pool_entry_id", None) == pin:
        return
    pool = getattr(agent, "_credential_pool", None)
    if pool is None:
        return
    entry = _reclaim(pool, pin, getattr(agent, "model", None))
    if entry is None:
        return
    if agent._swap_credential(entry) is not False:
        logger.info("Held session on Claude account %s", _safe_label(entry))


def hold_then_remember(agent: Any, revert: Callable[[Any], None]) -> None:
    """Hold the pin, run preferred-account revert, then remember the result.

    Order matters. Hold undoes a fresh ``select()`` hop. Revert then moves to
    the preferred account when its cooldown has lifted. Remember runs last so
    the pin follows that revert instead of pulling the next turn back.
    """
    hold_session_account(agent)
    revert(agent)
    remember_current(agent)


def select_sticky(agent: Any, pool: Any, model: Any) -> Any:
    """Current entry if it is usable, else the pin, else ``pool.select``."""
    seen: list[str] = []
    for candidate in (getattr(agent, "_credential_pool_entry_id", None), pinned_id(agent)):
        if not isinstance(candidate, str) or not candidate or candidate in seen:
            continue
        seen.append(candidate)
        entry = _reclaim(pool, candidate, model)
        if entry is not None:
            return entry
    try:
        return pool.select(model=model)
    except Exception:
        logger.debug("Claude pool select failed", exc_info=True)
        return None


def reselect_after_backoff(agent: Any, pool: Any, model: Any) -> None:
    """After an all-pool wait, stay on a usable current/pinned account when one exists."""
    entry = select_sticky(agent, pool, model)
    if entry is None:
        return
    if getattr(agent, "_credential_pool_entry_id", None) != getattr(entry, "id", None):
        if agent._swap_credential(entry) is False:
            return
    remember_current(agent)


def note_swap(agent: Any, *, swapped: bool, provider: str) -> None:
    """Remember a Claude rotation that actually took effect."""
    if swapped and (provider or "").strip().lower() == "anthropic":
        remember_current(agent)
