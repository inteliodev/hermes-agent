"""Opt-in Langfuse traces for one Hermes turn.

``post_api_request`` accumulates usage and latency (it has no final text).
``post_llm_call`` emits one trace and one generation. Tags: client, profile,
model, account label. Metadata: tools, latency, cost. Message content is
redacted when the turn matches finance, email, or texts, unless
``langfuse.redact_categories`` is an explicit empty list.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from datetime import datetime, timezone, UTC
from typing import Any, Optional

from .client import post_ingestion_fail_open
from .redact import matched_categories, redact_text

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_TURNS: dict[tuple[str, str], dict[str, Any]] = {}
_DEFAULT_REDACT = ("finance", "email", "texts")


def load_langfuse_config() -> dict:
    try:
        from hermes_cli.config import load_config
        section = load_config().get("langfuse") or {}
        return section if isinstance(section, dict) else {}
    except Exception:
        logger.debug("langfuse config unreadable", exc_info=True)
        return {}


def tracing_enabled(cfg: Optional[dict] = None) -> bool:
    section = cfg if cfg is not None else load_langfuse_config()
    flag = section.get("enabled")
    turned_on = flag is True or str(flag).strip().lower() in {"1", "true", "yes", "on"}
    if not turned_on:
        return False
    return bool((os.environ.get("LANGFUSE_PUBLIC_KEY") or "").strip()) and bool(
        (os.environ.get("LANGFUSE_SECRET_KEY") or "").strip()
    )


def _host(cfg: dict) -> str:
    override = (os.environ.get("LANGFUSE_HOST") or "").strip()
    if override:
        return override
    return str(cfg.get("host") or "http://127.0.0.1:3000").strip() or "http://127.0.0.1:3000"


def _redact_categories(cfg: dict) -> list[str]:
    raw = cfg.get("redact_categories", list(_DEFAULT_REDACT))
    if not isinstance(raw, list):
        return list(_DEFAULT_REDACT)
    return [str(item) for item in raw if str(item) in _DEFAULT_REDACT]


def _tool_names(value: Any) -> list[str]:
    calls = getattr(value, "tool_calls", None)
    if calls is None and isinstance(value, dict):
        calls = value.get("tool_calls")
    names: list[str] = []
    for call in calls or []:
        function = getattr(call, "function", None)
        if function is None and isinstance(call, dict):
            function = call.get("function")
        name = function.get("name") if isinstance(function, dict) else getattr(function, "name", None)
        if isinstance(name, str) and name:
            names.append(name)
    return names


def _names_from_history(history: Any) -> list[str]:
    names: list[str] = []
    for message in history or []:
        names.extend(_tool_names(message))
    return names


def _on_post_api_request(**kwargs: Any) -> None:
    if not tracing_enabled():
        return
    key = (str(kwargs.get("session_id") or ""), str(kwargs.get("turn_id") or ""))
    usage = kwargs.get("usage") if isinstance(kwargs.get("usage"), dict) else {}
    with _LOCK:
        slot = _TURNS.setdefault(key, {"calls": [], "tools": []})
        slot["calls"].append({
            "model": str(kwargs.get("model") or ""),
            "provider": str(kwargs.get("provider") or ""),
            "latency": kwargs.get("api_duration"),
            "usage": usage,
        })
        slot["tools"].extend(_tool_names(kwargs.get("assistant_message")))
    return


def _sum_usage(calls: list[dict]) -> dict[str, int]:
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    for call in calls:
        usage = call.get("usage") or {}
        for field in totals:
            try:
                totals[field] += int(usage.get(field) or 0)
            except (TypeError, ValueError):
                continue
    return totals


def _latency(calls: list[dict]) -> float:
    total = 0.0
    for call in calls:
        try:
            total += float(call.get("latency") or 0)
        except (TypeError, ValueError):
            continue
    return total


def _cost(model: str, provider: str, usage: dict[str, int]) -> Optional[float]:
    try:
        from agent.usage_pricing import CanonicalUsage, estimate_usage_cost
        result = estimate_usage_cost(
            model,
            CanonicalUsage(
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                cache_read_tokens=usage["cache_read_tokens"],
                cache_write_tokens=usage["cache_write_tokens"],
            ),
            provider=provider or None,
            api_key="",
        )
        amount = getattr(result, "amount_usd", None)
        return None if amount is None else float(amount)
    except Exception:
        logger.debug("langfuse cost estimate failed", exc_info=True)
        return None


def _client_tag() -> str:
    try:
        from hermes_cli.config import load_config
        raw = str((load_config().get("intelio") or {}).get("client") or "").strip()
    except Exception:
        return ""
    if raw in {"intelio", "PRC", "Alignment", "HHP", "ARLP"}:
        return raw
    return ""


def _profile_name() -> str:
    try:
        from hermes_cli.profiles import get_active_profile_name
        return get_active_profile_name()
    except Exception:
        return ""


def _account_label(session_id: str) -> str:
    try:
        from agent.claude_account_sticky import account_label_for_session
        return account_label_for_session(session_id)
    except Exception:
        return ""


def _on_post_llm_call(**kwargs: Any) -> None:
    cfg = load_langfuse_config()
    if not tracing_enabled(cfg):
        return
    session_id = str(kwargs.get("session_id") or "")
    key = (session_id, str(kwargs.get("turn_id") or ""))
    with _LOCK:
        slot = _TURNS.pop(key, {"calls": [], "tools": []})
    calls = list(slot.get("calls") or [])
    tools = list(dict.fromkeys([*slot.get("tools", []), *_names_from_history(kwargs.get("conversation_history"))]))
    model = str(kwargs.get("model") or (calls[-1]["model"] if calls else "") or "")
    provider = str(calls[-1]["provider"] if calls else "")
    usage = _sum_usage(calls)
    user_text = kwargs.get("user_message")
    assistant_text = kwargs.get("assistant_response")
    user_body = user_text if isinstance(user_text, str) else ""
    assistant_body = assistant_text if isinstance(assistant_text, str) else ""
    categories = matched_categories(
        f"{user_body}\n{assistant_body}", tools, _redact_categories(cfg),
    )
    client = _client_tag()
    profile = _profile_name()
    account = _account_label(session_id)
    latency = _latency(calls)
    cost = _cost(model, provider, usage)
    now = datetime.now(UTC).isoformat()
    trace_id = uuid.uuid4().hex
    tags = [f"profile:{profile or 'unknown'}", f"model:{model or 'unknown'}"]
    if client:
        tags.append(f"client:{client}")
    if account:
        tags.append(f"account:{account}")
    metadata = {
        "client": client or None,
        "profile": profile or None,
        "model": model or None,
        "account_label": account or None,
        "tools": tools,
        "latency_seconds": latency,
        "cost_usd": cost,
        "redacted": categories,
    }
    batch = [
        {
            "id": uuid.uuid4().hex,
            "type": "trace-create",
            "timestamp": now,
            "body": {
                "id": trace_id,
                "name": "hermes-turn",
                "sessionId": session_id,
                "userId": profile or None,
                "tags": tags,
                "input": redact_text(user_body, categories),
                "output": redact_text(assistant_body, categories),
                "metadata": metadata,
            },
        },
        {
            "id": uuid.uuid4().hex,
            "type": "generation-create",
            "timestamp": now,
            "body": {
                "id": uuid.uuid4().hex,
                "traceId": trace_id,
                "name": model or "generation",
                "model": model or None,
                "usage": {
                    "input": usage["input_tokens"],
                    "output": usage["output_tokens"],
                    "total": usage["input_tokens"] + usage["output_tokens"],
                    "unit": "TOKENS",
                },
                "metadata": metadata,
            },
        },
    ]
    post_ingestion_fail_open(
        _host(cfg),
        os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
        os.environ.get("LANGFUSE_SECRET_KEY", ""),
        batch,
    )
    return


def register(ctx) -> None:
    ctx.register_hook("post_api_request", _on_post_api_request)
    ctx.register_hook("post_llm_call", _on_post_llm_call)
