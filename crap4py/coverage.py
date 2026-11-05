"""coverage.py JSON loader + per-method coverage attribution.

Expects the coverage.py JSON format::

    {"meta": ..., "totals": ...,
     "files": {"rel/path.py": {"executed_lines": [...], "missing_lines": [...], ...}}}

A path whose file name ends with ``.info`` is parsed as an lcov tracefile
(``coverage lcov`` output) instead: one statement per ``DA:<line>,<hits>``
record, ``SF:`` keys resolved against the project root, function and branch
records ignored.
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
    """Load a coverage report (JSON, or lcov for ``*.info``).

    Returns ``{}`` (with a warning) if absent.
    """
    p = Path(path)
    if not p.exists():
        print(
            f"Warning: coverage file not found at {p}. Coverage will be N/A.\n"
            "Hint: generate coverage first — `coverage run -m pytest && coverage json`, "
            "or run `crap4py --run-tests` to do it automatically.",
            file=sys.stderr,
        )
        return {}
    return load_coverage_records(p, project_root)


def load_coverage_records(
    path: PathLike, project_root: PathLike | None = None
) -> dict[str, FileCoverage]:
    """Parse a coverage report, raising OSError/ValueError on IO/parse failures.

    The strict preflight (``--strict``) uses this to report the exact data
    problem instead of silently analyzing without coverage.
    """
    p = Path(path)
    if p.name.endswith(".info"):
        return parse_lcov(p.read_text(encoding="utf-8"), project_root)
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


def parse_lcov(text: str, project_root: PathLike | None = None) -> dict[str, FileCoverage]:
    """Build the ``{rel_path: FileCoverage}`` map from an lcov tracefile.

    Each ``DA:<line>,<hits>`` record is one statement (hit when ``hits > 0``);
    duplicate ``SF:`` sections for one file sum their hits per line. ``FN``,
    ``FNDA``, ``BRDA``, ``LF`` and ``LH`` records are ignored, as is any
    record type this port does not know.
    """
    root = Path(project_root).resolve() if project_root is not None else None
    result: dict[str, FileCoverage] = {}
    rel: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("SF:"):
            rel = _lcov_key(line[3:], root)
        elif line.startswith("DA:") and rel is not None:
            lineno, hits = _parse_da(line)
            _merge_da(result.setdefault(rel, FileCoverage()), lineno, hits)
    return result


def _merge_da(fc: FileCoverage, lineno: int, hits: int) -> None:
    """Sum one ``DA:`` record into ``fc``: any hit marks the line executed."""
    if hits > 0:
        fc.executed_lines.add(lineno)
        fc.missing_lines.discard(lineno)
    elif lineno not in fc.executed_lines:
        fc.missing_lines.add(lineno)


def _lcov_key(sf_path: str, root: Path | None) -> str:
    """Resolve an ``SF:`` path to a coverage-map key.

    Relative paths resolve against the project root; absolute paths go
    through the same relativization as the JSON keys.
    """
    if root is None:
        return sf_path
    p = Path(sf_path)
    if not p.is_absolute():
        p = root / p
    return _relativize(str(p), root)


def _parse_da(line: str) -> tuple[int, int]:
    """The ``(line, hits)`` of a ``DA:`` record; ValueError when malformed."""
    parts = line[3:].split(",")
    if len(parts) < 2:
        raise ValueError(f"malformed lcov DA record: {line!r}")
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(f"malformed lcov DA record: {line!r}") from None


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
