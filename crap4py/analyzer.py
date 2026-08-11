"""Combine parsing, complexity, and coverage into per-method CRAP metrics."""

from __future__ import annotations

import sys
from pathlib import Path

from .complexity import extract_methods
from .coverage import coverage_for_method, load_coverage
from .crap import MethodMetric, crap_score

PathLike = str | Path


def analyze(
    file_paths: list[PathLike], coverage_path: PathLike, project_root: PathLike
) -> list[MethodMetric]:
    """Parse each file, attribute coverage, and compute CRAP for every method."""
    root = Path(project_root).resolve()
    coverage = load_coverage(coverage_path, root)
    metrics: list[MethodMetric] = []
    for file_path in file_paths:
        p = Path(file_path)
        rel = _relative_to_root(p, root)
        try:
            source = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"Warning: could not read {file_path}: {exc}", file=sys.stderr)
            continue
        for desc in extract_methods(source, filename=str(p)):
            fc = coverage.get(rel)
            cov = coverage_for_method(fc, desc.start_line, desc.end_line)
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
    try:
        return str(p.resolve().relative_to(root))
    except ValueError:
        return str(p)
