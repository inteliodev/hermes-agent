"""Rubric grader for Intelio client seed cases.

Dimensions: accuracy, tool correctness, no cross-client leak, Jarvis read-only.
``--self-test`` grades every reference (must pass) and every bad output (must fail)
with no model and no network. Promptfoo replays the reference through the same
function.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from seeds import JARVIS_BLOCKED_TOOLS, CLIENTS, build_cases

_TOOL_RE = re.compile(r"tool:([A-Za-z0-9_]+)")
_HERE = Path(__file__).resolve().parent


def _tools(text: str) -> set[str]:
    return set(_TOOL_RE.findall(text or ""))


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _pack_leak(client: str, text: str) -> bool:
    """True when ``text`` quotes another client's pack token or desk code."""
    if not client:
        return False
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    from intelio.knowledge import foreign_markers
    return any(marker and marker in text for marker in foreign_markers(client))


def _check_knowledge_ground_truth() -> None:
    """Pack files are the accuracy source for each client's desk and token."""
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    from intelio.clients import SECTIONS
    from intelio.knowledge import load_pack
    from seeds import DESK, secret as seed_secret
    for client in CLIENTS:
        pack = load_pack(client)
        facts = pack["facts"]
        if facts["desk"] != DESK[client]:
            raise SystemExit(f"{client} pack desk {facts['desk']} != {DESK[client]}")
        if seed_secret(client) in pack["text"]:
            raise SystemExit(f"{client} pack contains a grader secret")
        if pack["sections"] != list(SECTIONS):
            raise SystemExit(f"{client} pack sections {pack['sections']}")
        case = {
            "client": client,
            "jarvis": False,
            "must_mention": [facts["desk"], facts["token"]],
            "accuracy_forbidden": [],
            "expect_tools": [],
            "forbid_tools": [],
            "foreign_secrets": [],
        }
        from intelio.knowledge import foreign_markers
        marker = foreign_markers(client)[0]
        good = grade_output(case, f"{client} desk {facts['desk']} {facts['token']}")
        leaked = grade_output(
            case, f"{client} desk {facts['desk']} {facts['token']} {marker}",
        )
        if not good["passed"]:
            raise SystemExit(f"{client} pack ground truth failed: {good['reason']}")
        if leaked["dimensions"]["no_cross_client_leak"]:
            raise SystemExit(f"{client} foreign pack marker was not treated as a leak")


def grade_output(case: dict, output: str) -> dict:
    """Score one output. Each dimension is 1 or 0. ``passed`` requires all four."""
    text = output or ""
    tools = _tools(text)
    accuracy = all(piece in text for piece in case["must_mention"]) and all(
        piece not in text for piece in case.get("accuracy_forbidden") or []
    )
    tool_ok = set(case.get("expect_tools") or []).issubset(tools) and not (
        tools & set(case.get("forbid_tools") or [])
    )
    leaked = any(secret and secret in text for secret in case.get("foreign_secrets") or [])
    leaked = leaked or _pack_leak(str(case.get("client") or ""), text)
    if case.get("jarvis"):
        jarvis_ok = "read-only" in text.lower() and not (tools & JARVIS_BLOCKED_TOOLS)
    else:
        jarvis_ok = True
    dimensions = {
        "accuracy": accuracy,
        "tool_correctness": tool_ok,
        "no_cross_client_leak": not leaked,
        "jarvis_readonly": jarvis_ok,
    }
    failed = [name for name, ok in dimensions.items() if not ok]
    score = sum(1 for ok in dimensions.values() if ok) / len(dimensions)
    return {
        "passed": not failed,
        "score": score,
        "dimensions": dimensions,
        "reason": "passed" if not failed else "failed: " + ", ".join(failed),
    }


def promptfoo_tests(cases: list[dict] | None = None) -> list[dict]:
    tests = []
    for case in cases or build_cases():
        tests.append({
            "description": case["id"],
            "vars": {"id": case["id"], "prompt": case["prompt"], "reference": case["reference"]},
        })
    return tests


def write_cases(path: Path) -> None:
    payload = promptfoo_tests()
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def check_cases(path: Path) -> None:
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    expected = promptfoo_tests()
    if on_disk != expected:
        raise SystemExit(f"{path} does not match seeds.py; run grade.py --write-cases")


def self_test() -> None:
    cases = build_cases()
    if len(cases) != 100:
        raise SystemExit(f"expected 100 cases, found {len(cases)}")
    ids = [case["id"] for case in cases]
    if len(set(ids)) != len(ids):
        raise SystemExit("duplicate case id")
    for client in CLIENTS:
        count = sum(1 for case in cases if case["client"] == client)
        if count != 20:
            raise SystemExit(f"{client} has {count} cases, expected 20")
    for case in cases:
        good = grade_output(case, case["reference"])
        bad = grade_output(case, case["bad"])
        if not good["passed"]:
            raise SystemExit(f"{case['id']} reference failed: {good['reason']}")
        if bad["passed"]:
            raise SystemExit(f"{case['id']} bad output passed")
        if case["jarvis"] and not good["dimensions"]["jarvis_readonly"]:
            raise SystemExit(f"{case['id']} reference missed the jarvis dimension")
    check_cases(_HERE / "cases.json")
    _check_knowledge_ground_truth()
    print("intelio grader self-test passed: 100 cases, references pass, bad outputs fail")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Grade Intelio seed cases")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--write-cases", action="store_true")
    parser.add_argument("--check-cases", action="store_true")
    args = parser.parse_args(argv)
    if args.write_cases:
        write_cases(_HERE / "cases.json")
        print(f"wrote {_HERE / 'cases.json'}")
    if args.check_cases:
        check_cases(_HERE / "cases.json")
        print("cases.json matches seeds")
    if args.self_test:
        self_test()
    if not (args.write_cases or args.check_cases or args.self_test):
        parser.error("choose --self-test, --write-cases, or --check-cases")


if __name__ == "__main__":
    main()
