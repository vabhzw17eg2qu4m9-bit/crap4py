"""``string-hazards`` subcommand: flags string literal hazards.

Port of the crap4dart ``string_hazards`` gate (62fec6833e): flags string
literals whose escaping or pattern usage contradicts what the author almost
certainly meant, from the parsed AST only. Three near-zero-false-positive
kinds, each mapped to Python's honest analog of the upstream Dart shape:

1. **Escaped f-string interpolation** — upstream's ``\\$name`` inside a
   non-raw string renders the literal text ``$name``; Python's analog is
   ``{{name}}`` inside an f-string (doubled braces render a literal
   ``{name}``). Flagged only when the same identifier is genuinely
   interpolated elsewhere in the same file (sibling evidence), which keeps
   deliberate literal braces quiet. Raw-ness is deliberately not consulted:
   unlike Dart raw strings (where ``\\$`` stays ``\\$``), ``{{ }}``
   unescaping is a brace rule that behaves identically in raw f-strings, so
   ``rf'{{name}}'`` has exactly the same hazard. Plain (non-f) strings never
   interpolate in Python — nothing to escape, never scanned.
2. **Escaped regex anchor** — a string literal passed to the ``re.compile``
   family whose value ends with ``\\$`` or starts with ``\\^``: the regex
   matches a literal dollar/caret where the end/start anchor was almost
   certainly intended. The check is value-based, not raw-based: in Python an
   unrecognized escape such as ``'\\$'`` survives into the regex in BOTH raw
   and non-raw strings (a non-raw ``'\\$'`` is ``\\$``, unlike Dart where it
   decodes to a bare ``$`` anchor), so both spellings carry the same bug the
   upstream raw-string check catches.
3. **Regex anchors in literal ``str`` patterns** — ``str.replace`` /
   ``split`` / ``rsplit`` / ``partition`` / ``rpartition`` treat their
   pattern argument as literal text, so a leading ``^`` or trailing ``$``
   can only match that literal character; a regex was probably intended.
   Only a string-literal first positional argument is checked (a name or
   call argument is the author's own regex composition upstream spares too).
   Python has no adjacent-string literals (implicit concatenation already
   merges into one constant), so the merged value is checked whole. A value
   ending ``\\$`` is spared — an explicitly escaped dollar is deliberate.
   ``re.split`` is a regex call (kind 2), not the literal kind 3.

Deliberate literal dollars/carets (shell text, prices) trip the warnings
occasionally — that is what warning severity is for (default ``warning``:
findings are reported ``[WARN]`` but do not fail the run; ``--severity
error`` fails with exit 2, matching upstream's per-gate severity config).
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
from .constants import NO_FILES_MESSAGE
from .files import PathLike, expand_paths, find_source_files, is_test_file
from .ignore_filter import filter_ignored

#: Instance methods whose pattern argument is matched literally.
LITERAL_PATTERN_METHODS = frozenset({"replace", "split", "rsplit", "partition", "rpartition"})

#: ``re`` functions whose first argument is a regex pattern source.
RE_PATTERN_FUNCTIONS = frozenset(
    {"compile", "match", "search", "fullmatch", "findall", "finditer", "split", "sub", "subn"}
)

#: ``{identifier`` inside an f-string's decoded literal text: literal braces
#: in decoded f-string parts can only come from ``{{`` escapes, so this is
#: an escaped interpolation (the raw ``{{name`` form).
_ESCAPED_BRACE = re.compile(r"\{([A-Za-z_]\w*)")


@dataclass(frozen=True, slots=True)
class StringHazard:
    """A single string-literal hazard finding."""

    file: str
    line: int
    message: str


@dataclass(frozen=True, slots=True)
class StringHazardsResult:
    """Violations found plus how many files were checked."""

    violations: list[StringHazard]
    checked: int


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py string-hazards [paths...]``.

    Exit 0 by default (warning severity: findings are reported, not fatal);
    exit 2 only under ``--severity error`` with findings.
    """
    args = _build_parser().parse_args(argv)
    files = (
        expand_paths(args.paths, project_root) if args.paths else find_source_files(project_root)
    )
    if not files:
        print(NO_FILES_MESSAGE)
        return 0
    result = filter_ignored("string_hazards", check_files(files, project_root), project_root)
    errored = args.severity == "error"
    for violation in result.violations:
        prefix = "" if errored else "[WARN] "
        print(f"{prefix}{violation.file}:{violation.line}: {violation.message}")
    print(summary(result))
    return 2 if errored and result.violations else 0


def check_files(files: Iterable[PathLike], project_root: PathLike) -> StringHazardsResult:
    """Scan every non-test file for the three string-hazard kinds."""
    violations: list[StringHazard] = []
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
    return StringHazardsResult(violations, checked)


