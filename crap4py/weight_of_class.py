"""``weight-of-class`` subcommand: flags data-revealing classes.

Fails classes whose share of public data among public instance members
exceeds 0.33 — port of the crap4dart ``weight_of_class`` gate, adapted to
Python's lack of field declarations: public fields are the distinct public
``self.<attr>`` assignment targets found in the class's methods.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from .analyzer import _relative_to_root
from .args import UsageErrorParser
from .files import PathLike, expand_paths, find_source_files, is_test_file, parse_file

MAX_WEIGHT = 0.33

_FUNCTION_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True, slots=True)
class WeightViolation:
    """A single over-weight class finding."""

    file: str
    line: int
    name: str
    fields: int
    members: int
    ratio: float


@dataclass(frozen=True, slots=True)
class WeightResult:
    """Violations found plus how many classes were checked."""

    violations: list[WeightViolation]
    checked: int


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py weight-of-class [paths...]``. Exit 2 iff violations."""
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
            f"{violation.file}:{violation.line}: class {violation.name} data weight "
            f"{violation.ratio:.2f} ({violation.fields} public fields of "
            f"{violation.members} public members) > {MAX_WEIGHT}"
        )
    print(summary(result))
    return 2 if result.violations else 0


def check_files(files: Iterable[PathLike], project_root: PathLike) -> WeightResult:
    """Check every public class of every non-test file against the weight limit."""
    root = Path(project_root)
    violations: list[WeightViolation] = []
    checked = 0
    for file_path in files:
        p = Path(file_path)
        if is_test_file(p, root):
            continue
        tree = parse_file(p)
        if tree is None:
            continue
        file_violations, count = _class_violations(tree, _relative_to_root(p, root))
        violations += file_violations
        checked += count
    return WeightResult(violations, checked)


def weight_violation(node: ast.ClassDef) -> WeightViolation | None:
    """Weight violation for one class, or None (private or within the limit)."""
    if node.name.startswith("_"):
        return None
    fields = public_self_fields(node)
    members = len(fields) + len(public_instance_methods(node))
    ratio = len(fields) / members if members else 0.0
    if not fields or ratio <= MAX_WEIGHT:
        return None
    return WeightViolation("", node.lineno, node.name, len(fields), members, ratio)


def public_self_fields(node: ast.ClassDef) -> set[str]:
    """Distinct public ``self.<attr>`` assignment targets across all instance
    methods — fields usually get assigned in ``__init__``, which is private-named."""
    fields: set[str] = set()
    for method in _instance_methods(node):
        fields |= _assigned_self_attrs(method)
    return fields


def public_instance_methods(node: ast.ClassDef) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Public, non-static methods declared directly in the class body."""
    return [stmt for stmt in _instance_methods(node) if not stmt.name.startswith("_")]


def _instance_methods(node: ast.ClassDef) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Non-static methods declared directly in the class body."""
    return [stmt for stmt in node.body if _is_instance_method(stmt)]


def _is_instance_method(stmt: ast.stmt) -> bool:
    if not isinstance(stmt, _FUNCTION_TYPES):
        return False
    return not any(
        isinstance(decorator, ast.Name) and decorator.id in ("staticmethod", "classmethod")
        for decorator in stmt.decorator_list
    )


def _assigned_self_attrs(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Public attribute names assigned via ``self.<attr> = ...`` in ``func``."""
    return {
        node.attr
        for node in ast.walk(func)
        if isinstance(node, ast.Attribute)
        and isinstance(node.ctx, ast.Store)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        and not node.attr.startswith("_")
    }


def _class_violations(tree: ast.Module, rel: str) -> tuple[list[WeightViolation], int]:
    """Violations and class count of one module, with the file path filled in."""
    violations = []
    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        checked += 1
        violation = weight_violation(node)
        if violation is not None:
            violations.append(replace(violation, file=rel))
    return violations, checked


def summary(result: WeightResult) -> str:
    """One-line summary printed after the violations."""
    if result.violations:
        return f"{len(result.violations)}/{result.checked} classes reveal more data than behavior"
    return f"{result.checked} classes within data weight {MAX_WEIGHT}"


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py weight-of-class",
        description="Flag classes whose public data share exceeds 0.33.",
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument(
        "paths", nargs="*", help="explicit files or directories (default: normal selection)"
    )
    return parser
