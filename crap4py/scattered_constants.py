"""``scattered-constants`` subcommand: flags literals repeated across files.

Port of the crap4dart ``scattered_constants`` gate (e766d68): the corpus-wide
sibling of ``magic-constants``. Two finding kinds:

- **Constant regression** — a named constant for the value already exists
  somewhere in the corpus (module/class-level ALL_CAPS assignment with a
  plain string/number value; first declaration wins on shared values).
  Every hardcoded re-introduction — a bare literal or a duplicate ALL_CAPS
  declaration in another file — flags IMMEDIATELY, at any occurrence count,
  naming the constant and its file:line to reference. The winning declaring
  site itself never flags; duplicate declarations do.
- **Scattered literal** — no constant exists yet and the value reached
  ``--min-files`` (default 3) distinct files. One finding per occurrence
  (file + line): the "change 86400 in every service" hazard that in-file
  repeat counting cannot see — a literal used once per file in N files
  never trips the in-file gate.

Values compare semantically: digit separators do not split a fact, so
``1_000``, ``1000`` and ``0x3E8`` are one value (canonical int value keys,
not raw lexemes like the in-file gate uses).

False-positive controls (upstream defaults):
- strings shorter than ``--min-length`` (4) and numbers of magnitude at or
  below ``--min-value`` (100) are coincidence;
- dict keys, index subscripts and match-case literal patterns are protocol
  identifiers, not constants (shared rules with magic-constants);
- import specifiers are structural references upstream (import/export/part
  URIs) and are not string literals in Python — nothing to skip;
- the winning constant declaration is the blessed form and exempt; a
  computed ALL_CAPS initializer's nested literals stay ordinary sites;
- test files are excluded by default (the normal source selection); Python
  has no generated-code glob convention and build/dist/venv trees are
  already excluded by the source finder.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .analyzer import _relative_to_root
from .args import UsageErrorParser
from .constants import NO_FILES_MESSAGE
from .files import PathLike, expand_paths, find_source_files, is_test_file, parse_file
from .magic_constants import MIN_LENGTH, _identifier_key_ids, _is_const_statement

MIN_FILES = 3
MIN_VALUE = 100

_MAX_DISPLAY = 40

# A named constant in the corpus: name plus the declaration site every
# hardcoded occurrence of the same value should reference.
_Shared = tuple[str, str, int]
# One occurrence site: file, 1-based line, and whether the site is itself a
# constant-declaration initializer (the winning declaration never flags).
_Site = tuple[str, int, bool]


@dataclass(frozen=True, slots=True)
class ScatteredConstant:
    """A single scattered-literal finding."""

    file: str
    line: int
    message: str


@dataclass(frozen=True, slots=True)
class ScatteredConstantsResult:
    """Violations found plus how many files were checked."""

    violations: list[ScatteredConstant]
    checked: int


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py scattered-constants [paths...]``. Exit 2 iff violations."""
    args = _build_parser().parse_args(argv)
    files = (
        expand_paths(args.paths, project_root) if args.paths else find_source_files(project_root)
    )
    if not files:
        print(NO_FILES_MESSAGE)
        return 0
    result = check_files(files, project_root, args.min_files, args.min_length, args.min_value)
    for violation in result.violations:
        print(f"{violation.file}:{violation.line}: {violation.message}")
    print(summary(result))
    return 2 if result.violations else 0


def check_files(
    files: Iterable[PathLike],
    project_root: PathLike,
    min_files: int = MIN_FILES,
    min_length: int = MIN_LENGTH,
    min_value: int = MIN_VALUE,
) -> ScatteredConstantsResult:
    """Flag constant regressions everywhere and literals scattered over ``min_files`` files."""
    root = Path(project_root)
    sites: dict[tuple, list[_Site]] = {}
    constants: dict[tuple, _Shared] = {}
    checked = 0
    for path in files:
        p = Path(path)
        if is_test_file(p, root):
            continue
        tree = parse_file(p)
        if tree is None:
            continue
        checked += 1
        _scan_file(tree, _relative_to_root(p, root), min_length, min_value, sites, constants)
    return ScatteredConstantsResult(_violations(sites, constants, min_files), checked)


