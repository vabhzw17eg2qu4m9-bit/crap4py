"""coverage.py JSON loader + per-method coverage attribution.

Expects the coverage.py JSON format::

    {"meta": ..., "totals": ...,
     "files": {"rel/path.py": {"executed_lines": [...], "missing_lines": [...], ...}}}
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

PathLike = str | Path


@dataclass
class FileCoverage:
    """Executed and missing line numbers for a single source file."""

    executed_lines: set[int] = field(default_factory=set)
    missing_lines: set[int] = field(default_factory=set)


def load_coverage(path: PathLike, project_root: PathLike | None = None) -> dict[str, FileCoverage]:
    """Load a coverage.py JSON report. Returns ``{}`` (with a warning) if absent."""
    p = Path(path)
    if not p.exists():
        print(
            f"Warning: coverage file not found at {p}. Coverage will be N/A.",
            file=sys.stderr,
        )
        return {}
    with p.open() as fh:
        return parse_coverage_dict(json.load(fh), project_root)


def parse_coverage_dict(
    data: dict, project_root: PathLike | None = None
) -> dict[str, FileCoverage]:
    """Build the ``{rel_path: FileCoverage}`` map from a parsed coverage.py JSON dict."""
    root = Path(project_root).resolve() if project_root is not None else None
    result: dict[str, FileCoverage] = {}
    for key, info in (data.get("files") or {}).items():
        rel = _relativize(key, root)
        result[rel] = FileCoverage(
            executed_lines=set(info.get("executed_lines", [])),
            missing_lines=set(info.get("missing_lines", [])),
        )
    return result


def _relativize(key: str, root: Path | None) -> str:
    """Normalize a coverage file key to a project-root-relative path."""
    if root is None:
        return key
    p = Path(key)
    if not p.is_absolute():
        return key
    try:
        return str(p.resolve().relative_to(root))
    except ValueError:
        return key  # outside project root — left as-is; ignored at lookup time


def coverage_for_method(fc: FileCoverage | None, start_line: int, end_line: int) -> float | None:
    """Coverage fraction within ``[start_line, end_line]``, or ``None`` if no records."""
    if fc is None:
        return None
    line_range = set(range(start_line, end_line + 1))
    executed = fc.executed_lines & line_range
    missing = fc.missing_lines & line_range
    total = executed | missing
    if not total:
        return None
    return len(executed) / len(total)
