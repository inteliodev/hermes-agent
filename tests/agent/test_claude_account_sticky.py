"""A session stays on its Claude account until that account is rate-limited."""

import agent.claude_account_sticky as sticky


class _Entry:
    def __init__(self, entry_id, label="primary"):
        self.id = entry_id
        self.label = label


class _Pool:
    def __init__(self, available):
        self.available = set(available)
        self._entries = {entry_id: _Entry(entry_id, entry_id) for entry_id in available | {"benched"}}

    def entries(self):
        return list(self._entries.values())

    def reclaim(self, credential_id, model=None):
        if credential_id not in self.available:
            return None
        return self._entries[credential_id]

    def select(self, model=None):
        return self._entries["other"]


class _Agent:
    provider = "anthropic"
    session_id = "sess-1"
    model = "claude-opus"

    def __init__(self, current, pool):
        self._credential_pool_entry_id = current
        self._credential_pool = pool
        self.swapped = []

    def _swap_credential(self, entry):
        self.swapped.append(entry.id)
        self._credential_pool_entry_id = entry.id
        return True


def _reset(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    sticky._PINS.clear()
    sticky._LABELS.clear()


def test_hold_returns_to_the_pinned_account(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    pool = _Pool({"alpha", "beta"})
    agent = _Agent("alpha", pool)
    sticky.remember_current(agent)
    agent._credential_pool_entry_id = "beta"
    sticky.hold_session_account(agent)
    assert agent._credential_pool_entry_id == "alpha"
    assert sticky.account_label_for_session("sess-1") == "alpha"


def test_rate_limited_pin_is_not_reclaimed(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    pool = _Pool({"beta"})
    agent = _Agent("alpha", pool)
    sticky.remember_current(agent)
    agent._credential_pool_entry_id = "beta"
    pool.available.discard("alpha")
    sticky.hold_session_account(agent)
    assert agent.swapped == []
    assert agent._credential_pool_entry_id == "beta"


def test_revert_to_preferred_updates_the_pin(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    pool = _Pool({"fallback", "preferred", "other"})
    agent = _Agent("fallback", pool)
    sticky.remember_current(agent)
    agent._credential_pool_entry_id = "other"

    def revert(live):
        live._credential_pool_entry_id = "preferred"

    sticky.hold_then_remember(agent, revert)
    assert sticky.pinned_id(agent) == "preferred"


def test_reselect_keeps_the_current_account_when_it_is_usable(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    pool = _Pool({"alpha", "other"})
    agent = _Agent("alpha", pool)
    sticky.reselect_after_backoff(agent, pool, "claude-opus")
    assert agent.swapped == []
    assert sticky.pinned_id(agent) == "alpha"


def test_unsafe_label_is_not_stored(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    pool = _Pool({"alpha"})
    pool._entries["alpha"].label = "sk-live-secret"
    agent = _Agent("alpha", pool)
    sticky.remember_current(agent)
    assert sticky.account_label_for_session("sess-1") == "configured account"
