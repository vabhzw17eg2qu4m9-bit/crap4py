"""``crap4py`` CLI entry point — parses argv, orchestrates analysis, returns exit code."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .analyzer import analyze
from .files import changed_files, expand_paths, find_source_files
from .report import format_report
from .runtests import run_tests

DEFAULT_THRESHOLD = 8.0
DEFAULT_COVERAGE = "coverage.json"

_USAGE = """\
Usage:
  crap4py                      Analyze all Python files under src/ (else .).
  crap4py --changed            Analyze git-changed Python files.
  crap4py <path>...            Analyze explicit files; directories expand to .py files.
  crap4py --help               Print this help message.

Options:
  --coverage <path>            Coverage file (default: coverage.json).
  --threshold <num>            CRAP threshold (default: 8.0).
  --run-tests                  Run tests under coverage before analyzing.
"""


class _ArgumentParser(argparse.ArgumentParser):
    """Usage errors exit 1 (not argparse's default 2, which we reserve for threshold)."""

    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{self.prog}: error: {message}\n")
        raise SystemExit(1)


def _build_parser() -> _ArgumentParser:
    parser = _ArgumentParser(
        prog="crap4py",
        description="CRAP (Change Risk Anti-Patterns) metric for Python — crap4java port.",
        add_help=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_USAGE,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument("--changed", action="store_true", help="analyze git-changed files only")
    parser.add_argument(
        "--coverage", default=DEFAULT_COVERAGE, help=f"coverage file (default: {DEFAULT_COVERAGE})"
    )
    parser.add_argument(
        "--threshold", type=float, default=DEFAULT_THRESHOLD, help="CRAP threshold (default: 8.0)"
    )
    parser.add_argument("--run-tests", action="store_true", help="run tests under coverage first")
    parser.add_argument("paths", nargs="*", help="explicit files or directories to analyze")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI. Returns 0 (ok), 1 (usage/IO error), or 2 (CRAP threshold exceeded)."""
    args = _build_parser().parse_args(argv)
    project_root = Path.cwd()

    if args.changed and args.paths:
        sys.stderr.write(_USAGE)
        sys.stderr.write("error: --changed cannot be combined with file arguments\n")
        return 1

    if args.run_tests:
        try:
            run_tests(project_root)
        except (RuntimeError, OSError) as exc:
            print(str(exc), file=sys.stderr)
            return 1

    try:
        files = _select_files(args, project_root)
    except (RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not files:
        print("No Python files to analyze.")
        return 0

    try:
        metrics = analyze([str(f) for f in files], args.coverage, project_root)
    except (OSError, ValueError) as exc:
        print(f"Error analyzing files: {exc}", file=sys.stderr)
        return 1

    print(format_report(metrics, args.threshold))

    max_crap = max((m.crap_score for m in metrics if m.crap_score is not None), default=0.0)
    if max_crap > args.threshold:
        print(f"CRAP threshold exceeded: {max_crap:.1f} > {args.threshold:.1f}", file=sys.stderr)
        return 2
    return 0


def _select_files(args: argparse.Namespace, project_root: Path) -> list[Path]:
    if args.changed:
        return changed_files(project_root)
    if args.paths:
        return expand_paths(args.paths, project_root)
    return find_source_files(project_root)
