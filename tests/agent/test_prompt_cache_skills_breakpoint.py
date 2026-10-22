"""Skills sit in their own Anthropic cache span; the volatile tail stays unmarked."""

from agent.prompt_caching import (
    _count_cache_markers,
    apply_anthropic_cache_control,
    build_prompt_cache_plan,
    strip_anthropic_cache_control,
)
from agent.system_prompt import prefix_through_skills

MARKER = {"type": "ephemeral"}


def _system(static: str, skills_span: str, volatile: str) -> str:
    return static + skills_span + volatile


def test_skills_prefix_marks_stable_and_skills_and_leaves_volatile_unmarked():
    static = "stable identity"
    skills_span = "\n\nproject context\n\n# Skills\n- demo"
    volatile = "\n\nmemory and timestamp"
    full = _system(static, skills_span, volatile)
    skills_prefix = static + skills_span
    messages = apply_anthropic_cache_control(
        [{"role": "system", "content": full}, {"role": "user", "content": "hi"}],
        native_anthropic=True,
        static_system_prefix=static,
        skills_prefix=skills_prefix,
    )
    parts = messages[0]["content"]
    assert [part["text"] for part in parts] == [static, skills_span, volatile]
    assert parts[0]["cache_control"] == MARKER
    assert parts[1]["cache_control"] == MARKER
    assert "cache_control" not in parts[2]
    assert _count_cache_markers(messages, []) <= 4
    restored = strip_anthropic_cache_control(messages)
    assert restored[0]["content"] == full


def test_native_tool_plan_keeps_four_markers_when_skills_are_split():
    static = "stable prefix"
    skills_span = "\n\nskills index"
    volatile = "\n\nvolatile tail"
    full = static + skills_span + volatile
    tools = [
        {"type": "function", "function": {"name": f"tool_{i:02d}", "parameters": {}}}
        for i in range(4)
    ]
    history = [
        {"role": "system", "content": full},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "function": {"name": "tool_00", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "a", "content": "result"},
    ]
    plan = build_prompt_cache_plan(
        history, tools, native_anthropic=True, static_system_prefix=static,
        direct_native_tool_cache=True, skills_prefix=static + skills_span,
    )
    parts = plan.messages[0]["content"]
    assert "cache_control" not in parts[2]
    assert "cache_control" in plan.tools[-1]
    assert _count_cache_markers(plan.messages, plan.tools) <= 4
    assert _count_cache_markers(plan.messages, plan.tools) == 4


def test_missing_skills_prefix_keeps_the_two_part_layout():
    messages = apply_anthropic_cache_control(
        [{"role": "system", "content": "stable prefix\nvolatile suffix"}],
        native_anthropic=True,
        static_system_prefix="stable prefix",
    )
    parts = messages[0]["content"]
    assert len(parts) == 2
    assert parts[0]["cache_control"] == MARKER
    assert parts[1]["cache_control"] == MARKER


def test_prefix_through_skills_stops_before_the_volatile_tail():
    prefix = prefix_through_skills("stable", "context", "  skills index  ")
    assert prefix == "stable\n\ncontext\n\nskills index"
    assert prefix_through_skills("stable", "", "") is None
