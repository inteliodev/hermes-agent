"""Install one profile's intelio routines on that profile's cron ticker.

Digests and other routines stay off until the profile opts in. Each job is a
``no_agent`` script with ``deliver`` and ``failure_deliver`` set to ``local``
and no origin, so the draft stays in Hermes cron output and, for a digest, in
``intelio/digests/<client>/``. Nothing is texted or emailed.

A profile schedules only routines whose client is ``intelio.client``. The host
ticker calls ``sync_gateway_profile`` for each home it is about to tick, so a
secondary profile does not wait for a session. Sources come from that home's
``intelio/digest-sources.json``. Another client's pack token or desk code is
dropped, and so are Finance and Messages notes owned by Hayden or by nobody.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from hermes_constants import (
    get_hermes_home,
    reset_hermes_home_override,
    set_hermes_home_override,
)
from intelio.clients import CLIENTS
from intelio.routines import RoutineStore, flag_on

logger = logging.getLogger(__name__)

JOB_PREFIX = "intelio-routine-"
PAUSE_REASON = "Intelio routines are off for this profile."
LOCAL_DELIVER = "local"
PRIVATE_CATEGORIES = frozenset({"finance", "messages"})
_HAYDEN_OWNER = re.compile(r"\bhayden\b")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")
_ROUTINES_PLUGIN = "intelio-routines"
_REPO = Path(__file__).resolve().parents[1]


def source_allowed(client: str, item: Any) -> bool:
    """True when one digest row belongs to this profile's client and is not private."""
    if not isinstance(item, dict) or client not in CLIENTS:
        return False
    category = str(item.get("category") or "").strip().lower()
    owner = str(item.get("owner") or "").strip().lower()
    if category in PRIVATE_CATEGORIES and _private_owner(owner):
        return False
    return str(item.get("client") or "").strip() == client


def compose_digest(client: str, sources: Any, *, pack_text: str = "") -> str:
    """Draft text for one client. A leaky body collapses to the pending line."""
    if client not in CLIENTS:
        return ""
    header = f"Morning digest for {client}. Pending approval. Not sent."
    parts = [header]
    if pack_text and not _leaks(client, pack_text):
        parts.append(pack_text.strip())
    for item in sources or []:
        if not source_allowed(client, item):
            continue
        text = str(item.get("text") or "").strip()
        if text and not _leaks(client, text):
            parts.append(text)
    body = "\n".join(parts)
    if _leaks(client, body):
        return header
    return body


def load_digest_sources(home: Path) -> list[dict[str, Any]]:
    """Rows from ``home/intelio/digest-sources.json`` only. A path outside the home is ignored."""
    root = Path(home).resolve()
    path = (root / "intelio" / "digest-sources.json").resolve()
    if not _inside(path, root) or not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.debug("digest sources unreadable", exc_info=True)
        return []
    rows = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def sync_gateway_profile(home: Optional[Path] = None) -> dict[str, Any]:
    """Install or pause one home the host ticker is about to visit.

    Called inside that profile's cron scope, before the due scan. The profile's
    ``config.yaml`` is read directly so a secondary home is not confused with
    the launch profile and so a profile that never opted in is not created.
    A profile that has not enabled ``intelio-routines`` is paused and never
    given a new row. Opted-in profiles schedule only their own ``intelio.client``.
    """
    target = Path(home) if home is not None else get_hermes_home()
    raw = _read_profile_config(target)
    plugins = raw.get("plugins") if isinstance(raw.get("plugins"), dict) else {}
    section = raw.get("intelio") if isinstance(raw.get("intelio"), dict) else {}
    if not _plugin_listed(plugins):
        return sync_profile(
            target, cfg={"client": "", "digests": False, "routines_enabled": False},
        )
    return sync_profile(target, cfg=section)


def sync_profile(
    home: Optional[Path] = None, *, cfg: Optional[dict] = None, now: Optional[float] = None,
) -> dict[str, Any]:
    """Create, repair, or pause this profile's intelio cron jobs. Other homes are untouched."""
    target = Path(home) if home is not None else get_hermes_home()
    token = set_hermes_home_override(str(target))
    try:
        from cron.jobs import use_cron_store

        with use_cron_store(target):
            section = cfg if isinstance(cfg, dict) else _intelio_section()
            return _sync_in_scope(target, section, time.time() if now is None else now)
    finally:
        reset_hermes_home_override(token)


