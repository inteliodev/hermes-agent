"""The routines plugin blocks sends only while a routine is executing."""

import importlib.util
import sys
from pathlib import Path

from intelio.routines import routine_inflight


def _load():
    plugin_dir = Path(__file__).resolve().parents[2] / "plugins" / "intelio-routines"
    spec = importlib.util.spec_from_file_location(
        "intelio_routines_under_test", plugin_dir / "__init__.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["intelio_routines_under_test"] = module
    spec.loader.exec_module(module)
    return module


def test_disabled_plugin_does_not_block(monkeypatch):
    plugin = _load()
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"intelio": {"routines_enabled": False}},
    )
    with routine_inflight():
        assert plugin._on_pre_tool_call(tool_name="send_message") is None


def test_enabled_plugin_blocks_send_only_during_a_routine(monkeypatch):
    plugin = _load()
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"intelio": {"routines_enabled": True}},
    )
    assert plugin._on_pre_tool_call(tool_name="send_message") is None
    with routine_inflight():
        blocked = plugin._on_pre_tool_call(tool_name="send_message")
        assert blocked["action"] == "block"
        assert plugin._on_pre_tool_call(tool_name="read_file") is None
    assert plugin._on_pre_tool_call(tool_name="send_message") is None
