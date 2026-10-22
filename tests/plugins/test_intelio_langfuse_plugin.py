"""Langfuse tracing is off by default, tagged, and redacts finance, email, and texts."""

import importlib.util
import json
import sys
import types
from pathlib import Path


def _load():
    plugin_dir = Path(__file__).resolve().parents[2] / "plugins" / "intelio-langfuse"
    package = "hermes_plugins.intelio_langfuse_under_test"
    if "hermes_plugins" not in sys.modules:
        namespace = types.ModuleType("hermes_plugins")
        namespace.__path__ = []
        sys.modules["hermes_plugins"] = namespace
    spec = importlib.util.spec_from_file_location(
        package, plugin_dir / "__init__.py", submodule_search_locations=[str(plugin_dir)],
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = package
    module.__path__ = [str(plugin_dir)]
    sys.modules[package] = module
    spec.loader.exec_module(module)
    return module


def _enable(module, monkeypatch):
    module._TURNS.clear()
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setattr(module, "load_langfuse_config", lambda: {
        "enabled": True, "host": "http://127.0.0.1:3000",
        "redact_categories": ["finance", "email", "texts"],
    })
    monkeypatch.setattr(module, "_client_tag", lambda: "PRC")
    monkeypatch.setattr(module, "_profile_name", lambda: "prc")
    monkeypatch.setattr(module, "_account_label", lambda _session_id: "desk")
    monkeypatch.setattr(module, "_cost", lambda *args, **kwargs: 0.02)


def test_disabled_without_keys_does_not_post(monkeypatch):
    module = _load()
    module._TURNS.clear()
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setattr(module, "load_langfuse_config", lambda: {"enabled": True})
    posted = []
    monkeypatch.setattr(module, "post_ingestion_fail_open", lambda *args: posted.append(args))
    module._on_post_llm_call(session_id="s", turn_id="t", user_message="hi", assistant_response="ok")
    assert posted == []


def test_trace_tags_and_redacts_email(monkeypatch):
    module = _load()
    _enable(module, monkeypatch)
    posted = {}

    def capture(host, public_key, secret_key, batch):
        posted["host"] = host
        posted["batch"] = batch
        assert public_key == "pk-test"
        assert secret_key == "sk-test"

    monkeypatch.setattr(module, "post_ingestion_fail_open", capture)
    module._on_post_api_request(
        session_id="s", turn_id="t", model="claude-opus", provider="anthropic",
        api_duration=0.5,
        usage={"input_tokens": 3, "output_tokens": 4, "cache_read_tokens": 1, "cache_write_tokens": 2},
        assistant_message={"tool_calls": [{"function": {"name": "gmail_send"}}]},
    )
    module._on_post_llm_call(
        session_id="s", turn_id="t", model="claude-opus", platform="cli",
        user_message="mail ada@example.test about the invoice",
        assistant_response="drafted",
        conversation_history=[{"role": "assistant", "tool_calls": [{"function": {"name": "read_file"}}]}],
    )
    body = json.dumps(posted["batch"])
    assert "ada@example.test" not in body
    assert "invoice" not in body
    assert posted["host"] == "http://127.0.0.1:3000"
    tags = posted["batch"][0]["body"]["tags"]
    assert "client:PRC" in tags
    assert "profile:prc" in tags
    assert "model:claude-opus" in tags
    assert "account:desk" in tags
    metadata = posted["batch"][0]["body"]["metadata"]
    assert metadata["tools"] == ["gmail_send", "read_file"]
    assert metadata["latency_seconds"] == 0.5
    assert metadata["cost_usd"] == 0.02
    assert posted["batch"][1]["body"]["usage"]["input"] == 3


def test_plain_text_is_kept_when_no_category_matches(monkeypatch):
    module = _load()
    _enable(module, monkeypatch)
    posted = {}
    monkeypatch.setattr(module, "post_ingestion_fail_open", lambda *args: posted.setdefault("batch", args[-1]))
    module._on_post_llm_call(
        session_id="s", turn_id="t2", model="claude-opus",
        user_message="What is the desk code?", assistant_response="1842",
        conversation_history=[],
    )
    assert posted["batch"][0]["body"]["input"] == "What is the desk code?"
    assert posted["batch"][0]["body"]["output"] == "1842"
