"""``--run-tests`` driver: ``coverage run -m pytest`` (fallback to unittest)."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

PathLike = str | Path


def run_tests(project_root: PathLike) -> None:
    """Run the test suite under coverage, emitting ``coverage.json``.

    Tries ``coverage run -m pytest`` first; on any failure falls back to
    ``coverage run -m unittest discover``. Raises ``RuntimeError`` if both fail.
    """
    root = str(project_root)
    pytest_result = _run(["coverage", "run", "-m", "pytest"], root)
    if pytest_result.returncode == 0:
        _emit_json(root)
        return
    unittest_result = _run(["coverage", "run", "-m", "unittest", "discover"], root)
    if unittest_result.returncode == 0:
        _emit_json(root)
        return
    raise RuntimeError(
        "Test runs failed under both pytest and unittest.\n"
        f"--- pytest stdout ---\n{pytest_result.stdout}\n"
        f"--- pytest stderr ---\n{pytest_result.stderr}\n"
        f"--- unittest stdout ---\n{unittest_result.stdout}\n"
        f"--- unittest stderr ---\n{unittest_result.stderr}"
    )


def _run(cmd: Sequence[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)


def _emit_json(cwd: str) -> None:
    result = _run(["coverage", "json", "-o", "coverage.json"], cwd)
    if result.returncode != 0:
        raise RuntimeError(f"coverage json failed:\n{result.stdout}\n{result.stderr}")
