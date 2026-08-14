"""``nesting`` subcommand: flags functions with deeply nested control flow.

Deep nesting is the classic artifact of dodging the complexity gate with
layered conditionals — port of the crap4dart ``nesting`` gate (limit 5).
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .analyzer import _relative_to_root
from .args import UsageErrorParser
from .files import PathLike, expand_paths, find_source_files, is_test_file, parse_file

MAX_NESTING = 5

_FUNCTION_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)
_CONTROL_TYPES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    ast.TryStar,
)


@dataclass(frozen=True, slots=True)
class NestingViolation:
    """A single over-nested function finding."""

    file: str
    line: int
    function: str
    depth: int


@dataclass(frozen=True, slots=True)
class NestingResult:
    """Violations found plus how many functions were checked."""

    violations: list[NestingViolation]
    checked: int


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py nesting [paths...]``. Exit 2 iff violations."""
    args = _build_parser().parse_args(argv)
    files = (
        expand_paths(args.paths, project_root) if args.paths else find_source_files(project_root)
    )
    if not files:
        print("No Python files to check.")
        return 0
    result = check_files(files, project_root)
    for violation in result.violations:
        print(
            f"{violation.file}:{violation.line}: {violation.function} nesting depth "
            f"{violation.depth} > {MAX_NESTING}"
        )
    print(summary(result))
    return 2 if result.violations else 0


def check_files(files: Iterable[PathLike], project_root: PathLike) -> NestingResult:
    """Check every function of every non-test file against the nesting limit."""
    root = Path(project_root)
    violations: list[NestingViolation] = []
    checked = 0
    for file_path in files:
        p = Path(file_path)
        if is_test_file(p, root):
            continue
        tree = parse_file(p)
        if tree is None:
            continue
        rel = _relative_to_root(p, root)
        for name, line, depth in function_depths(tree):
            checked += 1
            if depth > MAX_NESTING:
                violations.append(NestingViolation(rel, line, name, depth))
    return NestingResult(violations, checked)


def max_nesting(func: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Deepest control-flow nesting in ``func``; the body block is level 1."""
    return _block_depth(func.body, 1)


def function_depths(tree: ast.Module) -> Iterator[tuple[str, int, int]]:
    """Yield ``(qualified_name, lineno, max_nesting)`` for every function in ``tree``."""
    yield from _collect_functions(tree, None)


def summary(result: NestingResult) -> str:
    """One-line summary printed after the violations."""
    if result.violations:
        return (
            f"{len(result.violations)}/{result.checked} functions exceed nesting depth"
            f" {MAX_NESTING}"
        )
    return f"{result.checked} functions within nesting depth {MAX_NESTING}"


def _collect_functions(node: ast.AST, prefix: str | None) -> Iterator[tuple[str, int, int]]:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            yield from _collect_functions(child, _qualified(prefix, child.name))
        elif isinstance(child, _FUNCTION_TYPES):
            yield _qualified(prefix, child.name), child.lineno, max_nesting(child)
            yield from _collect_functions(child, child.name)


def _qualified(prefix: str | None, name: str) -> str:
    return f"{prefix}.{name}" if prefix else name


def _block_depth(stmts: list[ast.stmt], level: int) -> int:
    best = level
    for stmt in stmts:
        best = max(best, _statement_depth(stmt, level))
    return best


def _statement_depth(stmt: ast.stmt, level: int) -> int:
    """Deepest level reached inside ``stmt``; control-flow blocks nest at ``level + 1``."""
    if isinstance(stmt, ast.Match):
        return max((_block_depth(case.body, level + 1) for case in stmt.cases), default=level)
    if not isinstance(stmt, _CONTROL_TYPES):
        return level
    blocks = _control_blocks(stmt)
    return max((_block_depth(body, level + offset) for body, offset in blocks), default=level)


def _control_blocks(stmt: ast.stmt) -> list[tuple[list[ast.stmt], int]]:
    """Child statement blocks of a control statement with their level offsets."""
    blocks = [(stmt.body, 1)]
    blocks += [(block, 1) for name in ("orelse", "finalbody") if (block := getattr(stmt, name, []))]
    blocks += [(handler.body, 2) for handler in getattr(stmt, "handlers", [])]
    return blocks


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py nesting",
        description="Flag functions whose control-flow nesting exceeds 5 levels.",
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument(
        "paths", nargs="*", help="explicit files or directories (default: normal selection)"
    )
    return parser
