"""``unused-files`` subcommand: flags source files nobody imports.

Port of the crap4dart ``unused_files`` gate: non-test files never imported
by any other analyzed non-test file. Relative imports resolve against the
importing file's directory, absolute/dotted imports against the package
root (``src/`` when present); stdlib and external imports never resolve.
``__init__.py`` and ``__main__.py`` are exempt.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .analyzer import _relative_to_root
from .args import UsageErrorParser
from .files import PathLike, find_source_files, is_test_file, parse_file
from .imports import imported_paths, package_root

_EXEMPT_NAMES = frozenset({"__init__.py", "__main__.py"})


@dataclass(frozen=True, slots=True)
class FileResult:
    """Violations (never-imported files) plus how many files were checked."""

    violations: list[str]
    checked: int


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py unused-files [paths...]``. Exit 2 iff violations.

    Reachability over a partial selection yields false positives, so explicit
    paths make the check skip (crap4dart 0.5.1 behavior).
    """
    args = _build_parser().parse_args(argv)
    if args.paths:
        print("unused-files: not meaningful for a partial selection")
        return 0
    result = check_files(find_source_files(project_root), project_root)
    for rel in result.violations:
        print(f"{rel}: never imported by any analyzed source file")
    print(summary(result))
    return 2 if result.violations else 0


def check_files(files: Iterable[PathLike], project_root: PathLike) -> FileResult:
    """Flag non-test files never imported by any other analyzed non-test file."""
    root = Path(project_root).resolve()
    pkg_root = package_root(root)
    sources = [Path(p) for p in files if not is_test_file(p, root)]
    imported = set()
    for path in sources:
        tree = parse_file(path)
        if tree is not None:
            imported |= imported_paths(tree, path.resolve(), pkg_root)
    violations = [
        _relative_to_root(p, root)
        for p in sources
        if p.name not in _EXEMPT_NAMES and p.resolve() not in imported
    ]
    return FileResult(sorted(violations), len(sources))


def summary(result: FileResult) -> str:
    """One-line summary printed after the violations."""
    if result.violations:
        return f"{len(result.violations)}/{result.checked} files never imported"
    return f"{result.checked} files imported or exempt"


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py unused-files",
        description="Flag source files never imported by any analyzed source file.",
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument(
        "paths", nargs="*", help="explicit files or directories (default: normal selection)"
    )
    return parser
