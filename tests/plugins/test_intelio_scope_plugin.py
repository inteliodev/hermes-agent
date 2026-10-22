"""Client scope is re-injected after compaction, and Jarvis stays read-only."""

import importlib.util
import sys
from pathlib import Path

import pytest


def _load():
    plugin_dir = Path(__file__).resolve().parents[2] / "plugins" / "intelio-scope"
    spec = importlib.util.spec_from_file_location("intelio_scope_under_test", plugin_dir / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["intelio_scope_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def scope(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    module = _load()
    module._PENDING.clear()
    monkeypatch.setattr(module, "load_intelio_config", lambda: {
        "client": "HHP", "scope_rules": "Use the HHP workspace only.", "jarvis_readonly": False,
    })
    return module


def test_unknown_client_renders_nothing(scope, monkeypatch):
    monkeypatch.setattr(scope, "load_intelio_config", lambda: {"client": "other"})
    assert scope.render_block() == ""


def test_block_names_the_client_and_the_others(scope):
    text = scope.render_block()
    assert "Active client profile: HHP." in text
    assert "intelio" in text and "PRC" in text and "Alignment" in text and "ARLP" in text
    assert "Use the HHP workspace only." in text


def test_post_compaction_reinjects_once(scope):
    scope._on_post_compaction(session_id="sess")
    first = scope._on_pre_llm_call(session_id="sess")
    second = scope._on_pre_llm_call(session_id="sess")
    assert first["context"].startswith("Active client profile: HHP.")
    assert second is None


def test_jarvis_blocks_mutating_tools_and_allows_reads(scope, monkeypatch):
    monkeypatch.setattr(scope, "load_intelio_config", lambda: {"client": "intelio", "jarvis_readonly": True})
    blocked = scope._on_pre_tool_call(tool_name="write_file")
    assert blocked["action"] == "block"
    assert scope._on_pre_tool_call(tool_name="read_file") is None
    assert "Jarvis is read-only" in scope.render_block()


def test_profile_named_jarvis_is_read_only(scope, monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.profiles.get_active_profile_name", lambda: "jarvis",
    )
    assert scope.jarvis_readonly({"client": "ARLP", "jarvis_readonly": False}) is True


def test_blocked_tool_set_matches_the_grader(scope):
    seeds_path = Path(__file__).resolve().parents[2] / "evals" / "intelio_grader" / "seeds.py"
    spec = importlib.util.spec_from_file_location("intelio_grader_seeds_under_test", seeds_path)
    seeds = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seeds)
    assert scope.JARVIS_BLOCKED_TOOLS == seeds.JARVIS_BLOCKED_TOOLS


def test_register_wires_the_section(scope):
    class Ctx:
        def __init__(self):
            self.hooks = {}
            self.section = None

        def register_hook(self, name, callback):
            self.hooks[name] = callback

        def register_system_prompt_section(self, section_id, content, **kwargs):
            self.section = (section_id, content, kwargs)

    ctx = Ctx()
    scope.register(ctx)
    assert set(ctx.hooks) == {"post_compaction", "pre_llm_call", "pre_tool_call"}
    assert ctx.section[0] == "intelio.client_scope"
    assert "HHP" in ctx.section[1]({})
