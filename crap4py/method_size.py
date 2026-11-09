"""``method-size`` subcommand: flags oversized methods and signatures.

Port of the crap4dart ``method_size`` gate: a method spanning more than
``--max-lines`` (default 60) lines (``end_lineno - lineno + 1``) or
taking more than ``--max-params`` (default 6) parameters is a violation.
``__init__`` constructors are checked for parameter count only (the
upstream constructor rule). Host conventions: Python receivers are
listed parameters, so ``self``/``cls`` count toward ``max_params``
(Dart's receiver is implicit), and nested defs are checked like
methods, named per the complexity gate's convention (``m.inner``).
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .analyzer import _relative_to_root
from .args import UsageErrorParser
from .complexity import _FUNCTION_TYPES
from .constants import NO_FILES_MESSAGE
from .files import PathLike, expand_paths, find_source_files, is_test_file, parse_file
from .ignore_filter import filter_ignored

MAX_LINES = 60
MAX_PARAMS = 6


@dataclass(frozen=True, slots=True)
class SizeViolation:
    """A single over-large method finding."""

    file: str
    line: int
    name: str
    reason: str


@dataclass(frozen=True, slots=True)
class SizeResult:
    """Violations found plus how many methods were checked."""

    violations: list[SizeViolation]
    checked: int


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py method-size [paths...]``. Exit 2 iff violations."""
    args = _build_parser().parse_args(argv)
    files = (
        expand_paths(args.paths, project_root) if args.paths else find_source_files(project_root)
    )
    if not files:
        print(NO_FILES_MESSAGE)
        return 0
    result = check_files(files, project_root, args.max_lines, args.max_params)
    result = filter_ignored("method_size", result, project_root)
    for violation in result.violations:
        print(f"{violation.file}:{violation.line}: {violation.name} {violation.reason}")
    print(summary(result))
    return 2 if result.violations else 0


def check_files(
    files: Iterable[PathLike],
    project_root: PathLike,
    max_lines: int = MAX_LINES,
    max_params: int = MAX_PARAMS,
) -> SizeResult:
    """Check every method of every non-test file against the size limits."""
    root = Path(project_root)
    violations: list[SizeViolation] = []
    checked = 0
    for file_path in files:
        p = Path(file_path)
        if is_test_file(p, root):
            continue
        tree = parse_file(p)
        if tree is None:
            continue
        rel = _relative_to_root(p, root)
        for name, node in _methods(tree):
            checked += 1
            violations.extend(
                SizeViolation(rel, node.lineno, name, reason)
                for reason in _reasons(node, max_lines, max_params)
            )
    return SizeResult(violations, checked)


def summary(result: SizeResult) -> str:
    """One-line summary printed after the violations."""
    if result.violations:
        return f"{len(result.violations)}/{result.checked} methods over their size limits"
    return f"{result.checked} methods within their size limits"


def _reasons(
    node: ast.FunctionDef | ast.AsyncFunctionDef, max_lines: int, max_params: int
) -> Iterator[str]:
    """Violation texts for ``node``, or nothing when it is within limits."""
    if node.name != "__init__":  # constructors are checked for params only
        lines = node.end_lineno - node.lineno + 1
        if lines > max_lines:
            yield f"has {lines} lines > max {max_lines}"
    params = _param_count(node)
    if params > max_params:
        yield f"has {params} params > max {max_params}"


def _param_count(func: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Every listed parameter: positional, keyword-only, ``*args``, ``**kwargs``."""
    args = func.args
    return (
        len(args.posonlyargs)
        + len(args.args)
        + len(args.kwonlyargs)
        + bool(args.vararg)
        + bool(args.kwarg)
    )


def _methods(tree: ast.Module) -> Iterator[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Yield ``(qualified_name, node)`` for every function in ``tree``."""
    yield from _collect(tree, None)


def _collect(
    node: ast.AST, prefix: str | None
) -> Iterator[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            yield from _collect(child, _qualified(prefix, child.name))
        elif isinstance(child, _FUNCTION_TYPES):
            yield _qualified(prefix, child.name), child
            yield from _collect(child, child.name)


def _qualified(prefix: str | None, name: str) -> str:
    return f"{prefix}.{name}" if prefix else name


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py method-size",
        description="Flag methods over the line or parameter-count limit.",
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument(
        "--max-lines",
        type=int,
        default=MAX_LINES,
        help=f"maximum allowed lines per method (default: {MAX_LINES})",
    )
    parser.add_argument(
        "--max-params",
        type=int,
        default=MAX_PARAMS,
        help=f"maximum allowed parameters per method (default: {MAX_PARAMS})",
    )
    parser.add_argument(
        "paths", nargs="*", help="explicit files or directories (default: normal selection)"
    )
    return parser
