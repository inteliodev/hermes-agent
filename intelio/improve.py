"""Proposals and regression cases. Nothing is applied until it is approved.

Approving a proposal records Hayden's decision in this log. It does not write
a skill, a memory, or a message. Grader failures become regression cases in a
caller-supplied file.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Optional


class ImproveLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.proposals: list[dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        if not self.path.is_file():
            self.proposals = []
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.proposals = list(payload.get("proposals") or [])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps({"proposals": self.proposals}, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)

    def propose(
        self, correction: str, *, target: str, now: float,
    ) -> dict[str, Any]:
        if target not in {"skill", "memory"}:
            raise ValueError("target must be skill or memory")
        text = correction.strip()
        if not text:
            raise ValueError("correction is empty")
        proposal = {
            "id": uuid.uuid4().hex,
            "target": target,
            "correction": text[:2000],
            "status": "pending_approval",
            "applied": False,
            "created_at": now,
            "approved_at": None,
        }
        self.proposals.append(proposal)
        self.save()
        return proposal

    def approve(self, proposal_id: str, now: float) -> dict[str, Any]:
        proposal = self._require(proposal_id)
        if proposal["status"] != "pending_approval":
            raise ValueError("proposal is not pending")
        proposal["status"] = "approved"
        proposal["approved_at"] = now
        proposal["applied"] = False
        self.save()
        return proposal

    def apply(self, proposal_id: str, dest: Path) -> Path:
        """Write an approved proposal into ``dest``. ``dest`` is caller-supplied."""
        proposal = self._require(proposal_id)
        if proposal["status"] != "approved":
            raise ValueError("approve the proposal before applying it")
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / f"{proposal['id']}.md"
        body = (
            f"# {proposal['target']} update\n\n"
            f"{proposal['correction']}\n"
        )
        target.write_text(body, encoding="utf-8")
        proposal["applied"] = True
        self.save()
        return target

    def _require(self, proposal_id: str) -> dict[str, Any]:
        found = next((item for item in self.proposals if item["id"] == proposal_id), None)
        if found is None:
            raise KeyError(proposal_id)
        return found


def proposal_from_message(
    user_message: str, log: ImproveLog, *, now: float,
) -> Optional[dict[str, Any]]:
    """A message that starts with ``correction:`` becomes a pending proposal."""
    stripped = (user_message or "").strip()
    if not stripped.lower().startswith("correction:"):
        return None
    body = stripped.split(":", 1)[1].strip()
    target = "memory" if body.lower().startswith("remember ") else "skill"
    return log.propose(body, target=target, now=now)


def record_regression(
    path: Path, case: dict, output: str, grade: dict, *, now: float,
) -> dict[str, Any]:
    """Append a failing grade as a regression case. Passing grades are refused."""
    if grade.get("passed"):
        raise ValueError("only grader failures become regression cases")
    entry = {
        "id": f"reg-{case.get('id', 'case')}-{uuid.uuid4().hex[:8]}",
        "source": case.get("id", ""),
        "client": case.get("client", ""),
        "prompt": case.get("prompt", ""),
        "bad": output,
        "must_mention": list(case.get("must_mention") or []),
        "reason": grade.get("reason", ""),
        "at": now,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
    os.chmod(path, 0o600)
    return entry


def load_regressions(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def weekly_report(
    log: ImproveLog, regressions: Path, *, start: float, end: float,
) -> str:
    """What was approved, and which grader failures became cases, in ``[start, end]``."""
    approved = [
        item for item in log.proposals
        if item.get("status") == "approved"
        and item.get("approved_at") is not None
        and start <= float(item["approved_at"]) <= end
    ]
    fresh = [
        row for row in load_regressions(regressions)
        if start <= float(row.get("at") or 0) <= end
    ]
    lines = [
        "Weekly improvement report",
        f"Approved updates: {len(approved)}",
    ]
    for item in approved:
        lines.append(f"- {item['target']}: {item['correction']}")
    lines.append(f"New regression cases: {len(fresh)}")
    for row in fresh:
        lines.append(f"- {row['id']} from {row['source']}: {row['reason']}")
    if not approved and not fresh:
        lines.append("Nothing changed in this window.")
    return "\n".join(lines) + "\n"
