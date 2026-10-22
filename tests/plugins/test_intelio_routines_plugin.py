"""The routines plugin blocks sends only while a routine is executing."""

import importlib.util
import json
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


class _Hooks:
    def __init__(self):
        self.hooks = {}

    def register_hook(self, name, callback):
        self.hooks.setdefault(name, []).append(callback)


def test_digest_opt_in_blocks_send_only_during_a_routine(monkeypatch):
    plugin = _load()
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"intelio": {"routines_enabled": False, "digests": True}},
    )
    assert plugin._on_pre_tool_call(tool_name="send_message") is None
    with routine_inflight():
        blocked = plugin._on_pre_tool_call(tool_name="send_message")
        assert blocked["action"] == "block"
    assert plugin._on_pre_tool_call(tool_name="send_message") is None


def test_register_installs_a_digest_on_session_start_and_pauses_when_off(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    state = {"intelio": {"client": "PRC", "routines_enabled": False, "digests": False}}
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: state)
    plugin = _load()
    ctx = _Hooks()
    plugin.register(ctx)
    assert set(ctx.hooks) >= {"pre_tool_call", "on_session_start"}
    assert not (home / "cron" / "jobs.json").exists()

    state["intelio"]["digests"] = True
    ctx.hooks["on_session_start"][0]()
    jobs = json.loads((home / "cron" / "jobs.json").read_text(encoding="utf-8"))["jobs"]
    assert [job["name"] for job in jobs] == ["intelio-routine-morning-digest-PRC"]
    assert jobs[0]["deliver"] == "local"
    assert jobs[0]["failure_deliver"] == "local"
    assert jobs[0]["no_agent"] is True

    state["intelio"]["digests"] = False
    ctx.hooks["on_session_start"][0]()
    jobs = json.loads((home / "cron" / "jobs.json").read_text(encoding="utf-8"))["jobs"]
    assert jobs[0]["enabled"] is False
    assert jobs[0]["state"] == "paused"
    assert not jobs[0].get("origin")
