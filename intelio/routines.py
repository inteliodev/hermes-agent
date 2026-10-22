"""Routines: scheduled jobs and event triggers on top of cron schedules.

The store is a JSON file plus a JSONL run log in a directory the caller
chooses. A schedule is accepted only when ``cron.jobs.parse_schedule`` accepts
it. Every successful action is a draft with ``auto_send`` false. Approving a
draft records the decision and still does not send.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from intelio.clients import CLIENTS

logger = logging.getLogger(__name__)

DIGEST_SCHEDULE = "0 8 * * 1-5"
WEEKLY_SCHEDULE = "0 9 * * 1"
EVENT_TYPES = ("email", "transcript", "bank_charge", "lease_uploaded")
_SEND_ACTIONS = frozenset({"send", "send_message", "auto_send"})
_inflight: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "intelio_routine_inflight", default=False,
)


def send_blocked(tool_name: str) -> Optional[dict[str, str]]:
    """Block ``send_message`` while a routine is executing. Other tools pass."""
    if tool_name != "send_message" or not _inflight.get():
        return None
    return {
        "action": "block",
        "message": (
            "Routines never auto-send. The draft is waiting for approval."
        ),
    }


@contextmanager
def routine_inflight() -> Iterator[None]:
    token = _inflight.set(True)
    try:
        yield
    finally:
        _inflight.reset(token)


def parse_cron(schedule: str) -> dict[str, Any]:
    """Validate a schedule with the cron job parser and return its spec."""
    from cron.jobs import parse_schedule
    spec = parse_schedule(schedule)
    if spec.get("kind") != "cron" or not spec.get("expr"):
        raise ValueError(f"routine schedules must be cron expressions, got {schedule!r}")
    return spec


def next_cron_time(expr: str, now: float) -> float:
    from croniter import croniter
    base = datetime.fromtimestamp(now, UTC)
    nxt = croniter(expr, base).get_next(datetime)
    return float(nxt.timestamp())


def _reject_send(action: str) -> None:
    if action in _SEND_ACTIONS:
        raise ValueError("routines never auto-send")


def _event_matches(trigger: dict, event: dict) -> bool:
    kind = trigger["event_type"]
    if kind == "email":
        expected = str(trigger.get("from") or "")
        return not expected or event.get("from") == expected
    if kind == "bank_charge":
        amount = event.get("amount")
        threshold = trigger.get("threshold")
        if not isinstance(amount, (int, float)) or isinstance(amount, bool):
            return False
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
            return False
        return float(amount) >= float(threshold)
    if kind in {"transcript", "lease_uploaded"}:
        return True
    return False


class RoutineStore:
    """JSON routines plus a JSONL run log. ``load`` rebuilds both after a restart."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.runs_path = self.path.with_suffix(".runs.jsonl")
        self.routines: list[dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        if not self.path.is_file():
            self.routines = []
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.routines = list(payload.get("routines") or [])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(".json.tmp")
        body = json.dumps({"routines": self.routines}, indent=2) + "\n"
        temporary.write_text(body, encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)

    def get(self, routine_id: str) -> Optional[dict[str, Any]]:
        return next((item for item in self.routines if item["id"] == routine_id), None)

    def add(
        self, *, routine_id: str, name: str, client: str, trigger: dict,
        action: str, now: float, max_retries: int = 3,
    ) -> dict[str, Any]:
        if self.get(routine_id) is not None:
            raise ValueError(f"routine {routine_id} already exists")
        _reject_send(action)
        if client and client not in CLIENTS:
            raise ValueError(f"unknown client {client!r}")
        kind = trigger.get("kind")
        stored_trigger = dict(trigger)
        next_run = None
        if kind == "schedule":
            spec = parse_cron(str(trigger.get("schedule") or ""))
            stored_trigger["expr"] = spec["expr"]
            next_run = next_cron_time(spec["expr"], now)
        elif kind == "event":
            if trigger.get("event_type") not in EVENT_TYPES:
                raise ValueError(f"unknown event type {trigger.get('event_type')!r}")
        else:
            raise ValueError("trigger kind must be schedule or event")
        routine = {
            "id": routine_id,
            "name": name,
            "client": client,
            "trigger": stored_trigger,
            "action": action,
            "approval_required": True,
            "auto_send": False,
            "status": "active",
            "retry_count": 0,
            "max_retries": max_retries,
            "next_run_at": next_run,
            "next_attempt_at": None,
            "paused": False,
        }
        self.routines.append(routine)
        self.save()
        return routine

    def ensure_morning_digests(self, now: float) -> list[dict[str, Any]]:
        """One weekday morning draft per client. Existing rows are left alone."""
        created: list[dict[str, Any]] = []
        for client in CLIENTS:
            routine_id = f"morning-digest-{client}"
            if self.get(routine_id) is not None:
                continue
            created.append(self.add(
                routine_id=routine_id,
                name=f"{client} morning digest",
                client=client,
                trigger={"kind": "schedule", "schedule": DIGEST_SCHEDULE},
                action="draft_digest",
                now=now,
            ))
        return created

    def ensure_weekly_report(self, now: float) -> dict[str, Any]:
        """Owner-level Monday draft. Not scoped to one client's pack."""
        existing = self.get("weekly-improvement")
        if existing is not None:
            return existing
        return self.add(
            routine_id="weekly-improvement",
            name="Weekly improvement report",
            client="",
            trigger={"kind": "schedule", "schedule": WEEKLY_SCHEDULE},
            action="draft_report",
            now=now,
        )

    def match_event(self, event: dict) -> list[dict[str, Any]]:
        matched: list[dict[str, Any]] = []
        for routine in self.routines:
            trigger = routine["trigger"]
            if trigger.get("kind") != "event" or routine.get("paused"):
                continue
            if routine.get("client") and event.get("client") != routine["client"]:
                continue
            if trigger.get("event_type") != event.get("type"):
                continue
            if _event_matches(trigger, event):
                matched.append(routine)
        return matched

    def due(self, now: float) -> list[dict[str, Any]]:
        ready: list[dict[str, Any]] = []
        for routine in self.routines:
            if routine.get("paused") or routine.get("status") == "failed":
                continue
            if routine.get("status") == "retry":
                attempt_at = routine.get("next_attempt_at")
                if attempt_at is not None and float(attempt_at) <= now:
                    ready.append(routine)
                continue
            if routine["trigger"].get("kind") != "schedule":
                continue
            nxt = routine.get("next_run_at")
            if nxt is None or float(nxt) <= now:
                ready.append(routine)
        return ready

    def execute(
        self, routine_id: str, now: float, *,
        runner: Optional[Callable[[dict], str]] = None, fail: bool = False,
    ) -> dict[str, Any]:
        routine = self.get(routine_id)
        if routine is None:
            raise KeyError(routine_id)
        attempt = int(routine.get("retry_count") or 0) + 1
        proposal = ""
        error = ""
        try:
            with routine_inflight():
                if fail:
                    raise RuntimeError("routine failed")
                if runner is None:
                    proposal = _default_proposal(routine)
                else:
                    proposal = runner(routine)
            routine["retry_count"] = 0
            routine["status"] = "active"
            routine["next_attempt_at"] = None
            if routine["trigger"].get("kind") == "schedule":
                routine["next_run_at"] = next_cron_time(routine["trigger"]["expr"], now)
            status = "pending_approval"
            approval = "pending"
        except Exception as exc:
            logger.exception("routine %s failed", routine_id)
            error = str(exc)
            proposal = ""
            routine["retry_count"] = attempt
            if attempt <= int(routine["max_retries"]):
                routine["status"] = "retry"
                routine["next_attempt_at"] = now + 60 * (2 ** (attempt - 1))
                status = "retry"
            else:
                routine["status"] = "failed"
                routine["next_attempt_at"] = None
                status = "failed"
            approval = "none"
        self.save()
        record = {
            "run_id": uuid.uuid4().hex,
            "routine_id": routine_id,
            "client": routine.get("client") or "",
            "at": now,
            "status": status,
            "approval": approval,
            "auto_send": False,
            "proposal": proposal,
            "error": error,
            "attempt": attempt,
        }
        self._append_run(record)
        return record

    def load_runs(self) -> list[dict[str, Any]]:
        if not self.runs_path.is_file():
            return []
        rows = []
        for line in self.runs_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def approve(self, run_id: str) -> dict[str, Any]:
        """Record approval. The draft stays unsent."""
        runs = self.load_runs()
        found = next((row for row in runs if row["run_id"] == run_id), None)
        if found is None:
            raise KeyError(run_id)
        if found["status"] != "pending_approval":
            raise ValueError("only a pending draft can be approved")
        found["approval"] = "approved"
        found["auto_send"] = False
        self._write_runs(runs)
        return found

    def _append_run(self, record: dict) -> None:
        self.runs_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.runs_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        os.chmod(self.runs_path, 0o600)

    def _write_runs(self, runs: list[dict]) -> None:
        temporary = self.runs_path.with_suffix(".jsonl.tmp")
        temporary.write_text(
            "".join(json.dumps(row) + "\n" for row in runs), encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.runs_path)


def _default_proposal(routine: dict) -> str:
    client = routine.get("client") or "all clients"
    action = routine["action"]
    if action == "draft_digest":
        return (
            f"Draft morning digest for {client}. Pending approval. Not sent."
        )
    if action == "draft_report":
        return "Draft weekly improvement report for Hayden. Pending approval. Not sent."
    return f"Draft {action} for {client}. Pending approval. Not sent."
