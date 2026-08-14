"""``unused-code`` subcommand: flags dead private module-level declarations.

Port of the crap4dart ``unused_code`` gate, scoped per module: a private
top-level name (``_function``, ``_class``, ``_x = ...``) whose identifier
never appears anywhere else in the module is flagged. Dunder names
(``__version__``) are conventionally public and never flagged.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .analyzer import _relative_to_root
from .args import UsageErrorParser
from .files import PathLike, find_source_files, is_test_file, parse_file

_DECL_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


@dataclass(frozen=True, slots=True)
class CodeViolation:
    """A single unused private declaration finding."""

    file: str
    line: int
    name: str


@dataclass(frozen=True, slots=True)
class CodeResult:
    """Violations found plus how many files were checked."""

    violations: list[CodeViolation]
    checked: int


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py unused-code [paths...]``. Exit 2 iff violations.

    A partial selection cannot know whether a name is used elsewhere, so
    explicit paths make the check skip (crap4dart 0.5.1 behavior).
    """
    args = _build_parser().parse_args(argv)
    if args.paths:
        print("unused-code: not meaningful for a partial selection")
        return 0
    result = check_files(find_source_files(project_root), project_root)
    for violation in result.violations:
        print(f"{violation.file}:{violation.line}: '{violation.name}' is never referenced")
    print(summary(result))
    return 2 if result.violations else 0


def check_files(files: Iterable[PathLike], project_root: PathLike) -> CodeResult:
    """Check every non-test file for module-level dead private declarations."""
    root = Path(project_root)
    violations: list[CodeViolation] = []
    checked = 0
    for file_path in files:
        p = Path(file_path)
        if is_test_file(p, root):
            continue
        tree = parse_file(p)
        if tree is None:
            continue
        checked += 1
        rel = _relative_to_root(p, root)
        violations += [
            CodeViolation(rel, line, name) for name, line in sorted(unused_names(tree).items())
        ]
    return CodeResult(violations, checked)


def unused_names(tree: ast.Module) -> dict[str, int]:
    """Private module-level names never referenced by any identifier in ``tree``."""
    declared = {name: stmt.lineno for stmt in tree.body if (name := _declared_name(stmt))}
    used = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and id(node) not in _store_target_ids(tree)
    }
    return {name: line for name, line in declared.items() if name not in used}


def _declared_name(stmt: ast.stmt) -> str | None:
    """Private module-level name declared by ``stmt``, or None."""
    name = stmt.name if isinstance(stmt, _DECL_TYPES) else _assigned_name(stmt)
    return name if name and _is_private(name) else None


def _assigned_name(stmt: ast.stmt) -> str | None:
    """Single ``Name`` target of a module-level assignment, or None."""
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
        target = stmt.targets[0]
    elif isinstance(stmt, ast.AnnAssign):
        target = stmt.target
    else:
        return None
    return target.id if isinstance(target, ast.Name) else None


def _store_target_ids(tree: ast.Module) -> set[int]:
    """``id()``s of module-level private assignment targets (not references)."""
    ids = set()
    for stmt in tree.body:
        targets = stmt.targets if isinstance(stmt, ast.Assign) else _ann_target(stmt)
        ids |= {id(t) for t in targets if isinstance(t, ast.Name) and _is_private(t.id)}
    return ids


def _ann_target(stmt: ast.stmt) -> list[ast.expr]:
    return [stmt.target] if isinstance(stmt, ast.AnnAssign) else []


def _is_private(name: str) -> bool:
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def summary(result: CodeResult) -> str:
    """One-line summary printed after the violations."""
    if result.violations:
        return f"{len(result.violations)} unused private declarations in {result.checked} files"
    return f"no unused private declarations in {result.checked} files"


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py unused-code",
        description="Flag module-level private declarations never referenced.",
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument(
        "paths", nargs="*", help="explicit files or directories (default: normal selection)"
    )
    return parser
