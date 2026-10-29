"""``reorder`` subcommand: deterministic canonical member ordering in class bodies.

Port of the crap4dart ``reorder`` command (24a6f24f: ``member_order.dart`` +
``reorder_command.dart``), adapted to Python. Every class body is rewritten so
its members follow the canonical order — a stable partition: same-kind members
keep their source-relative order, never an alphabetical or heuristic sort.

Canonical category order (host-idiomatic table, porting upstream's
constants/static fields/fields/constructors/static methods/accessors/public
methods/private methods):

0. class constants — ``UPPER_SNAKE_CASE`` assignments (annotated or not)
1. class attributes — every other assignment/annotation in the body
2. constructors — ``__init__``, ``__new__``
3. other dunders — ``__repr__``, ``__eq__``, ``__len__``, ...
4. static/class methods — ``@staticmethod`` / ``@classmethod``
5. properties — ``@property`` / ``@x.setter`` / ``@x.deleter``
6. public methods
7. private methods — ``_name``

A leading docstring stays first. ``#`` comment lines directly above a member
and its decorators move with it; a comment block separated from its member by
ONE blank line still attaches, TWO blank lines detach — a detached block rides
with the preceding member, so comments are never dropped (upstream drops them;
here every slice is whole lines, and the between-member gap belongs to the
preceding member).

Enums (``enum.Enum`` family): member assignments are never touched — value
order is semantics — so the reorderable span starts after the LAST member
assignment (an interleaved member freezes everything up to and including
itself); the section after it reorders like any class body, and enums
without a member section come back byte-identical. Also never touched:
bodies containing anything but members (``if``/``try``/nested class/bare
expressions), members sharing a source line
(``class A: x = 1`` one-liners, semicolon lists).

Deterministic: same input, same output; already-canonical bodies are left
byte-identical (the span is rewritten only when the category sequence
changes); ``fix(fix(x)) == fix(x)``; the file's line-ending style (LF/CRLF)
is preserved.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from .analyzer import _relative_to_root
from .args import UsageErrorParser
from .constants import NO_FILES_MESSAGE
from .files import expand_paths, find_source_files

CONSTANTS, ATTRS, CONSTRUCTORS, DUNDERS = range(4)
STATIC_METHODS, PROPERTIES, PUBLIC, PRIVATE = range(4, 8)

_CONSTANT_NAME = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_CONSTRUCTOR_NAMES = frozenset({"__init__", "__new__"})
_ENUM_BASE_NAMES = frozenset({"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"})
_MEMBER_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Assign, ast.AnnAssign)


@dataclass(frozen=True, slots=True)
class _Slice:
    """One member's source span plus its stable-partition sort keys."""

    category: int
    index: int
    start: int
    end: int


