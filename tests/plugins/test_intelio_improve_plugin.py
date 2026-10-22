"""A correction: message becomes a pending proposal and is not applied."""

import importlib.util
import json
import sys
from pathlib import Path


def _load():
    plugin_dir = Path(__file__).resolve().parents[2] / "plugins" / "intelio-improve"
    spec = importlib.util.spec_from_file_location(
        "intelio_improve_under_test", plugin_dir / "__init__.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["intelio_improve_under_test"] = module
    spec.loader.exec_module(module)
    return module


def test_improve_off_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    plugin = _load()
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"intelio": {"improve": False}},
    )
    assert plugin._on_pre_llm_call(user_message="correction: remember desks") is None
    assert not (tmp_path / "intelio-improve" / "proposals.json").exists()


def test_correction_is_proposed_and_not_applied(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    plugin = _load()
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"intelio": {"improve": True}},
    )
    result = plugin._on_pre_llm_call(user_message="correction: remember desks")
    assert "not applied" in result["context"]
    assert "nothing was sent" in result["context"]
    saved = json.loads(
        (tmp_path / "intelio-improve" / "proposals.json").read_text(encoding="utf-8"),
    )
    assert saved["proposals"][0]["target"] == "memory"
    assert saved["proposals"][0]["applied"] is False
    assert plugin._on_pre_llm_call(user_message="hello") is None
