"""``public-docs`` subcommand: flags public declarations without docstrings.

Port of the crap4dart ``public_docs`` gate: public modules, classes,
functions and methods need a docstring (``ast.get_docstring``). A name
starting with ``_`` is private; members of private classes are exempt,
as are files matching ``--exclude`` (fnmatch on the project-relative
path, repeatable). Upstream exempts ``@override`` members; this port
does not — PEP 698 ``typing.override`` is not honored (stricter only).
"""

from __future__ import annotations

import ast
import fnmatch
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .analyzer import _relative_to_root
from .args import UsageErrorParser
from .complexity import _FUNCTION_TYPES
from .constants import NO_FILES_MESSAGE
from .files import PathLike, expand_paths, find_source_files, is_test_file, parse_file
from .ignore_filter import filter_ignored


@dataclass(frozen=True, slots=True)
class DocsViolation:
    """A single undocumented public declaration finding."""

    file: str
    line: int
    message: str


@dataclass(frozen=True, slots=True)
class DocsResult:
    """Violations found plus how many public declarations were checked."""

    violations: list[DocsViolation]
    checked: int


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py public-docs [paths...]``. Exit 2 iff violations."""
    args = _build_parser().parse_args(argv)
    files = (
        expand_paths(args.paths, project_root) if args.paths else find_source_files(project_root)
    )
    if not files:
        print(NO_FILES_MESSAGE)
        return 0
    result = check_files(files, project_root, args.exclude)
    result = filter_ignored("public_docs", result, project_root)
    for violation in result.violations:
        print(f"{violation.file}:{violation.line}: {violation.message}")
    print(summary(result))
    return 2 if result.violations else 0


def check_files(
    files: Iterable[PathLike],
    project_root: PathLike,
    excludes: Iterable[str] = (),
) -> DocsResult:
    """Check every public declaration of every non-test file for a docstring."""
    root = Path(project_root)
    violations: list[DocsViolation] = []
    checked = 0
    for file_path in files:
        p = Path(file_path)
        if is_test_file(p, root):
            continue
        tree = parse_file(p)
        if tree is None:
            continue
        rel = _relative_to_root(p, root)
        if any(fnmatch.fnmatch(rel, pattern) for pattern in excludes):
            continue
        for kind, name, node in _public_declarations(tree, Path(rel).stem):
            checked += 1
            if ast.get_docstring(node) is None:
                line = getattr(node, "lineno", 1)
                violations.append(DocsViolation(rel, line, f'missing docstring on {kind} "{name}"'))
    return DocsResult(violations, checked)


def summary(result: DocsResult) -> str:
    """One-line summary printed after the violations."""
    if result.violations:
        return f"{len(result.violations)}/{result.checked} public declarations missing docstrings"
    return f"{result.checked} public declarations documented"


def _public_declarations(tree: ast.Module, module_name: str) -> Iterator[tuple[str, str, ast.AST]]:
    """``(kind, name, node)`` for every public declaration in ``tree``."""
    yield "module", module_name, tree
    yield from _scan_body(tree.body, in_class=False)


def _scan_body(body: list[ast.stmt], in_class: bool) -> Iterator[tuple[str, str, ast.AST]]:
    """Public functions/methods and classes in ``body``; private classes
    are not public API, so their members are skipped entirely."""
    for stmt in body:
        if isinstance(stmt, _FUNCTION_TYPES):
            if _is_public(stmt.name):
                yield ("method" if in_class else "function"), stmt.name, stmt
        elif isinstance(stmt, ast.ClassDef) and _is_public(stmt.name):
            yield "class", stmt.name, stmt
            yield from _scan_body(stmt.body, in_class=True)


def _is_public(name: str) -> bool:
    return not name.startswith("_")


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py public-docs",
        description="Flag public declarations without a docstring.",
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
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
