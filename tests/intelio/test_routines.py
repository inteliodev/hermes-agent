"""Routines draft work, retry, and survive a new process reading the same files."""

import pytest

from intelio.clients import CLIENTS
from intelio.routines import (
    DIGEST_SCHEDULE,
    WEEKLY_SCHEDULE,
    RoutineStore,
    parse_cron,
    routine_inflight,
    send_blocked,
)


NOW = 1_700_000_000.0


def test_morning_digests_and_the_weekly_report_use_cron_schedules(tmp_path):
    store = RoutineStore(tmp_path / "routines.json")
    created = store.ensure_morning_digests(NOW)
    assert {item["client"] for item in created} == set(CLIENTS)
    assert store.ensure_morning_digests(NOW) == []
    for item in created:
        assert item["id"] == f"morning-digest-{item['client']}"
        assert item["action"] == "draft_digest"
        assert item["auto_send"] is False
        assert item["approval_required"] is True
        spec = parse_cron(item["trigger"]["schedule"])
        assert spec["kind"] == "cron"
        assert item["trigger"]["schedule"] == DIGEST_SCHEDULE
        assert item["next_run_at"] > NOW

    weekly = store.ensure_weekly_report(NOW)
    assert weekly["id"] == "weekly-improvement"
    assert weekly["client"] == ""
    assert weekly["action"] == "draft_report"
    assert parse_cron(WEEKLY_SCHEDULE)["kind"] == "cron"
    assert store.ensure_weekly_report(NOW)["id"] == weekly["id"]

    reloaded = RoutineStore(tmp_path / "routines.json")
    assert {item["id"] for item in reloaded.routines} == {item["id"] for item in store.routines}


def test_events_match_one_client_and_the_bank_threshold(tmp_path):
    store = RoutineStore(tmp_path / "routines.json")
    store.add(
        routine_id="mail", name="mail from X", client="PRC",
        trigger={"kind": "event", "event_type": "email", "from": "X"},
        action="draft_reply", now=NOW,
    )
    store.add(
        routine_id="any-mail", name="any PRC mail", client="PRC",
        trigger={"kind": "event", "event_type": "email"},
        action="draft_reply", now=NOW,
    )
    store.add(
        routine_id="transcript", name="transcript", client="HHP",
        trigger={"kind": "event", "event_type": "transcript"},
        action="draft_note", now=NOW,
    )
    store.add(
        routine_id="charge", name="large charge", client="Alignment",
        trigger={"kind": "event", "event_type": "bank_charge", "threshold": 500},
        action="draft_review", now=NOW,
    )
    store.add(
        routine_id="lease", name="lease", client="ARLP",
        trigger={"kind": "event", "event_type": "lease_uploaded"},
        action="draft_review", now=NOW,
    )

    assert [item["id"] for item in store.match_event(
        {"type": "email", "client": "PRC", "from": "X"},
    )] == ["mail", "any-mail"]
    assert [item["id"] for item in store.match_event(
        {"type": "email", "client": "PRC", "from": "Y"},
    )] == ["any-mail"]
    assert store.match_event({"type": "email", "client": "HHP", "from": "X"}) == []
    assert [item["id"] for item in store.match_event(
        {"type": "transcript", "client": "HHP"},
    )] == ["transcript"]
    assert store.match_event({"type": "transcript", "client": "PRC"}) == []
    assert [item["id"] for item in store.match_event(
        {"type": "bank_charge", "client": "Alignment", "amount": 500},
    )] == ["charge"]
    assert store.match_event(
        {"type": "bank_charge", "client": "Alignment", "amount": 499},
    ) == []
    assert store.match_event(
        {"type": "bank_charge", "client": "Alignment", "amount": True},
    ) == []
    assert store.match_event(
        {"type": "bank_charge", "client": "HHP", "amount": 900},
    ) == []
    assert [item["id"] for item in store.match_event(
        {"type": "lease_uploaded", "client": "ARLP"},
    )] == ["lease"]


def test_send_actions_are_rejected(tmp_path):
    store = RoutineStore(tmp_path / "routines.json")
    with pytest.raises(ValueError, match="never auto-send"):
        store.add(
            routine_id="bad", name="bad", client="intelio",
            trigger={"kind": "event", "event_type": "email", "from": "X"},
            action="send_message", now=NOW,
        )
    assert store.routines == []


def test_a_successful_run_waits_for_approval_and_does_not_send(tmp_path):
    store = RoutineStore(tmp_path / "routines.json")
    store.ensure_morning_digests(NOW)
    seen = {}

    def runner(routine):
        seen["blocked"] = send_blocked("send_message")
        seen["other"] = send_blocked("read_file")
        return f"draft for {routine['client']}"

    record = store.execute("morning-digest-HHP", NOW, runner=runner)
    assert seen["blocked"]["action"] == "block"
    assert seen["other"] is None
    assert record["status"] == "pending_approval"
    assert record["approval"] == "pending"
    assert record["auto_send"] is False
    assert record["proposal"] == "draft for HHP"
    assert send_blocked("send_message") is None

    default = store.execute("morning-digest-PRC", NOW)
    assert default["status"] == "pending_approval"
    assert default["auto_send"] is False
    assert "Pending approval. Not sent." in default["proposal"]
    approved = store.approve(default["run_id"])
    assert approved["approval"] == "approved"
    assert approved["auto_send"] is False
    assert "Not sent." in approved["proposal"]


def test_failures_retry_then_fail_and_the_log_survives_reload(tmp_path):
    path = tmp_path / "routines.json"
    store = RoutineStore(path)
    store.add(
        routine_id="mail", name="mail", client="PRC",
        trigger={"kind": "event", "event_type": "email", "from": "X"},
        action="draft_reply", now=NOW, max_retries=1,
    )
    first = store.execute("mail", NOW, fail=True)
    assert first["status"] == "retry"
    assert first["auto_send"] is False
    assert store.get("mail")["next_attempt_at"] == NOW + 60
    assert store.due(NOW + 59) == []
    assert [item["id"] for item in store.due(NOW + 60)] == ["mail"]

    second = store.execute("mail", NOW + 60, fail=True)
    assert second["status"] == "failed"
    assert store.due(NOW + 10_000) == []

    reloaded = RoutineStore(path)
    assert reloaded.get("mail")["status"] == "failed"
    runs = reloaded.load_runs()
    assert [row["status"] for row in runs] == ["retry", "failed"]
    assert all(row["auto_send"] is False for row in runs)
