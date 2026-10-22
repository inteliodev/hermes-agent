"""Official OpenAI cache keys follow the profile and the session, not the prompt text."""

from agent.transports import get_transport


def _kwargs(transport, *, instructions, session_id, base_url, profile="hhp"):
    from hermes_cli import profiles
    original = profiles.get_active_profile_name
    profiles.get_active_profile_name = lambda: profile
    try:
        return transport.build_kwargs(
            model="gpt-cache-model",
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": "hello"},
            ],
            tools=[{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
            session_id=session_id,
            base_url=base_url,
        )
    finally:
        profiles.get_active_profile_name = original


def test_openai_key_ignores_instruction_edits_and_changes_with_session(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    transport = get_transport("chat_completions")
    base = "https://api.openai.com/v1"
    first = _kwargs(transport, instructions="You are stable.", session_id="sess-a", base_url=base)
    edited = _kwargs(transport, instructions="You are different.", session_id="sess-a", base_url=base)
    other = _kwargs(transport, instructions="You are stable.", session_id="sess-b", base_url=base)
    other_profile = _kwargs(
        transport, instructions="You are stable.", session_id="sess-a", base_url=base, profile="arlp",
    )
    assert first["prompt_cache_key"] == edited["prompt_cache_key"]
    assert first["prompt_cache_key"] != other["prompt_cache_key"]
    assert first["prompt_cache_key"] != other_profile["prompt_cache_key"]
    assert first["prompt_cache_key"].startswith("pck_")
    assert len(first["prompt_cache_key"]) <= 64


def test_non_openai_host_stays_content_addressed():
    transport = get_transport("chat_completions")
    base = "https://cache-capable.example/v1"
    first = transport.build_kwargs(
        model="cache-model",
        messages=[{"role": "system", "content": "one"}, {"role": "user", "content": "hi"}],
        tools=None,
        session_id="sess-a",
        base_url=base,
        supports_prompt_cache_key=True,
    )
    edited = transport.build_kwargs(
        model="cache-model",
        messages=[{"role": "system", "content": "two"}, {"role": "user", "content": "hi"}],
        tools=None,
        session_id="sess-a",
        base_url=base,
        supports_prompt_cache_key=True,
    )
    assert first["prompt_cache_key"] != edited["prompt_cache_key"]


def test_openai_without_a_session_falls_back_to_the_content_key():
    transport = get_transport("chat_completions")
    first = transport.build_kwargs(
        model="gpt-cache-model",
        messages=[{"role": "system", "content": "one"}, {"role": "user", "content": "hi"}],
        tools=None,
        session_id="",
        base_url="https://api.openai.com/v1",
    )
    edited = transport.build_kwargs(
        model="gpt-cache-model",
        messages=[{"role": "system", "content": "two"}, {"role": "user", "content": "hi"}],
        tools=None,
        session_id="",
        base_url="https://api.openai.com/v1",
    )
    assert first["prompt_cache_key"] != edited["prompt_cache_key"]
