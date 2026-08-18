"""``test-assertions`` subcommand: flags test bodies without assertions.

Port of the crap4dart ``test_assertions`` gate: ``test_*`` functions and
methods (unittest classes and pytest-style functions alike — Python names
them identically) whose bodies contain fewer than ``--min`` (default 1)
assertion signals — bare ``assert`` statements, ``self.assert*`` /
``self.fail`` calls (``assertEqual``, ``assertRaises``, ...), or ``raises``
calls (``pytest.raises`` / ``from pytest import raises``). A test without
assertions compiles, runs green and verifies nothing — a typical
placeholder.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .analyzer import _relative_to_root
from .args import UsageErrorParser
from .complexity import _FUNCTION_TYPES, _qualified
from .files import PathLike, find_test_files, is_test_file, parse_file

MIN_ASSERTIONS = 1
_BARE_NAMES = frozenset({"raises", "fail"})


@dataclass(frozen=True, slots=True)
class AssertionViolation:
    """A single assertion-free test finding."""

    file: str
    line: int
    message: str


@dataclass(frozen=True, slots=True)
class AssertionResult:
    """Violations found plus how many test bodies were checked."""

    violations: list[AssertionViolation]
    checked: int


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py test-assertions [paths...]``. Exit 2 iff violations."""
    args = _build_parser().parse_args(argv)
    files = _select_files(args.paths, project_root)
    result = check_files(files, project_root, args.min)
    for violation in result.violations:
        print(f"{violation.file}:{violation.line}: {violation.message}")
    print(summary(result))
    return 2 if result.violations else 0


def check_files(
    files: Iterable[PathLike], project_root: PathLike, min_assertions: int = MIN_ASSERTIONS
) -> AssertionResult:
    """Flag ``test_*`` bodies in test files with fewer than ``min_assertions`` signals."""
    root = Path(project_root)
    violations: list[AssertionViolation] = []
    checked = 0
    for path in files:
        tree = parse_file(path)
        if tree is None:
            continue
        rel = _relative_to_root(Path(path), root)
        for label, line, assertions in test_candidates(tree):
            checked += 1
            if assertions < min_assertions:
                violations.append(
                    AssertionViolation(
                        rel,
                        line,
                        f"'{label}' has {assertions} assertion(s) — "
                        "a test without assertions verifies nothing",
                    )
                )
    return AssertionResult(violations, checked)


def test_candidates(tree: ast.Module) -> list[tuple[str, int, int]]:
    """``(label, line, assertion count)`` of every ``test_*`` function/method."""
    return _collect_tests(tree, prefix=None)


def _collect_tests(node: ast.AST, prefix: str | None) -> list[tuple[str, int, int]]:
    """Descend classes (label ``Class.test_x``); collect ``test_*`` bodies."""
    tests: list[tuple[str, int, int]] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            tests += _collect_tests(child, _qualified(prefix, child.name))
        elif isinstance(child, _FUNCTION_TYPES) and child.name.startswith("test_"):
            label = _qualified(prefix, child.name)
            tests.append((label, child.lineno, count_assertions(child.body)))
    return tests


def count_assertions(body: list[ast.stmt]) -> int:
    """Assertion signals in a test body: ``assert`` statements plus
    ``self.assert*``/``self.fail``/``*.raises`` calls and bare ``raises``/``fail``."""
    count = 0
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Assert):
                count += 1
            elif isinstance(node, ast.Call) and _is_assertion_call(node.func):
                count += 1
    return count


def _is_assertion_call(func: ast.expr) -> bool:
    if isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name) and func.value.id == "self":
            return func.attr.startswith("assert") or func.attr == "fail"
        return func.attr == "raises"
    return isinstance(func, ast.Name) and func.id in _BARE_NAMES


def summary(result: AssertionResult) -> str:
    """One-line summary printed after the violations."""
    if result.violations:
        return f"{len(result.violations)}/{result.checked} tests without assertions"
    return f"{result.checked} tests assert their expectations"


def _select_files(paths: list[str], project_root: Path) -> list[Path]:
    """Test files to scan: the default selection, or test files among explicit args."""
    if not paths:
        return find_test_files(project_root)
    root = project_root.resolve()
    selected: set[Path] = set()
    for arg in paths:
        p = Path(arg)
        p = p if p.is_absolute() else root / arg
        p = p.resolve()
        if p.is_dir():
            selected.update(q for q in p.rglob("*.py") if is_test_file(q, root))
        elif p.is_file() and is_test_file(p, root):
            selected.add(p)
    return sorted(selected)


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py test-assertions",
        description="Flag test bodies without assertion calls.",
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument(
        "paths", nargs="*", help="explicit test files or directories (default: all test files)"
    )
    parser.add_argument(
        "--min",
        type=int,
        default=MIN_ASSERTIONS,
        help=f"minimum assertion signals per test body (default: {MIN_ASSERTIONS})",
    )
    return parser
