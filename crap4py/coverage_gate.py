"""``test-coverage`` subcommand: fails below-minimum total coverage.

Port of the crap4dart ``test_coverage`` gate over the coverage input the
CRAP command already parses (coverage.py JSON or lcov ``.info``): the
statement-hit ratio — executed lines over executed+missing lines,
aggregated across files — must reach ``--min-percent`` (default 80.0).
``--exclude`` globs drop files from the ratio. Coverage records outside
the project root (system packages, caches) never count toward the
aggregate (upstream spec 11.2); an all-foreign input fails closed.
Missing, unreadable, unparseable, or record-free coverage also fails
closed (exit 1), matching the CRAP command's require-coverage semantics.
"""

from __future__ import annotations

import fnmatch
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .args import UsageErrorParser
from .constants import DEFAULT_COVERAGE
from .coverage import FileCoverage, load_coverage_records

MIN_PERCENT = 80.0

_COV_HINT = (
    "generate coverage first — `coverage run -m pytest && coverage json`, "
    "or run `crap4py --run-tests` to do it automatically"
)


@dataclass(frozen=True, slots=True)
class CoverageResult:
    """The total-coverage verdict: one violation when below the minimum."""

    violations: list[str]
    percent: float


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py test-coverage``. Exit 2 below the minimum."""
    args = _build_parser().parse_args(argv)
    path = Path(args.coverage)
    if not path.is_absolute():
        path = project_root / path
    problem = _input_error(path)
    if problem is not None:
        print(
            f"Error: {problem}. Unit-test coverage is required.\nHint: {_COV_HINT}.",
            file=sys.stderr,
        )
        return 1
    records = load_coverage_records(path, project_root)
    records = _local_records(records)
    if not records:
        print(
            f"Error: no coverage records for files inside {project_root} in {args.coverage}. "
            f"Unit-test coverage is required.\nHint: {_COV_HINT}.",
            file=sys.stderr,
        )
        return 1
    result = check_coverage(records, args.min_percent, args.exclude)
    for violation in result.violations:
        print(f"{args.coverage}: {violation}")
    print(summary(result, args.min_percent))
    return 2 if result.violations else 0


def check_coverage(
    records: dict[str, FileCoverage],
    min_percent: float = MIN_PERCENT,
    excludes: Iterable[str] = (),
) -> CoverageResult:
    """Fail when the statement-hit ratio over all records is below ``min_percent``."""
    percent = total_ratio(records, excludes)
    violation = (
        None
        if percent >= min_percent
        else f"total coverage {percent:.1f}% < min {min_percent:.1f}%"
    )
    return CoverageResult([violation] if violation else [], percent)


def total_ratio(records: dict[str, FileCoverage], excludes: Iterable[str] = ()) -> float:
    """Statement-hit percentage across all records (100.0 with no statements)."""
    executed = 0
    total = 0
    for rel, fc in records.items():
        if any(fnmatch.fnmatch(rel, pattern) for pattern in excludes):
            continue
        executed += len(fc.executed_lines)
        total += len(fc.executed_lines) + len(fc.missing_lines)
    return 100.0 if total == 0 else executed / total * 100.0


def _local_records(records: dict[str, FileCoverage]) -> dict[str, FileCoverage]:
    """The records whose keys are inside the project root.

    Coverage keys come back project-root-relative: a key that is still
    absolute was resolved outside the root (system packages, caches) and
    never counts toward the aggregate (upstream spec 11.2; the js port's
    ``toProjectRelative`` null rule).
    """
    return {key: fc for key, fc in records.items() if not Path(key).is_absolute()}


def summary(result: CoverageResult, min_percent: float = MIN_PERCENT) -> str:
    """One-line summary printed after the violations."""
    op = "<" if result.violations else ">="
    return f"coverage {result.percent:.1f}% {op} {min_percent:.1f}%"


def _input_error(path: Path) -> str | None:
    """The coverage input problem, or None when the data is usable."""
    try:
        records = load_coverage_records(path)
    except FileNotFoundError:
        return f"coverage file not found at {path}"
    except OSError as exc:
        return f"cannot read coverage file {path}: {exc}"
    except ValueError as exc:
        return f"cannot parse coverage file {path}: {exc}"
    if not records:
        return f"no coverage records in {path}"
    return None


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py test-coverage",
        description="Fail when total coverage is below the minimum percent.",
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument(
        "--min-percent",
        type=float,
        default=MIN_PERCENT,
        help=f"minimum total coverage percent (default: {MIN_PERCENT})",
    )
    parser.add_argument(
        "--coverage",
        default=DEFAULT_COVERAGE,
        metavar="PATH",
        help=f"coverage file (default: {DEFAULT_COVERAGE}); *.info parses as lcov",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="drop files matching this fnmatch glob from the ratio (repeatable)",
    )
    return parser
