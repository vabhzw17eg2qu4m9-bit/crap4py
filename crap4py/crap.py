"""CRAP formula and the dataclasses shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MethodDescriptor:
    """A parsed method and its cyclomatic complexity, before coverage attribution."""

    name: str
    start_line: int
    end_line: int
    complexity: int


@dataclass(frozen=True, slots=True)
class MethodMetric:
    """A method's complexity, attributed coverage, and final CRAP score."""

    method_name: str
    file: str
    complexity: int
    coverage: float | None
    crap_score: float | None


def crap_score(cc: int, coverage: float | None) -> float | None:
    """Return CRAP = cc^2 * (1 - coverage)^3 + cc, or None when coverage is None."""
    if coverage is None:
        return None
    uncovered = 1.0 - coverage
    return cc * cc * uncovered**3 + cc