def run_routine_job(routine_id: str, *, now: Optional[float] = None) -> str:
    """Stdout for one scheduled routine. Empty when the profile did not opt in or the row is foreign."""
    if not _safe_id(routine_id):
        return ""
    home = get_hermes_home()
    section = _intelio_section()
    client = _profile_client(section)
    digests = flag_on(section, "digests")
    routines = flag_on(section, "routines_enabled")
    if not client or not (digests or routines):
        return ""
    store = RoutineStore(home / "intelio" / "routines.json")
    routine = store.get(routine_id)
    if routine is None or not _run_allowed(routine, client, digests, routines):
        return ""
    proposal = _proposal_for(routine, home, client)
    if not proposal or _leaks(client, proposal):
        return ""
    record = store.execute(
        routine_id, time.time() if now is None else now, runner=lambda _routine: proposal,
    )
    if record.get("auto_send") is not False or record.get("status") != "pending_approval":
        return ""
    if routine.get("action") == "draft_digest":
        _write_in_app(home, client, str(record.get("run_id") or ""), proposal)
    return proposal if proposal.endswith("\n") else proposal + "\n"


def _sync_in_scope(home: Path, section: dict, now: float) -> dict[str, Any]:
    client, wanted = _wanted_routines(home, section, now)
    installed = [_upsert_job(home, row) for row in wanted]
    paused = _pause_others(home, set(installed))
    return {"client": client, "installed": installed, "paused": paused}


def _wanted_routines(home: Path, section: dict, now: float) -> tuple[str, list[dict[str, Any]]]:
    client = _profile_client(section)
    digests = flag_on(section, "digests")
    routines = flag_on(section, "routines_enabled")
    if not client or not (digests or routines):
        return client, []
    store = RoutineStore(home / "intelio" / "routines.json")
    if digests:
        store.ensure_client_digest(client, now)
    return client, _schedulable(store.routines, client, digests, routines)


def _schedulable(
    rows: list[dict[str, Any]], client: str, digests: bool, routines: bool,
) -> list[dict[str, Any]]:
    return [row for row in rows if _row_schedulable(row, client, digests, routines)]


def _row_schedulable(row: dict[str, Any], client: str, digests: bool, routines: bool) -> bool:
    trigger = row.get("trigger") or {}
    if trigger.get("kind") != "schedule" or row.get("client") != client:
        return False
    if row.get("paused") or row.get("status") == "failed":
        return False
    if not str(trigger.get("schedule") or "").strip():
        return False
    routine_id = str(row.get("id") or "")
    if not _safe_id(routine_id):
        logger.debug("skipping unsafe intelio routine id")
        return False
    if row.get("action") == "draft_digest":
        return digests
    return routines


def _upsert_job(home: Path, row: dict[str, Any]) -> str:
    routine_id = str(row["id"])
    name = f"{JOB_PREFIX}{routine_id}"
    filename = _write_script(home, routine_id)
    schedule = str(row["trigger"]["schedule"])
    existing = _job_named(home, name)
    if existing is None:
        _create_local_job(name, schedule, filename)
        return name
    _repair_job(existing, schedule, filename)
    if existing.get("state") == "paused" and existing.get("paused_reason") == PAUSE_REASON:
        from cron.jobs import resume_job
        resume_job(existing["id"])
    return name


def _create_local_job(name: str, schedule: str, filename: str) -> None:
    from cron.jobs import create_job
    create_job(
        prompt=None,
        schedule=schedule,
        name=name,
        deliver=LOCAL_DELIVER,
        failure_deliver=LOCAL_DELIVER,
        origin=None,
        script=filename,
        no_agent=True,
    )


def _repair_job(job: dict[str, Any], schedule: str, filename: str) -> None:
    from cron.jobs import parse_schedule, update_job
    updates: dict[str, Any] = {}
    if job.get("deliver") != LOCAL_DELIVER:
        updates["deliver"] = LOCAL_DELIVER
    if job.get("failure_deliver") != LOCAL_DELIVER:
        updates["failure_deliver"] = LOCAL_DELIVER
    if job.get("origin"):
        updates["origin"] = None
    if job.get("no_agent") is not True:
        updates["no_agent"] = True
    if job.get("script") != filename:
        updates["script"] = filename
    if str(job.get("prompt") or "").strip():
        updates["prompt"] = ""
    wanted = parse_schedule(schedule)["expr"]
    if (job.get("schedule") or {}).get("expr") != wanted:
        updates["schedule"] = schedule
    if updates:
        update_job(job["id"], updates)


def _pause_others(home: Path, wanted_names: set[str]) -> list[str]:
    if not (home / "cron" / "jobs.json").is_file():
        return []
    from cron.jobs import load_jobs, pause_job
    paused: list[str] = []
    for job in load_jobs():
        name = str(job.get("name") or "")
        if not name.startswith(JOB_PREFIX) or name in wanted_names:
            continue
        if job.get("state") == "paused" and job.get("enabled") is False:
            continue
        pause_job(job["id"], PAUSE_REASON)
        paused.append(name)
    return paused


