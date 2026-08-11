"""Tabular CRAP report formatter (see shared-contract.md §4)."""

from __future__ import annotations

from .analyzer import sort_metrics
from .crap import MethodMetric

_HEADER = f"{'Method':<30} {'File':<35} {'CC':>4} {'Cov%':>7} {'CRAP':>8}"


def format_report(metrics: list[MethodMetric], threshold: float) -> str:
    """Render the full CRAP report string, including the threshold verdict line."""
    rows = sort_metrics(metrics)
    lines = ["CRAP Report", "===========", _HEADER, "-" * len(_HEADER)]
    for m in rows:
        lines.append(_format_row(m))
    lines.append("")
    max_crap = _max_numeric_crap(rows)
    verdict = "FAILED" if max_crap > threshold else "passed"
    lines.append(f"Max CRAP: {max_crap:.1f} (threshold {threshold:.1f}) — {verdict}")
    return "\n".join(lines)


def _format_row(m: MethodMetric) -> str:
    cov = "N/A" if m.coverage is None else f"{m.coverage * 100:.1f}%"
    crap = "N/A" if m.crap_score is None else f"{m.crap_score:.1f}"
    return f"{m.method_name:<30} {m.file:<35} {m.complexity:>4} {cov:>7} {crap:>8}"


def _max_numeric_crap(metrics: list[MethodMetric]) -> float:
    """Maximum numeric CRAP score, or 0.0 when no numeric scores exist."""
    return max((m.crap_score for m in metrics if m.crap_score is not None), default=0.0)