def file_violations(tree: ast.Module, source: str, relative: str) -> list[StringHazard]:
    """All string-hazard findings of one parsed file."""
    visitor = _HazardsVisitor(relative)
    visitor.visit(tree)
    return visitor.resolve()


class _HazardsVisitor(ast.NodeVisitor):
    """Collects the three hazard kinds over one file's AST."""

    def __init__(self, file: str) -> None:
        self._file = file
        self.violations: list[StringHazard] = []
        self._escaped: list[tuple[str, int]] = []
        self._genuine: set[str] = set()

    def resolve(self) -> list[StringHazard]:
        """Emit escaped-brace findings backed by sibling evidence, once per (line, name)."""
        seen: set[tuple[int, str]] = set()
        for name, line in self._escaped:
            if name not in self._genuine or (line, name) in seen:
                continue
            seen.add((line, name))
            self.violations.append(
                StringHazard(
                    self._file,
                    line,
                    # '{{{{{name}}}}}' → literal '{{' + value + '}}'.
                    f"escaped interpolation '{{{{{name}}}}}' renders literal '{{{name}}}'; "
                    f"'{{{name}}}' is interpolated elsewhere in this file — stray doubled "
                    "braces?",
                )
            )
        return self.violations

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        for value in node.values:
            if isinstance(value, ast.Constant):
                for match in _ESCAPED_BRACE.finditer(value.value):
                    self._escaped.append((match.group(1), getattr(value, "lineno", node.lineno)))
            elif isinstance(value, ast.FormattedValue):
                root = _root_name(value.value)
                if root is not None:
                    self._genuine.add(root)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute):
            target = func.value
            is_re = isinstance(target, ast.Name) and target.id == "re"
            if is_re and func.attr in RE_PATTERN_FUNCTIONS:
                self._check_regex_anchor(node)
            elif func.attr in LITERAL_PATTERN_METHODS:
                self._check_literal_pattern(node)
        self.generic_visit(node)

    def _check_regex_anchor(self, node: ast.Call) -> None:
        literal = _first_pattern_literal(node, "pattern")
        if literal is None:
            return
        value = literal.value
        if value.endswith(r"\$"):
            self._add(
                literal,
                "regex pattern ends with '\\$' — that is a literal '$' match, not the "
                "end anchor; did you mean '$'?",
            )
        elif value.startswith(r"\^"):
            self._add(
                literal,
                "regex pattern starts with '\\^' — that is a literal '^' match, not the "
                "start anchor; did you mean '^'?",
            )

    def _check_literal_pattern(self, node: ast.Call) -> None:
        if not node.args:
            return
        literal = node.args[0]
        if not (isinstance(literal, ast.Constant) and isinstance(literal.value, str)):
            return
        value = literal.value
        if value.startswith("^"):
            self._add(
                literal,
                "literal pattern starts with '^' — str patterns match literally, so this "
                "can only match a literal '^' character; a regex was probably intended",
            )
        if value.endswith("$") and not value.endswith("\\$"):
            self._add(
                literal,
                "literal pattern ends with '$' — str patterns match literally, so this "
                "can only match a literal '$' character; a regex was probably intended",
            )

    def _add(self, node: ast.expr, message: str) -> None:
        self.violations.append(StringHazard(self._file, node.lineno, message))


def _first_pattern_literal(node: ast.Call, keyword: str) -> ast.Constant | None:
    """The string-literal pattern argument: first positional or named, else None."""
    if node.args:
        literal = node.args[0]
    else:
        literal = next((kw.value for kw in node.keywords if kw.arg == keyword), None)
    if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
        return literal
    return None


def _root_name(node: ast.expr) -> str | None:
    """The root receiver of an expression: ``a.b[i].c()`` → ``a`` (sibling-evidence key)."""
    while True:
        if isinstance(node, (ast.Attribute, ast.Subscript)):
            node = node.value
        elif isinstance(node, ast.Call):
            node = node.func
        else:
            break
    return node.id if isinstance(node, ast.Name) else None


def summary(result: StringHazardsResult) -> str:
    """One-line summary printed after the violations."""
    if result.violations:
        return f"{len(result.violations)} string hazard(s) in {result.checked} files"
    return f"{result.checked} files free of string hazards"


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py string-hazards",
        description=(
            "Flag escaped f-string interpolations, escaped regex anchors, and anchors "
            "in literal str patterns."
        ),
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument(
        "paths", nargs="*", help="explicit files or directories (default: normal selection)"
    )
    parser.add_argument(
        "--severity",
        choices=("warning", "error"),
        default="warning",
        help="warning: report findings, exit 0 (default); error: findings exit 2",
    )
    return parser
