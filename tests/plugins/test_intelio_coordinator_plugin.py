"""The front-door prompt is off until intelio.coordinator is set, then re-injected once."""

import importlib.util
import sys
from pathlib import Path


def _load():
    plugin_dir = Path(__file__).resolve().parents[2] / "plugins" / "intelio-coordinator"
    spec = importlib.util.spec_from_file_location(
        "intelio_coordinator_under_test", plugin_dir / "__init__.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["intelio_coordinator_under_test"] = module
    spec.loader.exec_module(module)
    return module


def test_coordinator_is_off_until_enabled(monkeypatch):
    plugin = _load()
    plugin._PENDING.clear()
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"intelio": {"coordinator": False, "owner": "Hayden"}},
    )
    plugin._on_post_compaction(session_id="sess")
    assert plugin._on_pre_llm_call(session_id="sess") is None
    assert plugin.render_front_door() == ""


def test_coordinator_reinjects_the_front_door_once(monkeypatch):
    plugin = _load()
    plugin._PENDING.clear()
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"intelio": {"coordinator": True, "owner": "Hayden"}},
    )
    plugin._on_post_compaction(session_id="sess")
    first = plugin._on_pre_llm_call(session_id="sess")
    second = plugin._on_pre_llm_call(session_id="sess")
    assert "Hayden" in first["context"]
    assert "Do not send messages." in first["context"]
    assert second is None
