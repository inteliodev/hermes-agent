"""Every API turn logs cache read and write counts, including zeros."""

import logging
from types import SimpleNamespace


def test_cache_token_line_is_always_emitted(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from run_agent import AIAgent
    from agent import turn_usage

    agent = AIAgent(
        api_key="k", base_url="https://inference-api.nousresearch.com/v1", provider="nous",
        api_mode="chat_completions", model="anthropic/claude-fable-5.1", session_id="t",
        platform="cli", quiet_mode=True, skip_context_files=True, skip_memory=True,
        save_trajectories=False, enabled_toolsets=["file"],
    )
    usage = SimpleNamespace(
        prompt_tokens=10, completion_tokens=2, total_tokens=12,
        prompt_tokens_details=SimpleNamespace(cached_tokens=0, cache_write_tokens=0),
        completion_tokens_details=None,
    )
    try:
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="agent.turn_usage"):
            turn_usage.record_response_usage(
                agent, SimpleNamespace(usage=usage, id=None, model=agent.model),
                messages=[{"role": "user", "content": "hi"}], api_call_count=1,
                api_duration=0.2, compression_attempts=0, max_compression_attempts=3,
            )
        lines = [record.getMessage() for record in caplog.records]
    finally:
        agent.close()
    summary = next(line for line in lines if line.startswith("API call #"))
    detail = next(line for line in lines if "cache_read_tokens=" in line)
    assert "write=" not in summary
    assert detail == "API call #1 cache tokens: cache_read_tokens=0 cache_write_tokens=0"