def reorder_source(source: str) -> str:
    """Return ``source`` with class members in canonical order.

    Enum member assignments stay frozen (value order is semantics); the
    member section after the last one reorders like a class body.
    Pure function: the same input always produces the same output, and
    already-canonical input comes back byte-identical. Raises SyntaxError
    on unparseable input.
    """
    while True:
        replacements = _replacements(ast.parse(source), source)
        if not replacements:
            return source
        for start, end, text in sorted(replacements, reverse=True):
            source = source[:start] + text + source[end:]


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py reorder [paths...] [--dry-run]``.

    Rewrites files (default: the normal source selection); ``--dry-run``
    writes nothing and exits 2 when any file would change. Parse errors
    are skipped with a note, never fatal.
    """
    args = _build_parser().parse_args(argv)
    files = (
        expand_paths(args.paths, project_root) if args.paths else find_source_files(project_root)
    )
    if not files:
        print(NO_FILES_MESSAGE)
        return 0
    changed = sum(_reorder_file(path, project_root, args.dry_run) for path in files)
    if not changed:
        print(f"All {len(files)} files already canonical.")
        return 0
    print(
        f"{changed} of {len(files)} files would be reordered."
        if args.dry_run
        else f"{changed} of {len(files)} files reordered."
    )
    return 2 if args.dry_run else 0


def _reorder_file(path: Path, project_root: Path, dry_run: bool) -> bool:
    """Reorder one file unless ``dry_run``; True when it would/did change."""
    rel = _relative_to_root(path, project_root)
    # newline="" keeps CRLF files CRLF (read_text would translate them).
    with path.open(encoding="utf-8", newline="") as handle:
        original = handle.read()
    try:
        reordered = reorder_source(original)
    except SyntaxError:
        print(f"skipped (parse error): {rel}")
        return False
    if reordered == original:
        return False
    if not dry_run:
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(reordered)
    print(f"{'would reorder' if dry_run else 'reordered'} {rel}")
    return True


def _replacements(tree: ast.Module, source: str) -> list[tuple[int, int, str]]:
    """Rewrite spans for this pass: innermost classes win, then fixpoint.

    A nested class's span sits inside its parent's, so each pass rewrites
    only classes containing no other rewrite; the loop in reorder_source
    re-parses and handles the parents (reordering never breaks an
    already-fixed inner class, so this terminates).
    """
    offsets = _line_offsets(source)
    lines = source.splitlines()
    candidates = [
        found
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and (found := _class_replacement(node, source, offsets, lines)) is not None
    ]
    return [
        span
        for span in candidates
        if not any(_contains(other, span) for other in candidates if other is not span)
    ]


def _contains(outer: tuple[int, int, str], inner: tuple[int, int, str]) -> bool:
    return outer[0] <= inner[0] and inner[1] <= outer[1]


def _class_replacement(
    node: ast.ClassDef, source: str, offsets: list[int], lines: list[str]
) -> tuple[int, int, str] | None:
    """Whole-span replacement for one class body, or None to leave it alone."""
    slices = _member_slices(node, offsets, lines)
    if slices is None:
        return None
    ordered = sorted(slices, key=lambda s: (s.category, s.index))
    if all(s.index == i for i, s in enumerate(ordered)):
        return None  # already canonical: byte-identical whatever the layout
    texts = [source[s.start : s.end] for s in ordered]
    source_last = source[slices[-1].start : slices[-1].end]
    ending = source_last[len(source_last.rstrip("\r\n")) :]  # keep EOF newline style
    separator = "\r\n\r\n" if "\r\n" in source else "\n\n"
    body = separator.join(text.rstrip("\r\n") for text in texts) + ending
    return slices[0].start, slices[-1].end, body


def _member_slices(node: ast.ClassDef, offsets: list[int], lines: list[str]) -> list[_Slice] | None:
    """One slice per eligible member, or None when the class must not move."""
    layout = _reorderable_members(node)
    if layout is None:
        return None
    members, boundary = layout
    claims = _claim_lines(members, boundary, lines)
    return [
        _Slice(
            _category(member),
            i,
            offsets[claims[i]],
            offsets[claims[i + 1]] if i + 1 < len(members) else offsets[member.end_lineno],
        )
        for i, member in enumerate(members)
    ]


def _reorderable_members(node: ast.ClassDef) -> tuple[list[ast.stmt], int] | None:
    """Members eligible for the stable partition plus the comment-walk floor
    line (docstring end, else class header), or None when the class must not
    be touched: bodies containing anything but members have no safe partition.
    Enum-family bodies take their own path (see :func:`_enum_members`).
    """
    if _is_enum_class(node):
        return _enum_members(node)
    doc_end = node.body[0].end_lineno if _is_docstring(node.body[0]) else 0
    members = node.body[1:] if doc_end else node.body
    if not _pure_members(members):
        return None
    boundary = doc_end or node.lineno
    return (members, boundary) if _shares_no_lines(members, boundary) else None


def _pure_members(members: list[ast.stmt]) -> bool:
    """Whether the body (docstring stripped) is two or more member statements."""
    return len(members) >= 2 and all(isinstance(m, _MEMBER_TYPES) for m in members)


def _shares_no_lines(members: list[ast.stmt], boundary: int) -> bool:
    """False when the first member shares the header/docstring line or any two
    members share a line (``class A: x = 1`` one-liners, semicolon lists)."""
    points = [_first_point(m) for m in members]
    if points[0][0] <= boundary:
        return False
    return all(cur[0] > prev.end_lineno for cur, prev in zip(points[1:], members, strict=False))


def _claim_lines(members: list[ast.stmt], boundary: int, lines: list[str]) -> list[int]:
    """First slice line (0-based) per member: its first-token line pulled up
    over attached comment lines (floor: previous member's last line, or the
    class header/docstring line for the first member)."""
    return [
        _attach_start(
            lines,
            _first_point(member)[0] - 1,
            members[i - 1].end_lineno if i else boundary,
        )
        for i, member in enumerate(members)
    ]


def _category(stmt: ast.stmt) -> int:
    """Canonical category of one eligible member."""
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return _method_category(stmt)
    return _attribute_category(stmt)


def _method_category(stmt: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    name = stmt.name
    if name in _CONSTRUCTOR_NAMES:
        return CONSTRUCTORS
    if name.startswith("__") and name.endswith("__"):
        return DUNDERS
    return _decorated_category(stmt) if stmt.decorator_list else _visibility_category(name)


def _decorated_category(stmt: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Category of a decorated method: static/class beats property beats name."""
    kinds = {_decorator_kind(d) for d in stmt.decorator_list}
    if STATIC_METHODS in kinds:
        return STATIC_METHODS
    if PROPERTIES in kinds:
        return PROPERTIES
    return _visibility_category(stmt.name)


