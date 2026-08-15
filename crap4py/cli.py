"""``crap4py`` CLI entry point — parses argv, orchestrates analysis, returns exit code."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .analyzer import analyze
from .args import UsageErrorParser
from .files import changed_files, expand_paths, find_source_files
from .report import format_report
from .runtests import run_tests

DEFAULT_THRESHOLD = 8.0
DEFAULT_COVERAGE = "coverage.json"

_SUBCOMMANDS = (
    "profile",
    "skill",
    "file-naming",
    "nesting",
    "class-size",
    "weight-of-class",
    "unused-code",
    "unused-files",
    "banned-imports",
    "magic-constants",
)

_USAGE = """\
Usage:
  crap4py                      Analyze all Python files under src/ (else .).
  crap4py --changed            Analyze git-changed Python files.
  crap4py <path>...            Analyze explicit files; directories expand to .py files.
  crap4py profile [opts] [p..] Run tests against instrumented source; report timings.
  crap4py file-naming [path]   Check source file names for mechanical names.
  crap4py nesting [path]       Check functions for nesting deeper than 5 levels.
  crap4py class-size [path]    Check classes for >25 methods or WMC >80.
  crap4py weight-of-class [p.] Check classes for public data weight >0.33.
  crap4py unused-code [path]   Check for unused private module declarations.
  crap4py unused-files [path]  Check for source files never imported.
  crap4py banned-imports [..]  Enforce --from/--forbid import boundaries.
  crap4py magic-constants [..] Flag hex colors outside constants and repeated literals.
  crap4py skill                Print the crap4py profiling skill.
  crap4py --help               Print this help message.

Options:
  --coverage <path>            Coverage file (default: coverage.json).
  --threshold <num>            CRAP threshold (default: 8.0).
  --run-tests                  Run tests under coverage before analyzing.
  --version                    Print version and exit.

Subcommands are recognized only as the first argument; anything else
(including flags and paths) is analyzed as above.
"""


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py",
        description="CRAP (Change Risk Anti-Patterns) metric for Python — crap4java port.",
        add_help=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_USAGE,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="print version and exit",
    )
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
    args_list = list(sys.argv[1:] if argv is None else argv)
    dispatched = _dispatch_subcommand(args_list, Path.cwd())
    if dispatched is not None:
        return dispatched
    args = _build_parser().parse_args(args_list)
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


def _dispatch_subcommand(args_list: list[str], project_root: Path) -> int | None:
    """Run a subcommand when the first argument names one; else None (analyze)."""
    if not args_list or args_list[0] not in _SUBCOMMANDS:
        return None
    from . import (
        banned_imports,
        class_size,
        file_naming,
        magic_constants,
        nesting,
        profile,
        skill,
        unused_code,
        unused_files,
        weight_of_class,
    )

    handlers = {
        "profile": profile.run,
        "skill": skill.run,
        "file-naming": file_naming.run,
        "nesting": nesting.run,
        "class-size": class_size.run,
        "weight-of-class": weight_of_class.run,
        "unused-code": unused_code.run,
        "unused-files": unused_files.run,
        "banned-imports": banned_imports.run,
        "magic-constants": magic_constants.run,
    }
    return handlers[args_list[0]](args_list[1:], project_root)
