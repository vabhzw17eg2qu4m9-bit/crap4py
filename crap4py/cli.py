"""``crap4py`` CLI entry point — parses argv, orchestrates analysis, returns exit code."""

from __future__ import annotations

import argparse
import fnmatch
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .analyzer import _relative_to_root, analyze
from .args import UsageErrorParser
from .constants import DEFAULT_COVERAGE
from .coverage import load_coverage_records
from .files import changed_files, expand_paths, find_source_files
from .mutation import load_changed_lines
from .report import format_report
from .runtests import run_tests

DEFAULT_THRESHOLD = 8.0

_SUBCOMMANDS = (
    "profile",
    "skill",
    "file-naming",
    "complexity",
    "nesting",
    "class-size",
    "weight-of-class",
    "unused-code",
    "unused-files",
    "banned-imports",
    "magic-constants",
    "scattered-constants",
    "test-assertions",
    "folder-structure",
    "duplicates",
    "mutation",
    "similarity",
    "memory",
    "tuple-signatures",
    "reorder",
    "string-hazards",
    "identical-branches",
    "enum-name-contract",
    "collapsible-flow",
)

_USAGE = """\
Usage:
  crap4py                      Analyze all Python files under src/ (else .).
  crap4py --changed            Analyze git-changed Python files.
  crap4py <path>...            Analyze explicit files; directories expand to .py files.
  crap4py --diff-base <ref>    Analyze only methods intersecting the diff vs <ref>.
  crap4py profile [opts] [p..] Run tests against instrumented source; report timings.
  crap4py file-naming [path]   Check source file names for mechanical names.
  crap4py complexity [opts] [p..] Flag methods over the complexity limit.
  crap4py nesting [path]       Check functions for nesting deeper than 5 levels.
  crap4py class-size [path]    Check classes for >25 methods or WMC >80.
  crap4py weight-of-class [p.] Check classes for public data weight >0.33.
  crap4py unused-code [path]   Check for unused private module declarations.
  crap4py unused-files [path]  Check for source files never imported.
  crap4py banned-imports [..]  Enforce --from/--forbid import boundaries.
  crap4py magic-constants [..] Flag hex colors outside constants and repeated literals.
  crap4py scattered-constants  Flag literals repeated across 3+ distinct files.
  crap4py test-assertions [..] Flag test bodies without assertion calls.
  crap4py duplicates [opts] [p..] Flag files whose duplicated lines exceed a threshold.
  crap4py mutation [opts] [p..]  Mutation testing: re-run tests per mutated operator.
  crap4py similarity [opts] [d..] Scan for near-duplicate files (console/json); exits 0.
  crap4py memory [opts] [p..]   Run tests under tracemalloc; per-test churn report, exits 0.
  crap4py tuple-signatures [p.] Flag inline tuple annotations in function signatures.
  crap4py reorder [-n] [p..]   Rewrite class bodies into canonical member order.
  crap4py string-hazards [p.]  Flag escaped f-strings, regex anchors, literal-pattern anchors.
  crap4py identical-branches [p.] Flag duplicate conditions/bodies/operands/args in one construct.
  crap4py enum-name-contract [p.] Flag built-in .name contracts inside enum bodies.
  crap4py collapsible-flow [p.]  Flag nested ifs to merge and redundant/flattenable else chains.
  crap4py skill                Print the crap4py profiling skill.
  crap4py --help               Print this help message.

Options:
  --coverage <path>            Coverage file (default: coverage.json); *.info parses as lcov.
  --threshold <num>            CRAP threshold (default: 8.0).
  --diff-base <ref>            Only methods touching lines added/changed vs this git ref.
  --exclude <glob>             Skip files whose project-relative path matches (repeatable).
  --strict                     Exit 1 when coverage is missing, unreadable, empty, or unparseable.
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
    parser.add_argument(
        "--diff-base",
        default=None,
        metavar="REF",
        help="only methods touching lines added/changed vs this git ref",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail when coverage data is missing, unreadable, empty, or unparseable",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="skip files whose project-relative path matches this fnmatch glob (repeatable)",
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
        return _usage_error("--changed cannot be combined with file arguments")
    if args.changed and args.diff_base is not None:
        return _usage_error("--changed cannot be combined with --diff-base")

    if args.run_tests:
        try:
            run_tests(project_root)
        except (RuntimeError, OSError) as exc:
            print(str(exc), file=sys.stderr)
            return 1

    if args.strict:
        problem = _strict_error(args)
        if problem is not None:
            print(problem, file=sys.stderr)
            return 1

    diff = None
    if args.diff_base is not None:
        diff = load_changed_lines(project_root, args.diff_base, paths=args.paths or None)
        if diff is None:
            return 1  # load_changed_lines already reported the git failure

    try:
        files = _select_files(args, project_root, diff)
    except (RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not files:
        print("No Python files to analyze.")
        return 0

    try:
        metrics = analyze(
            [str(f) for f in files],
            args.coverage,
            project_root,
            diff=diff,
            strict=args.strict,
        )
    except (OSError, ValueError) as exc:
        print(f"Error analyzing files: {exc}", file=sys.stderr)
        return 1

    print(format_report(metrics, args.threshold))

    max_crap = max((m.crap_score for m in metrics if m.crap_score is not None), default=0.0)
    if max_crap > args.threshold:
        print(f"CRAP threshold exceeded: {max_crap:.1f} > {args.threshold:.1f}", file=sys.stderr)
        return 2
    return 0


def _usage_error(message: str) -> int:
    sys.stderr.write(_USAGE)
    sys.stderr.write(f"error: {message}\n")
    return 1


def _strict_error(args: argparse.Namespace) -> str | None:
    """The strict-mode data problem, or None when the inputs are usable."""
    for path in args.paths:
        if not Path(path).exists():
            return f"Error: no such path: {path}"
    try:
        records = load_coverage_records(Path(args.coverage))
    except OSError as exc:
        return f"Error: cannot read coverage file {args.coverage}: {exc}"
    except ValueError as exc:
        return f"Error: cannot parse coverage file {args.coverage}: {exc}"
    if not records:
        return f"Error: no coverage records in {args.coverage}"
    return None


def _select_files(
    args: argparse.Namespace, project_root: Path, diff: dict[str, set[int]] | None
) -> list[Path]:
    if args.changed:
        files = changed_files(project_root)
    elif args.paths:
        files = expand_paths(args.paths, project_root)
    else:
        files = find_source_files(project_root)
    files = _apply_excludes(files, project_root, args.exclude)
    if diff is not None:
        files = [f for f in files if diff.get(_relative_to_root(f, project_root))]
    return files


def _apply_excludes(files: list[Path], project_root: Path, excludes: list[str]) -> list[Path]:
    """Drop files whose project-relative path matches any --exclude glob."""
    if not excludes:
        return files
    return [
        f
        for f in files
        if not any(
            fnmatch.fnmatch(_relative_to_root(f, project_root), pattern) for pattern in excludes
        )
    ]


def _dispatch_subcommand(args_list: list[str], project_root: Path) -> int | None:
    """Run a subcommand when the first argument names one; else None (analyze)."""
    if not args_list or args_list[0] not in _SUBCOMMANDS:
        return None
    from . import (
        banned_imports,
        class_size,
        collapsible_flow,
        complexity_gate,
        duplicates,
        enum_name_contract,
        file_naming,
        folder_structure,
        identical_branches,
        magic_constants,
        memory,
        mutation,
        nesting,
        profile,
        reorder,
        scattered_constants,
        similarity,
        skill,
        string_hazards,
        test_assertions,
        tuple_signatures,
        unused_code,
        unused_files,
        weight_of_class,
    )

    handlers = {
        "profile": profile.run,
        "skill": skill.run,
        "file-naming": file_naming.run,
        "complexity": complexity_gate.run,
        "nesting": nesting.run,
        "class-size": class_size.run,
        "weight-of-class": weight_of_class.run,
        "unused-code": unused_code.run,
        "unused-files": unused_files.run,
        "banned-imports": banned_imports.run,
        "magic-constants": magic_constants.run,
        "scattered-constants": scattered_constants.run,
        "test-assertions": test_assertions.run,
        "folder-structure": folder_structure.run,
        "duplicates": duplicates.run,
        "mutation": mutation.run,
        "similarity": similarity.run,
        "memory": memory.run,
        "tuple-signatures": tuple_signatures.run,
        "reorder": reorder.run,
        "string-hazards": string_hazards.run,
        "identical-branches": identical_branches.run,
        "enum-name-contract": enum_name_contract.run,
        "collapsible-flow": collapsible_flow.run,
    }
    return handlers[args_list[0]](args_list[1:], project_root)
