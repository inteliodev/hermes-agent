"""Route auxiliary classification and summarization onto a small Claude model.

Off unless ``claude_task_routing.enabled`` is true and ``small_model`` is set.
The session model stays the reasoning model. An explicit ``auxiliary.<task>``
model or provider wins. The route is always the existing Anthropic provider
(``anthropic_messages``); the credential pool supplies the key. No new provider
is introduced. Missing Claude credentials leave resolution unchanged.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

CLASSIFICATION_TASKS = frozenset({
    "approval",
    "title_generation",
    "monitor",
    "triage_specifier",
    "profile_describer",
    "memory_query_rewrite",
    "goal_judge",
})
SUMMARIZATION_TASKS = frozenset({
    "compression",
    "skills_hub",
    "curator",
})
_ROUTED_TASKS = CLASSIFICATION_TASKS | SUMMARIZATION_TASKS


def load_routing_config() -> dict:
    try:
        from hermes_cli.config import load_config
        section = load_config().get("claude_task_routing") or {}
        return section if isinstance(section, dict) else {}
    except Exception:
        logger.debug("claude task routing config unreadable", exc_info=True)
        return {}


def _explicit(value: Optional[str]) -> bool:
    text = str(value or "").strip().lower()
    return bool(text) and text != "auto"


def anthropic_credentials_available() -> bool:
    """True when this profile already has a Claude key or a non-empty pool."""
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN"):
        if (os.environ.get(name) or "").strip():
            return True
    try:
        from agent.credential_pool import load_pool
        pool = load_pool("anthropic")
        return bool(pool and pool.has_available())
    except Exception:
        logger.debug("anthropic credential probe failed", exc_info=True)
        return False


def finish_auto_resolution(
    task: Optional[str], *, explicit_model: Optional[str], explicit_provider: Optional[str],
    cfg_model: Optional[str], cfg_provider: Optional[str], resolved_model: Optional[str],
    resolved_api_mode: Optional[str],
) -> tuple[str, Optional[str], Optional[str], Optional[str], Optional[str]]:
    """The ``_resolve_task_provider_model`` auto return, or a small Claude route."""
    routed = _route(
        task, explicit_model=explicit_model, explicit_provider=explicit_provider,
        cfg_model=cfg_model, cfg_provider=cfg_provider,
    )
    if routed is not None:
        return routed
    return ("auto", resolved_model, None, None, resolved_api_mode)


def _route(
    task: Optional[str], *, explicit_model: Optional[str], explicit_provider: Optional[str],
    cfg_model: Optional[str], cfg_provider: Optional[str],
) -> Optional[tuple[str, str, None, None, str]]:
    if task not in _ROUTED_TASKS:
        return None
    if any(_explicit(value) for value in (explicit_model, explicit_provider, cfg_model, cfg_provider)):
        return None
    cfg = load_routing_config()
    if cfg.get("enabled") is not True and str(cfg.get("enabled")).strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    small = str(cfg.get("small_model") or "").strip()
    if not small or small.lower() == "auto":
        return None
    if not anthropic_credentials_available():
        return None
    logger.info("claude task routing: %s -> anthropic/%s", task, small)
    return ("anthropic", small, None, None, "anthropic_messages")
