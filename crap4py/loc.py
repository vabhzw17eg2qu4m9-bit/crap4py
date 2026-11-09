"""``loc`` subcommand: flags files longer than the line limit.

Port of the crap4dart ``loc`` gate: a file holding more than
``--max-lines`` (default 800) lines is a violation, reported per file as
``<n> lines > max <m>``. ``--exclude`` globs (fnmatch on the
project-relative path, repeatable) drop files from the check entirely.
"""

from __future__ import annotations

import fnmatch
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .analyzer import _relative_to_root
from .args import UsageErrorParser
from .constants import NO_FILES_MESSAGE
from .files import PathLike, expand_paths, find_source_files, is_test_file
from .ignore_filter import filter_ignored

MAX_LINES = 800


@dataclass(frozen=True, slots=True)
class LocViolation:
    """A single over-long file finding."""

    file: str
    lines: int
    max_lines: int


@dataclass(frozen=True, slots=True)
class LocResult:
    """Violations (over-long files) plus how many files were checked."""

    violations: list[LocViolation]
    checked: int


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py loc [paths...]``. Exit 2 iff violations."""
    args = _build_parser().parse_args(argv)
    files = (
        expand_paths(args.paths, project_root) if args.paths else find_source_files(project_root)
    )
    if not files:
        print(NO_FILES_MESSAGE)
        return 0
    result = check_files(files, project_root, args.max_lines, args.exclude)
    result = filter_ignored("loc", result, project_root)
    for violation in result.violations:
        print(f"{violation.file}: {violation.lines} lines > max {violation.max_lines}")
    print(summary(result, args.max_lines))
    return 2 if result.violations else 0


def check_files(
    files: Iterable[PathLike],
    project_root: PathLike,
    max_lines: int = MAX_LINES,
    excludes: Iterable[str] = (),
) -> LocResult:
    """Check every non-test file's line count against ``max_lines``."""
    root = Path(project_root)
    violations: list[LocViolation] = []
    checked = 0
    for file_path in files:
        p = Path(file_path)
        if is_test_file(p, root):
            continue
        rel = _relative_to_root(p, root)
        if any(fnmatch.fnmatch(rel, pattern) for pattern in excludes):
            continue
        lines = _line_count(p)
        if lines is None:
            continue
        checked += 1
        if lines > max_lines:
            violations.append(LocViolation(rel, lines, max_lines))
    return LocResult(violations, checked)


def summary(result: LocResult, max_lines: int = MAX_LINES) -> str:
    """One-line summary printed after the violations."""
    if result.violations:
        return f"{len(result.violations)}/{result.checked} files over their line limit"
    return f"{result.checked} files within {max_lines} lines"


def _line_count(path: Path) -> int | None:
    """Line count of ``path``; None when unreadable (a warning is printed)."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"Warning: could not read {path}: {exc}", file=sys.stderr)
        return None
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py loc",
        description="Flag files longer than the line limit.",
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument(
        "--max-lines",
        type=int,
        default=MAX_LINES,
        help=f"maximum allowed lines per file (default: {MAX_LINES})",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="skip files whose project-relative path matches this fnmatch glob (repeatable)",
    )
    parser.add_argument(
        "paths", nargs="*", help="explicit files or directories (default: normal selection)"
    )
    return parser
