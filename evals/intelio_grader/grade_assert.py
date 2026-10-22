"""Promptfoo assertion: the intelio rubric must pass the provider output."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import grade
import seeds


def get_assert(output, context):
    variables = (context or {}).get("vars") or {}
    case = seeds.by_id(variables["id"])
    result = grade.grade_output(case, output or "")
    return {"pass": result["passed"], "score": result["score"], "reason": result["reason"]}
