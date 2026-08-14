"""``class-size`` subcommand: flags oversized classes.

Catches god-classes assembled from many small methods that individually pass
the complexity check — port of the crap4dart ``class_size`` gate (max 25
concrete methods, max weighted-methods sum 80).
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .analyzer import _relative_to_root
from .args import UsageErrorParser
from .complexity import _complexity_of_function
from .files import PathLike, expand_paths, find_source_files, is_test_file, parse_file

MAX_METHODS = 25
MAX_WMC = 80

_FUNCTION_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True, slots=True)
class ClassViolation:
    """A single oversized-class finding."""

    file: str
    totals: ClassTotals


@dataclass(frozen=True, slots=True)
class ClassTotals:
    """Method count and weighted-methods sum of one class."""

    name: str
    line: int
    methods: int
    wmc: int


@dataclass(frozen=True, slots=True)
class ClassSizeResult:
    """Violations (oversized classes) plus how many classes were checked."""

    violations: list[ClassViolation]
    checked: int


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py class-size [paths...]``. Exit 2 iff violations."""
    args = _build_parser().parse_args(argv)
    files = (
        expand_paths(args.paths, project_root) if args.paths else find_source_files(project_root)
    )
    if not files:
        print("No Python files to check.")
        return 0
    result = check_files(files, project_root)
    for violation in result.violations:
        message = violation_message(violation.totals)
        print(f"{violation.file}:{violation.totals.line}: {message}")
    print(summary(result))
    return 2 if result.violations else 0


def check_files(files: Iterable[PathLike], project_root: PathLike) -> ClassSizeResult:
    """Measure every class of every non-test file against the size limits."""
    root = Path(project_root)
    violations: list[ClassViolation] = []
    checked = 0
    for file_path in files:
        p = Path(file_path)
        if is_test_file(p, root):
            continue
        tree = parse_file(p)
        if tree is None:
            continue
        rel = _relative_to_root(p, root)
        for totals in class_totals(tree):
            checked += 1
            if violation_message(totals) is not None:
                violations.append(ClassViolation(rel, totals))
    return ClassSizeResult(violations, checked)


def class_totals(tree: ast.Module) -> Iterator[ClassTotals]:
    """Yield concrete-method count and weighted-methods sum per class in ``tree``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            methods = [stmt for stmt in node.body if isinstance(stmt, _FUNCTION_TYPES)]
            wmc = sum(_complexity_of_function(method) for method in methods)
            yield ClassTotals(node.name, node.lineno, len(methods), wmc)


def violation_message(totals: ClassTotals) -> str | None:
    """Size violation text for ``totals``, or None when within limits."""
    reasons = []
    if totals.methods > MAX_METHODS:
        reasons.append(f"{totals.methods} methods (max {MAX_METHODS})")
    if totals.wmc > MAX_WMC:
        reasons.append(f"weighted methods {totals.wmc} (max {MAX_WMC})")
    return f"class {totals.name}: " + ", ".join(reasons) if reasons else None


def summary(result: ClassSizeResult) -> str:
    """One-line summary printed after the violations."""
    if result.violations:
        return (
            f"{len(result.violations)}/{result.checked} classes over "
            f"{MAX_METHODS} methods/WMC {MAX_WMC}"
        )
    return f"{result.checked} classes within {MAX_METHODS} methods/WMC {MAX_WMC}"


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py class-size",
        description="Flag classes over 25 methods or a weighted-methods sum over 80.",
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument(
        "paths", nargs="*", help="explicit files or directories (default: normal selection)"
    )
    return parser
