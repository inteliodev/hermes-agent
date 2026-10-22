"""Profile cron jobs for intelio routines: opt-in, isolation, and local delivery."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from intelio.routines import DIGEST_SCHEDULE, RoutineStore, parse_cron
from intelio.routines_schedule import (
    compose_digest,
    load_digest_sources,
    run_routine_job,
    source_allowed,
    sync_profile,
)

NOW = 1_700_000_000.0
FORBIDDEN = (
    "PACKTOKEN_HHP",
    "desk 4175",
    "HHP secret lease",
    "Hayden routing number",
    "Hayden text to mom",
    "UNREAD_LEASE_SECRET",
    "ONLY_IN_OTHER_HOME",
    "blank owner account",
)


def _jobs(home: Path) -> list[dict]:
    path = home / "cron" / "jobs.json"
    if not path.is_file():
        return []
    return list(json.loads(path.read_text(encoding="utf-8"))["jobs"])


def _names(home: Path) -> list[str]:
    return [job["name"] for job in _jobs(home)]


def _routine_ids(home: Path) -> set[str]:
    store = RoutineStore(home / "intelio" / "routines.json")
    return {item["id"] for item in store.routines}


def _write_config(home: Path, *, client: str, digests: bool, routines: bool = False) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        "intelio:\n"
        f"  client: {client}\n"
        f"  digests: {'true' if digests else 'false'}\n"
        f"  routines_enabled: {'true' if routines else 'false'}\n",
        encoding="utf-8",
    )


def _sources() -> list[dict]:
    return [
        {"client": "PRC", "category": "operations", "owner": "PRC", "text": "PRC dock note"},
        {"client": "HHP", "category": "operations", "owner": "HHP", "text": "HHP secret lease"},
        {
            "client": "PRC", "category": "Finance", "owner": "Hayden",
            "text": "Hayden routing number 999",
        },
        {
            "client": "PRC", "category": "Messages", "owner": "Hayden",
            "text": "Hayden text to mom",
        },
        {
            "client": "PRC", "category": "messages", "owner": "",
            "text": "blank owner account 42",
        },
        {
            "client": "PRC", "category": "operations", "owner": "PRC",
            "text": "see PACKTOKEN_HHP desk 4175",
        },
        {
            "client": "PRC", "category": "Finance", "owner": "desk",
            "text": "PRC vendor invoice 12",
        },
    ]


def test_digests_and_routines_are_off_until_the_profile_opts_in(tmp_path):
    home = tmp_path / "profile"
    result = sync_profile(
        home, cfg={"client": "PRC", "digests": False, "routines_enabled": False}, now=NOW,
    )
    assert result == {"client": "PRC", "installed": [], "paused": []}
    assert not (home / "cron" / "jobs.json").exists()
    assert not (home / "intelio" / "routines.json").exists()


def test_opt_in_installs_one_local_digest_and_a_second_sync_does_not_duplicate(tmp_path):
    home = tmp_path / "profile"
    cfg = {"client": "PRC", "digests": True, "routines_enabled": False}
    first = sync_profile(home, cfg=cfg, now=NOW)
    second = sync_profile(home, cfg=cfg, now=NOW)
    assert first["installed"] == ["intelio-routine-morning-digest-PRC"]
    assert second["installed"] == first["installed"]
    jobs = _jobs(home)
    assert len(jobs) == 1
    job = jobs[0]
    assert job["name"] == "intelio-routine-morning-digest-PRC"
    assert job["deliver"] == "local"
    assert job["failure_deliver"] == "local"
    assert not job.get("origin")
    assert job["no_agent"] is True
    assert job["script"] == "intelio_routine__morning-digest-PRC.py"
    assert job["schedule"]["expr"] == parse_cron(DIGEST_SCHEDULE)["expr"]
    assert _routine_ids(home) == {"morning-digest-PRC"}


def test_two_profiles_schedule_only_their_own_digest(tmp_path):
    prc = tmp_path / "prc"
    hhp = tmp_path / "hhp"
    sync_profile(prc, cfg={"client": "PRC", "digests": True}, now=NOW)
    sync_profile(hhp, cfg={"client": "HHP", "digests": True}, now=NOW)
    assert _names(prc) == ["intelio-routine-morning-digest-PRC"]
    assert _names(hhp) == ["intelio-routine-morning-digest-HHP"]
    assert "morning-digest-HHP" not in _routine_ids(prc)
    assert "morning-digest-PRC" not in _routine_ids(hhp)
    assert (prc / "cron" / "jobs.json").read_text(encoding="utf-8").find("HHP") == -1
    assert (hhp / "cron" / "jobs.json").read_text(encoding="utf-8").find("PRC") == -1


def test_routines_flag_schedules_only_this_clients_scheduled_rows(tmp_path):
    home = tmp_path / "profile"
    store = RoutineStore(home / "intelio" / "routines.json")
    store.add(
        routine_id="prc-mail-check", name="mail", client="PRC",
        trigger={"kind": "schedule", "schedule": "0 9 * * 1"},
        action="draft_note", now=NOW,
    )
    store.add(
        routine_id="hhp-mail-check", name="mail", client="HHP",
        trigger={"kind": "schedule", "schedule": "0 9 * * 1"},
        action="draft_note", now=NOW,
    )
    store.add(
        routine_id="prc-mail-event", name="event", client="PRC",
        trigger={"kind": "event", "event_type": "email", "from": "X"},
        action="draft_note", now=NOW,
    )
    result = sync_profile(
        home, cfg={"client": "PRC", "digests": False, "routines_enabled": True}, now=NOW,
    )
    assert result["installed"] == ["intelio-routine-prc-mail-check"]
    assert _names(home) == ["intelio-routine-prc-mail-check"]


def test_a_profile_with_no_client_gets_no_digest(tmp_path):
    home = tmp_path / "profile"
    result = sync_profile(home, cfg={"client": "", "digests": True}, now=NOW)
    assert result["installed"] == []
    assert result["client"] == ""
    assert not (home / "cron" / "jobs.json").exists()


def test_digest_text_keeps_this_client_and_drops_foreign_and_private_rows(tmp_path, monkeypatch):
    home = tmp_path / "prc"
    other = tmp_path / "other"
    _write_config(home, client="PRC", digests=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    secret = home / "secrets" / "lease.txt"
    secret.parent.mkdir(parents=True)
    secret.write_text("UNREAD_LEASE_SECRET\n", encoding="utf-8")
    (other / "intelio").mkdir(parents=True)
    (other / "intelio" / "digest-sources.json").write_text(
        json.dumps([{"client": "HHP", "text": "ONLY_IN_OTHER_HOME"}]),
        encoding="utf-8",
    )
    rows = _sources()
    rows[0]["path"] = str(secret)
    (home / "intelio").mkdir(parents=True, exist_ok=True)
    (home / "intelio" / "digest-sources.json").write_text(json.dumps(rows), encoding="utf-8")

    loaded = load_digest_sources(home)
    assert source_allowed("PRC", loaded[0]) is True
    assert source_allowed("PRC", loaded[1]) is False
    assert source_allowed("PRC", loaded[2]) is False
    text = compose_digest("PRC", loaded)
    assert "PRC dock note" in text
    assert "PRC vendor invoice 12" in text
    assert "Pending approval. Not sent." in text
    for forbidden in FORBIDDEN:
        assert forbidden not in text

    sync_profile(home, cfg={"client": "PRC", "digests": True, "routines_enabled": False}, now=NOW)
    output = run_routine_job("morning-digest-PRC", now=NOW)
    assert "PACKTOKEN_PRC" in output
    assert "PRC dock note" in output
    assert "Pending approval. Not sent." in output
    for forbidden in FORBIDDEN:
        assert forbidden not in output
    in_app = list((home / "intelio" / "digests" / "PRC").glob("*.md"))
    assert len(in_app) == 1
    assert "PRC dock note" in in_app[0].read_text(encoding="utf-8")
    runs = RoutineStore(home / "intelio" / "routines.json").load_runs()
    assert len(runs) == 1
    assert runs[0]["status"] == "pending_approval"
    assert runs[0]["auto_send"] is False
    assert runs[0]["approval"] == "pending"
    assert secret.read_text(encoding="utf-8") == "UNREAD_LEASE_SECRET\n"


def test_sync_repairs_outbound_delivery_and_leaves_a_user_pause(tmp_path):
    from cron.jobs import pause_job, update_job, use_cron_store
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    home = tmp_path / "profile"
    cfg = {"client": "PRC", "digests": True, "routines_enabled": False}
    sync_profile(home, cfg=cfg, now=NOW)
    token = set_hermes_home_override(str(home))
    try:
        with use_cron_store(home):
            job_id = _jobs(home)[0]["id"]
            update_job(job_id, {
                "deliver": "telegram",
                "failure_deliver": "email",
                "origin": {"platform": "sms", "chat_id": "1"},
                "prompt": "email the digest",
            })
            pause_job(job_id, "operator hold")
    finally:
        reset_hermes_home_override(token)

    sync_profile(home, cfg=cfg, now=NOW)
    job = _jobs(home)[0]
    assert job["deliver"] == "local"
    assert job["failure_deliver"] == "local"
    assert not job.get("origin")
    assert not str(job.get("prompt") or "").strip()
    assert job["no_agent"] is True
    assert job["enabled"] is False
    assert job["state"] == "paused"
    assert job["paused_reason"] == "operator hold"


@pytest.fixture
def scheduled_home(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    from cron import jobs, scheduler

    monkeypatch.setattr(jobs, "HERMES_DIR", home)
    monkeypatch.setattr(jobs, "CRON_DIR", home / "cron")
    monkeypatch.setattr(jobs, "JOBS_FILE", home / "cron" / "jobs.json")
    monkeypatch.setattr(jobs, "OUTPUT_DIR", home / "cron" / "output")
    monkeypatch.setattr(scheduler, "_hermes_home", home)
    monkeypatch.setattr(scheduler, "_should_yield_tick_to_fresh_gateway", lambda: None)
    monkeypatch.setattr(scheduler, "_sweep_mcp_orphans", lambda: None)
    monkeypatch.setattr(
        "cron.scheduler_authority.reconcile_pending",
        lambda *, allow_connect=True: None,
    )
    scheduler._running_job_ids.clear()
    yield home
    scheduler._shutdown_parallel_pool()
    scheduler._running_job_ids.clear()


def _make_due(job_id: str) -> None:
    """Park next_run_at on the previous legal weekday 08:00 so the due scan fires it."""
    from cron import jobs
    from croniter import croniter

    now = jobs._hermes_now()
    zone = now.tzinfo
    previous = croniter(DIGEST_SCHEDULE, now.astimezone(zone).replace(tzinfo=None)).get_prev(datetime)
    slot = previous.replace(tzinfo=zone, microsecond=0).isoformat()
    stored = jobs.load_jobs()
    next(row for row in stored if row["id"] == job_id)["next_run_at"] = slot
    jobs.save_jobs(stored)


def test_the_ticker_runs_a_local_digest_and_opt_out_stops_it(scheduled_home, monkeypatch):
    from cron import scheduler
    from cron.scheduler_delivery import _resolve_delivery_targets

    home = scheduled_home
    _write_config(home, client="PRC", digests=True)
    rows = _sources()
    secret = home / "secrets" / "lease.txt"
    secret.parent.mkdir(parents=True)
    secret.write_text("UNREAD_LEASE_SECRET\n", encoding="utf-8")
    rows[0]["path"] = str(secret)
    (home / "intelio").mkdir(parents=True, exist_ok=True)
    (home / "intelio" / "digest-sources.json").write_text(json.dumps(rows), encoding="utf-8")
    (home.parent / "other" / "intelio").mkdir(parents=True)
    (home.parent / "other" / "intelio" / "digest-sources.json").write_text(
        json.dumps([{"client": "HHP", "text": "ONLY_IN_OTHER_HOME"}]),
        encoding="utf-8",
    )
    sync_profile(
        home, cfg={"client": "PRC", "digests": True, "routines_enabled": False}, now=NOW,
    )
    job = _jobs(home)[0]
    assert _resolve_delivery_targets(job) == []
    assert _resolve_delivery_targets(job, for_failure=True) == []

    sent: list[str] = []

    def _outbound(*_args, **_kwargs):
        sent.append("sent")
        raise AssertionError("digest must not send")

    monkeypatch.setattr("cron.scheduler_delivery._deliver_via_live_adapter", _outbound)
    monkeypatch.setattr("cron.scheduler_delivery._standalone_send", _outbound)
    monkeypatch.setattr("cron.scheduler_delivery._deliver_to_bot_chat", _outbound)

    _make_due(job["id"])
    assert scheduler.tick(verbose=False, sync=True, headless=True) == 1
    assert sent == []

    runs = RoutineStore(home / "intelio" / "routines.json").load_runs()
    assert len(runs) == 1
    assert runs[0]["status"] == "pending_approval"
    assert runs[0]["auto_send"] is False
    assert runs[0]["approval"] == "pending"
    saved = list((home / "cron" / "output").rglob("*.md"))
    in_app = list((home / "intelio" / "digests" / "PRC").glob("*.md"))
    assert saved and in_app
    combined = "\n".join(path.read_text(encoding="utf-8") for path in saved + in_app)
    assert "PACKTOKEN_PRC" in combined
    assert "PRC dock note" in combined
    assert "Pending approval. Not sent." in combined
    for forbidden in FORBIDDEN:
        assert forbidden not in combined

    _write_config(home, client="PRC", digests=False)
    sync_profile(
        home, cfg={"client": "PRC", "digests": False, "routines_enabled": False}, now=NOW,
    )
    paused = _jobs(home)[0]
    assert paused["enabled"] is False
    assert paused["state"] == "paused"
    _make_due(paused["id"])
    assert scheduler.tick(verbose=False, sync=True, headless=True) == 0
    assert len(RoutineStore(home / "intelio" / "routines.json").load_runs()) == 1
    assert sent == []