def _decorator_kind(decorator: ast.expr) -> int | None:
    if isinstance(decorator, ast.Name):
        if decorator.id in ("staticmethod", "classmethod"):
            return STATIC_METHODS
        if decorator.id == "property":
            return PROPERTIES
        return None
    if isinstance(decorator, ast.Attribute) and decorator.attr in ("setter", "deleter"):
        return PROPERTIES
    return None


def _visibility_category(name: str) -> int:
    return PRIVATE if name.startswith("_") else PUBLIC


def _attribute_category(stmt: ast.stmt) -> int:
    name = None
    if isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Name):
        name = stmt.targets[0].id
    elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
        name = stmt.target.id
    return CONSTANTS if name and _CONSTANT_NAME.fullmatch(name) else ATTRS


def _enum_members(node: ast.ClassDef) -> tuple[list[ast.stmt], int] | None:
    """The member section of an enum-family body: everything after the LAST
    member assignment, with that assignment's last line as the comment-walk
    floor. The member assignments themselves are frozen — enum value order
    is semantics — and Python's interleaving freedom makes "last" the safe
    span start (the host analog of upstream's constants-terminator rule):
    a member assignment after code freezes everything up to and including
    it. An enum without member assignments has no constants to freeze, so
    the whole body (docstring still leading) is an ordinary member section.

    Returns None when there is no member section to reorder or it has no
    safe partition.
    """
    members = node.body
    boundary = 0
    if members and _is_docstring(members[0]):
        boundary = members[0].end_lineno
        members = members[1:]
    assigned = [i for i, m in enumerate(members) if _is_member_assignment(m)]
    if assigned:
        last = members[assigned[-1]]
        members = members[assigned[-1] + 1 :]
        boundary = last.end_lineno
    if not _pure_members(members):
        return None
    return (members, boundary) if _shares_no_lines(members, boundary) else None


def _is_member_assignment(stmt: ast.stmt) -> bool:
    """Whether the statement could define an enum member: any assignment, or
    an annotation carrying a value (a value-less annotation is a plain,
    reorderable field). Dunder names like ``__slots__`` are conservatively
    frozen too — never a correctness risk, unlike missing a member."""
    return isinstance(stmt, ast.Assign) or (
        isinstance(stmt, ast.AnnAssign) and stmt.value is not None
    )


def _is_enum_class(node: ast.ClassDef) -> bool:
    """Whether the class inherits the enum family — its member assignments
    are frozen (value order is semantics)."""
    return any(_dotted_name(base).rpartition(".")[2] in _ENUM_BASE_NAMES for base in node.bases)


def _dotted_name(node: ast.expr) -> str:
    if isinstance(node, ast.Attribute):
        return f"{_dotted_name(node.value)}.{node.attr}"
    return node.id if isinstance(node, ast.Name) else ""


def _is_docstring(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _first_point(stmt: ast.stmt) -> tuple[int, int]:
    """Earliest (line, column) of a member: decorators or the statement itself."""
    points = [(stmt.lineno, stmt.col_offset)]
    points.extend((d.lineno, d.col_offset) for d in getattr(stmt, "decorator_list", []))
    return min(points)


def _attach_start(lines: list[str], start: int, boundary: int) -> int:
    """0-based first line of a member's slice: its first-token line pulled up
    over whole-line ``#`` comments directly above it.

    ``boundary`` is the 1-based line the walk may not cross (previous member's
    last line, docstring end, or the class header). One blank line between a
    comment block and its member keeps them together; two detach the block
    (it stays with the preceding member's slice — nothing is ever dropped).
    """
    line = start
    while line - 1 >= boundary:
        above = lines[line - 1].strip()
        if above.startswith("#"):
            line -= 1
        elif not above and line - 2 >= boundary and lines[line - 2].strip().startswith("#"):
            line -= 2
        else:
            break
    return line


def _line_offsets(source: str) -> list[int]:
    """Offset of every line's start, plus a len(source) sentinel at the end."""
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py reorder",
        description="Reorder class members into canonical order "
        "(deterministic; same-kind order kept).",
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="show what would change without writing; exit 2 when anything would change",
    )
    parser.add_argument(
        "paths", nargs="*", help="explicit files or directories (default: normal selection)"
    )
    return parser