def _scan_file(
    tree: ast.Module,
    relative: str,
    min_length: int,
    min_value: int,
    sites: dict[tuple, list[_Site]],
    constants: dict[tuple, _Shared],
) -> None:
    """Register the module's constants, then add its literal sites."""
    const_ids = _register_constants(tree, relative, constants)
    identifiers = _identifier_key_ids(tree) | _fstring_part_ids(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or isinstance(node.value, (bool, type(None))):
            continue
        if id(node) in identifiers:
            continue
        key = _site_key(node.value, min_length, min_value)
        if key is not None:
            sites.setdefault(key, []).append((relative, node.lineno, id(node) in const_ids))


def _fstring_part_ids(tree: ast.Module) -> set[int]:
    """``id()``s of Constant nodes that are static parts of f-strings.

    An f-string is a ``JoinedStr``: its value is dynamic (interpolation)
    and its static text is formatting glue, not a constant — the whole
    f-string is skipped, static parts included.
    """
    return {
        id(part)
        for node in ast.walk(tree)
        if isinstance(node, ast.JoinedStr)
        for part in node.values
        if isinstance(part, ast.Constant)
    }


def _register_constants(
    tree: ast.Module, relative: str, constants: dict[tuple, _Shared]
) -> set[int]:
    """First-wins registry of ALL_CAPS constants with plain literal values.

    Returns the ``id()``s of the registered value nodes — their own sites
    are constant declarations, which never flag as regressions.
    """
    const_ids: set[int] = set()
    for stmt in _const_statements(tree):
        value = stmt.value
        key = _literal_key(value.value) if isinstance(value, ast.Constant) else None
        if key is None:
            continue
        const_ids.add(id(value))
        constants.setdefault(key, (_const_name(stmt), relative, value.lineno))
    return const_ids


def _const_statements(tree: ast.Module):
    """Module- and class-level ALL_CAPS assignments (Python's const convention)."""
    bodies = [tree.body]
    bodies += [node.body for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    for body in bodies:
        for stmt in body:
            if _is_const_statement(stmt):
                yield stmt


def _const_name(stmt: ast.stmt) -> str:
    """Declared name of an ALL_CAPS assignment (first target)."""
    target = stmt.target if isinstance(stmt, ast.AnnAssign) else stmt.targets[0]
    return target.id


def _literal_key(value):
    """Registry key of a plain constant value, else ``None`` (computed/complex)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        return ("s", value)
    if isinstance(value, int):
        return ("i", value)
    if isinstance(value, float):
        return ("f", value)
    return None


def _site_key(value, min_length: int, min_value: int):
    """Canonical corpus key of an eligible literal site, else ``None``.

    Strings compare by value once they reach ``min_length`` characters;
    numbers compare by typed value so digit separators and alternate
    spellings merge (``1_000`` == ``1000``) while ints and floats stay
    distinct facts; magnitudes at or below ``min_value`` are coincidence.
    """
    if isinstance(value, str):
        return ("s", value) if len(value) >= min_length else None
    if isinstance(value, int):
        return ("i", value) if abs(value) > min_value else None
    if isinstance(value, float):
        return ("f", value) if abs(value) > min_value else None
    return None


def _violations(
    sites: dict[tuple, list[_Site]], constants: dict[tuple, _Shared], min_files: int
) -> list[ScatteredConstant]:
    """Regressions first, then scattered sites, ordered by file and line."""
    violations: list[ScatteredConstant] = []
    for key, occurrences in sites.items():
        violations += _violations_for(key, occurrences, constants.get(key), min_files)
    violations.sort(key=lambda v: (v.file, v.line))
    return violations


def _violations_for(
    key: tuple, occurrences: list[_Site], shared: _Shared | None, min_files: int
) -> list[ScatteredConstant]:
    """Findings for one literal value: regressions before scatter findings."""
    if shared is not None:
        return _regressions(key, occurrences, shared)
    return _scatter(key, occurrences, min_files)


def _regressions(key: tuple, occurrences: list[_Site], shared: _Shared) -> list[ScatteredConstant]:
    """Every hardcoded re-use flags at once; the winning declaration is the reference."""
    name, decl_file, decl_line = shared
    return [
        ScatteredConstant(
            file,
            line,
            f"hardcoded {_display(key)} duplicates constant `{name}` "
            f"({decl_file}:{decl_line}) — reference the constant instead of "
            "repeating the literal",
        )
        for file, line, is_const_decl in occurrences
        if not (is_const_decl and file == decl_file and line == decl_line)
    ]


def _scatter(key: tuple, occurrences: list[_Site], min_files: int) -> list[ScatteredConstant]:
    """One finding per occurrence once the value spans ``min_files`` distinct files."""
    file_names = {file for file, _, _ in occurrences}
    if len(file_names) < min_files:
        return []
    scope = f"{len(file_names)} files ({len(occurrences)} occurrences)"
    message = (
        f"literal {_display(key)} repeats in {scope} — extract a shared constant "
        "and reference it from every site; verify all occurrences mean the same "
        "thing first"
    )
    return [ScatteredConstant(file, line, message) for file, line, _ in occurrences]


def _display(key: tuple) -> str:
    """One literal for messages: numbers in canonical form, strings quoted and truncated."""
    kind, value = key
    if kind != "s":
        return str(value)
    text = value if len(value) <= _MAX_DISPLAY else value[:_MAX_DISPLAY] + "…"
    return f"'{text}'"


def summary(result: ScatteredConstantsResult) -> str:
    """One-line summary printed after the violations."""
    if not result.violations:
        return f"{result.checked} files scanned, no literals repeat across files"
    regressions = sum("duplicates constant" in v.message for v in result.violations)
    scattered = len(result.violations) - regressions
    parts = []
    if regressions:
        parts.append(f"{regressions} hardcoded re-use(s) of existing constants")
    if scattered:
        parts.append(f"{scattered} scattered-literal occurrence(s)")
    return f"{', '.join(parts)} in {result.checked} files"


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py scattered-constants",
        description="Flag literals repeated across distinct files (corpus-wide magic constants).",
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument(
        "paths", nargs="*", help="explicit files or directories (default: normal selection)"
    )
    parser.add_argument(
        "--min-files",
        type=int,
        default=MIN_FILES,
        help=f"scatter threshold for values with no constant yet (default: {MIN_FILES})",
    )
    parser.add_argument(
        "--min-length",
        type=int,
        default=MIN_LENGTH,
        help=f"ignore strings shorter than this (default: {MIN_LENGTH})",
    )
    parser.add_argument(
        "--min-value",
        type=int,
        default=MIN_VALUE,
        help=f"ignore numbers of magnitude at or below this (default: {MIN_VALUE})",
    )
    return parser
