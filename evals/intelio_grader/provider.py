"""Promptfoo provider that replays the seed reference. No model call."""

from __future__ import annotations


def call_api(prompt, options, context):
    variables = (context or {}).get("vars") or {}
    return {"output": variables.get("reference") or ""}
