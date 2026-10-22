"""Corrections become proposals, and grader failures become regression cases."""

import importlib.util
import sys
from pathlib import Path

import pytest

from intelio.improve import (
    ImproveLog,
    proposal_from_message,
    record_regression,
    weekly_report,
)


def _grade():
    root = Path(__file__).resolve().parents[2]
    grader_dir = root / "evals" / "intelio_grader"
    for entry in (str(grader_dir), str(root)):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    spec = importlib.util.spec_from_file_location(
        "intelio_grader_grade_for_improve", grader_dir / "grade.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_correction_proposes_an_update_and_approval_does_not_apply_it(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    log = ImproveLog(tmp_path / "proposals.json")
    memory = proposal_from_message(
        "correction: remember Hayden approves every send", log, now=10,
    )
    skill = proposal_from_message(
        "correction: use the status note before drafting", log, now=11,
    )
    assert proposal_from_message("hello", log, now=12) is None
    assert memory["target"] == "memory"
    assert skill["target"] == "skill"
    assert memory["status"] == "pending_approval"
    assert memory["applied"] is False
    assert list(skills.iterdir()) == []

    approved = log.approve(memory["id"], now=20)
    assert approved["status"] == "approved"
    assert approved["applied"] is False
    assert list(skills.iterdir()) == []

    with pytest.raises(ValueError, match="approve"):
        log.apply(skill["id"], tmp_path / "out")

    dest = tmp_path / "approved"
    written = log.apply(memory["id"], dest)
    assert written.parent == dest
    assert written.read_text(encoding="utf-8").startswith("# memory update")
    assert list(skills.iterdir()) == []
    reloaded = ImproveLog(tmp_path / "proposals.json")
    saved = next(item for item in reloaded.proposals if item["id"] == memory["id"])
    assert saved["applied"] is True


def test_grader_failures_become_regression_cases(tmp_path):
    grade = _grade()
    case = next(
        item for item in grade.build_cases() if item["client"] == "HHP" and not item["jarvis"]
    )
    good = grade.grade_output(case, case["reference"])
    bad = grade.grade_output(case, case["bad"])
    assert good["passed"] is True
    assert bad["passed"] is False
    path = tmp_path / "regressions.jsonl"
    with pytest.raises(ValueError, match="failures"):
        record_regression(path, case, case["reference"], good, now=5)
    assert not path.exists()
    entry = record_regression(path, case, case["bad"], bad, now=5)
    again = grade.grade_output(case, entry["bad"])
    assert again["passed"] is False
    assert again["reason"] == bad["reason"]


def test_weekly_report_counts_approvals_and_new_cases(tmp_path):
    log = ImproveLog(tmp_path / "proposals.json")
    proposal = log.propose("remember the desk", target="memory", now=10)
    log.approve(proposal["id"], now=20)
    path = tmp_path / "regressions.jsonl"
    record_regression(
        path,
        {"id": "hhp-1", "client": "HHP", "prompt": "desk", "must_mention": ["4175"]},
        "wrong",
        {"passed": False, "reason": "failed: accuracy"},
        now=15,
    )
    report = weekly_report(log, path, start=0, end=30)
    assert "Approved updates: 1" in report
    assert "remember the desk" in report
    assert "New regression cases: 1" in report
    assert "failed: accuracy" in report
    quiet = weekly_report(log, path, start=100, end=200)
    assert "Nothing changed in this window." in quiet