def _job_named(home: Path, name: str) -> Optional[dict[str, Any]]:
    if not (home / "cron" / "jobs.json").is_file():
        return None
    from cron.jobs import load_jobs
    return next((job for job in load_jobs() if job.get("name") == name), None)


def _proposal_for(routine: dict[str, Any], home: Path, client: str) -> str:
    action = str(routine.get("action") or "")
    if action == "draft_digest":
        return compose_digest(client, load_digest_sources(home), pack_text=_pack_text(client))
    text = f"Draft {action or 'work'} for {client}. Pending approval. Not sent."
    if _leaks(client, text):
        return ""
    return text


def _run_allowed(routine: dict[str, Any], client: str, digests: bool, routines: bool) -> bool:
    trigger = routine.get("trigger") or {}
    if routine.get("client") != client or trigger.get("kind") != "schedule":
        return False
    if routine.get("paused") or routine.get("status") == "failed":
        return False
    if routine.get("action") == "draft_digest":
        return digests
    return routines


def _pack_text(client: str) -> str:
    try:
        from intelio.knowledge import excerpt_for_client
        text = excerpt_for_client(client)
    except (OSError, ValueError):
        logger.debug("knowledge pack unreadable for %s", client, exc_info=True)
        return ""
    if _leaks(client, text):
        return ""
    return text


def _leaks(client: str, text: str) -> bool:
    from intelio.knowledge import foreign_markers
    return any(marker and marker in text for marker in foreign_markers(client))


def _write_in_app(home: Path, client: str, run_id: str, text: str) -> None:
    if client not in CLIENTS or not _safe_id(run_id):
        return
    folder = home / "intelio" / "digests" / client
    target = folder / f"{run_id}.md"
    if not _inside(target, home):
        return
    body = text if text.endswith("\n") else text + "\n"
    try:
        _atomic_text(target, body, 0o600)
        os.chmod(folder, 0o700)
    except OSError:
        logger.warning("in-app digest was not written for %s", client, exc_info=True)


def _write_script(home: Path, routine_id: str) -> str:
    filename = f"intelio_routine__{routine_id}.py"
    target = home / "scripts" / filename
    body = _script_body(routine_id)
    if target.is_file() and target.read_text(encoding="utf-8") == body:
        return filename
    _atomic_text(target, body, 0o700)
    return filename


def _script_body(routine_id: str) -> str:
    repo = str(_REPO)
    return (
        '"""Print one intelio routine draft. This file never sends a message."""\n'
        "import sys\n"
        "from pathlib import Path\n"
        f"_candidate = Path({repo!r})\n"
        'if (_candidate / "intelio" / "routines_schedule.py").is_file():\n'
        "    sys.path.insert(0, str(_candidate))\n"
        "from intelio.routines_schedule import run_routine_job\n"
        'if __name__ == "__main__":\n'
        f"    sys.stdout.write(run_routine_job({routine_id!r}))\n"
    )


def _atomic_text(path: Path, body: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(body, encoding="utf-8")
    os.chmod(temporary, mode)
    os.replace(temporary, path)
    os.chmod(path, mode)


def _intelio_section() -> dict[str, Any]:
    try:
        from hermes_cli.config import load_config
        section = load_config().get("intelio") or {}
    except Exception:
        logger.debug("intelio config unreadable", exc_info=True)
        return {}
    return section if isinstance(section, dict) else {}


def _private_owner(owner: str) -> bool:
    """Blank, Hayden, or a name that includes Hayden. Other owners stay visible."""
    return not owner or _HAYDEN_OWNER.search(owner) is not None


def _read_profile_config(home: Path) -> dict[str, Any]:
    """That home's ``config.yaml`` only. Missing or unreadable is an empty mapping."""
    path = Path(home) / "config.yaml"
    if not path.is_file():
        return {}
    try:
        from hermes_cli.config import read_user_config_raw
        data = read_user_config_raw(path)
    except Exception:
        logger.debug("profile config unreadable for intelio sync", exc_info=True)
        return {}
    return data if isinstance(data, dict) else {}


def _plugin_listed(plugins: dict) -> bool:
    """True when this profile enabled the routines plugin and did not disable it."""
    disabled = plugins.get("disabled")
    enabled = plugins.get("enabled")
    if isinstance(disabled, list) and _ROUTINES_PLUGIN in disabled:
        return False
    return isinstance(enabled, list) and _ROUTINES_PLUGIN in enabled


def _profile_client(section: Any) -> str:
    if not isinstance(section, dict):
        return ""
    client = section.get("client")
    if isinstance(client, str) and client in CLIENTS:
        return client
    return ""


def _safe_id(value: str) -> bool:
    return bool(_ID_RE.fullmatch(value))


def _inside(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False
