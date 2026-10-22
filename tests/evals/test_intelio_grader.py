"""The seed grader passes its own references and fails its own bad outputs."""

import subprocess
import sys
from pathlib import Path


def test_grader_self_test():
    script = Path(__file__).resolve().parents[2] / "evals" / "intelio_grader" / "grade.py"
    completed = subprocess.run([sys.executable, str(script), "--self-test"], check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert "100 cases" in completed.stdout
