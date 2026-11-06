"""Combine parsing, complexity, and coverage into per-method CRAP metrics."""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path

from .complexity import extract_methods
from .coverage import FileCoverage, coverage_for_method, load_coverage_records
from .crap import MethodDescriptor, MethodMetric, crap_score

PathLike = str | Path


def analyze(
    file_paths: list[PathLike],
    coverage_path: PathLike,
    project_root: PathLike,
    diff: dict[str, set[int]] | None = None,
    strict: bool = False,
) -> list[MethodMetric]:
    """Parse each file, attribute coverage, and compute CRAP for every method.

    ``diff`` (as produced by ``mutation.load_changed_lines``) keeps only the
    methods whose span intersects the changed lines of their file; ``strict``
    scores a selected file with no coverage entry as 0.0% instead of N/A.
    Raises OSError/ValueError for a missing, unreadable, or unparseable
    coverage file.
    """
    root = Path(project_root).resolve()
    coverage = load_coverage_records(coverage_path, root)
    metrics: list[MethodMetric] = []
    for file_path in file_paths:
        metrics.extend(_analyze_file(file_path, root, coverage, diff, strict))
    return metrics


def _analyze_file(
    file_path: PathLike,
    root: Path,
    coverage: dict[str, FileCoverage],
    diff: dict[str, set[int]] | None,
    strict: bool,
) -> list[MethodMetric]:
    """One file's metrics; unreadable files warn on stderr and yield none."""
    p = Path(file_path)
    rel = _relative_to_root(p, root)
    try:
        source = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"Warning: could not read {file_path}: {exc}", file=sys.stderr)
        return []
    fc = coverage.get(rel)
    metrics = []
    for desc in extract_methods(source, filename=str(p)):
        if diff is not None and not _intersects_changed(diff.get(rel, ()), desc):
            continue
        cov = (
            0.0
            if strict and fc is None
            else coverage_for_method(fc, desc.start_line, desc.end_line)
        )
        metrics.append(
            MethodMetric(
                method_name=desc.name,
                file=rel,
                complexity=desc.complexity,
                coverage=cov,
                crap_score=crap_score(desc.complexity, cov),
            )
        )
    return metrics


def _intersects_changed(changed: Iterable[int], desc: MethodDescriptor) -> bool:
    """Whether ``desc``'s span contains a changed line (empty set -> False)."""
    return any(desc.start_line <= line <= desc.end_line for line in changed)


def sort_metrics(metrics: list[MethodMetric]) -> list[MethodMetric]:
    """Sort by CRAP desc (None last); tie-break by file asc, method_name asc."""
    return sorted(
        metrics,
        key=lambda m: (
            m.crap_score is None,
            -(m.crap_score or 0.0),
            m.file,
            m.method_name,
        ),
    )


def _relative_to_root(p: Path, root: Path) -> str:
    """Path relative to ``root``, or the original path when it lies outside.

    Both sides are resolved: resolving only ``p`` made the comparison
    asymmetric, so a caller passing an unresolved root (a symlinked path such
    as macOS's ``/var`` -> ``/private/var``) got the absolute path back
    instead of a relative one.
    """
    try:
        resolved_root = Path(root).resolve()
    except (OSError, RuntimeError):
        # A symlink loop or an unreadable parent leaves the root unresolvable;
        # comparing against it as given is what this function did before, and
        # is still better than failing outright.
        resolved_root = Path(root)
    try:
        return str(p.resolve().relative_to(resolved_root))
    except ValueError:
        return str(p)
