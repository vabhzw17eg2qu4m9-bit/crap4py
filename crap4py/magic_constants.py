"""``magic-constants`` subcommand: flags magic literals.

Port of the crap4dart ``magic_constants`` gate: (a) hex color literals
(``0xRRGGBB`` / ``0xAARRGGBB``) used outside named-constant declarations —
Python's const convention is an ALL_CAPS assignment, whose value lines are
exempt; (b) numeric or string literals whose value repeats at least 3 times
in one file — every occurrence is reported. String values shorter than 4
characters are ignored.

Identifier-position strings — dict keys (``{"theme": ...}``), index
expressions (``obj["key"]``) and match-case literal patterns — are protocol
identifiers, not magic constants, and are skipped (crap4dart 0.7.2/0.8.3
precision fixes). Lines inside ALL_CAPS initializers are exempt from both
checks (0.8.4).
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .analyzer import _relative_to_root
from .args import UsageErrorParser
from .files import PathLike, expand_paths, find_source_files, is_test_file

MIN_DUPLICATES = 3
MIN_LENGTH = 4

_HEX_COLOR = re.compile(r"0[xX][0-9a-fA-F]{6,8}")
_CONST_NAME = re.compile(r"_?[A-Z][A-Z0-9_]*")


@dataclass(frozen=True, slots=True)
class MagicConstant:
    """A single magic-literal finding."""

    file: str
    line: int
    message: str


@dataclass(frozen=True, slots=True)
class MagicConstantsResult:
    """Violations found plus how many files were checked."""

    violations: list[MagicConstant]
    checked: int


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py magic-constants [paths...]``. Exit 2 iff violations."""
    args = _build_parser().parse_args(argv)
    files = (
        expand_paths(args.paths, project_root) if args.paths else find_source_files(project_root)
    )
    if not files:
        print("No Python files to check.")
        return 0
    result = check_files(files, project_root)
    for violation in result.violations:
        print(f"{violation.file}:{violation.line}: {violation.message}")
    print(summary(result))
    return 2 if result.violations else 0


def check_files(files: Iterable[PathLike], project_root: PathLike) -> MagicConstantsResult:
    """Scan every non-test file for hex colors outside constants and repeated literals."""
    violations: list[MagicConstant] = []
    checked = 0
    for path in files:
        if is_test_file(path, project_root):
            continue
        try:
            source = Path(path).read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            print(f"Warning: could not parse {path}: {exc}", file=sys.stderr)
            continue
        checked += 1
        relative = _relative_to_root(Path(path), Path(project_root))
        violations.extend(file_violations(tree, source, relative))
    return MagicConstantsResult(violations, checked)


def file_violations(tree: ast.Module, source: str, relative: str) -> list[MagicConstant]:
    """All magic-constant findings of one parsed file."""
    exempt = constant_lines(tree)
    hexes, counts = _literals(tree, source)
    violations = [
        MagicConstant(relative, line, "hex color outside a constant declaration")
        for line in hexes
        if line not in exempt
    ]
    for key, lines in sorted(counts.items()):
        occurrences = [line for line in lines if line not in exempt]
        if len(occurrences) < MIN_DUPLICATES:
            continue
        violations.extend(
            MagicConstant(
                relative,
                line,
                f"literal {key} repeats {len(occurrences)} times — extract a named constant",
            )
            for line in occurrences
        )
    return violations


def constant_lines(tree: ast.Module) -> frozenset[int]:
    """Lines spanned by values of module- or class-level ALL_CAPS assignments.

    The span covers the FULL initializer subtree — nested calls, containers
    and expressions inside it are part of the named constant (crap4dart
    0.8.4/8071206).
    """
    bodies = [tree.body]
    bodies += [node.body for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    lines: set[int] = set()
    for body in bodies:
        for stmt in body:
            if _is_const_statement(stmt):
                lines.update(range(stmt.value.lineno, stmt.value.end_lineno + 1))
    return frozenset(lines)


def summary(result: MagicConstantsResult) -> str:
    """One-line summary printed after the violations."""
    if result.violations:
        return f"{len(result.violations)} magic constant(s) in {result.checked} files"
    return f"{result.checked} files free of magic constants"


def _is_const_statement(stmt: ast.stmt) -> bool:
    """Whether ``stmt`` assigns to ALL_CAPS name(s) only (Python const convention)."""
    if not isinstance(stmt, (ast.Assign, ast.AnnAssign)) or stmt.value is None:
        return False
    targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
    names = [t for t in targets if isinstance(t, ast.Name)]
    return bool(names) and all(_CONST_NAME.fullmatch(name.id) for name in names)


def _literals(tree: ast.Module, source: str) -> tuple[list[int], dict[str, list[int]]]:
    """Hex-color lines and per-value occurrence lines of every eligible literal.

    Adjacent string literals arrive merged as one ``Constant`` (upstream's
    adjacent-strings handling). f-strings (``JoinedStr``) are skipped —
    interpolation makes their value dynamic, not a constant. Strings in
    identifier positions (dict keys, index expressions, match-case patterns)
    are skipped — they name protocol fields, not constants.
    """
    hexes: list[int] = []
    counts: dict[str, list[int]] = {}
    identifier_keys = _identifier_key_ids(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and id(node) not in identifier_keys:
            _record(node, source, hexes, counts)
    return hexes, counts


def _identifier_key_ids(tree: ast.Module) -> set[int]:
    """``id()``s of str literals used as dict keys, indexes, or match patterns."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        ids |= _node_key_ids(node)
    return ids


def _node_key_ids(node: ast.AST) -> set[int]:
    """Identifier-position str literals contributed by one AST node."""
    if isinstance(node, ast.Dict):
        return {id(k) for k in node.keys if _is_str_constant(k)}
    if isinstance(node, ast.Subscript):
        return {id(node.slice)} if _is_str_constant(node.slice) else set()
    if isinstance(node, ast.MatchValue):
        return {id(node.value)} if _is_str_constant(node.value) else set()
    return set()


def _is_str_constant(node: ast.AST | None) -> bool:
    """Whether ``node`` is a plain ``str`` literal (the skipped kind upstream)."""
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _record(node: ast.Constant, source: str, hexes: list[int], counts: dict) -> None:
    """Record one literal occurrence as a hex color and/or a repeat candidate."""
    if isinstance(node.value, (bool, type(None))):
        return
    segment = ast.get_source_segment(source, node) or ""
    if isinstance(node.value, int) and _HEX_COLOR.fullmatch(segment):
        hexes.append(node.lineno)
    key = node.value if isinstance(node.value, str) else segment
    if len(key) >= MIN_LENGTH:
        counts.setdefault(key, []).append(node.lineno)


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py magic-constants",
        description="Flag hex colors outside constant declarations and literals repeated 3+ times.",
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument(
        "paths", nargs="*", help="explicit files or directories (default: normal selection)"
    )
    return parser
