"""Small-Claude auxiliary routing stays off unless config and credentials say otherwise."""

import agent.claude_task_routing as routing
from agent.auxiliary_client import _resolve_task_provider_model


def _enable(monkeypatch, *, enabled=True, small="claude-haiku-4-5", credentials=True):
    monkeypatch.setattr(routing, "load_routing_config", lambda: {
        "enabled": enabled, "small_model": small, "large_model": "claude-opus-4-5",
    })
    monkeypatch.setattr(routing, "anthropic_credentials_available", lambda: credentials)


def test_off_by_default(monkeypatch):
    _enable(monkeypatch, enabled=False)
    provider, model, _base, _key, _mode = _resolve_task_provider_model("title_generation")
    assert provider == "auto"
    assert model is None


def test_classification_and_summarization_use_the_small_model(monkeypatch):
    _enable(monkeypatch)
    for task in ("title_generation", "approval", "compression", "curator"):
        provider, model, base_url, api_key, api_mode = _resolve_task_provider_model(task)
        assert (provider, model, base_url, api_key, api_mode) == (
            "anthropic", "claude-haiku-4-5", None, None, "anthropic_messages",
        )


def test_explicit_task_model_wins(monkeypatch):
    _enable(monkeypatch)
    provider, model, *_rest = _resolve_task_provider_model("compression", model="claude-opus-explicit")
    assert (provider, model) == ("auto", "claude-opus-explicit")
    configured = routing.finish_auto_resolution(
        "compression", explicit_model=None, explicit_provider=None,
        cfg_model="claude-opus-from-config", cfg_provider="auto",
        resolved_model="claude-opus-from-config", resolved_api_mode=None,
    )
    assert configured[0] == "auto"
    assert configured[1] == "claude-opus-from-config"


def test_reasoning_task_is_not_rerouted(monkeypatch):
    _enable(monkeypatch)
    provider, model, *_rest = _resolve_task_provider_model("vision")
    assert provider == "auto"
    assert model is None


def test_missing_credentials_leave_resolution_alone(monkeypatch):
    _enable(monkeypatch, credentials=False)
    provider, model, *_rest = _resolve_task_provider_model("compression")
    assert (provider, model) == ("auto", None)
