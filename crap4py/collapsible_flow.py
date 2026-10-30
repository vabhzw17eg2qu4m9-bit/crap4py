"""``collapsible-flow`` subcommand: flags mechanical control-flow nesting.

Port of the crap4dart ``collapsible_flow`` gate (7bdfd64ae1): flags shapes a
semantics-preserving mechanical rewrite can flatten. Three checks, resolved
from the parsed AST only:

1. **Collapsible if** — ``if a:`` whose suite holds only an inner ``if``
   with no ``else`` on either level merges into ``if a and b:``. Python's
   ``and`` short-circuits, so evaluation order is preserved and the merge is
   always equivalent (``await``ed or side-effecting conditions included).
   Python ``if`` has no pattern condition to block a merge (``match`` is a
   separate statement, never merged).
2. **Redundant else** — ``if c:``/``else:`` where the then-suite *always
   exits*: ends with ``return``/``raise`` (bare ``raise`` = rethrow)/
   ``break``/``continue``, an ``if``/``else`` pair whose both branches exit,
   or a ``try`` whose body exits and whose ``finally`` is absent, empty, or
   exits. The ``else`` is redundant — dedent its body. An inner ``if``
   *without* an ``else`` never counts as exiting: control falls through when
   its condition is false, so dedenting would change behavior. ``match``
   bodies are not analyzed (like upstream's ``switch``: pattern fall-through
   semantics), ``except`` handlers are not analyzed (as upstream, only the
   body and ``finally`` decide).
3. **Chain after terminal then** — an ``elif`` (an ``If`` in the head's
   ``orelse`` — Python has no elif node) following an always-exiting
   then-suite. The whole chain wants a guard sequence or a dispatch table;
   that rewrite is not mechanical, so this check only points at it (no
   fix-shaped message).

Python specifics beyond the upstream mapping: a redundant else on an
*elif link* (the link's own then exits, its else is plain) must not
bare-dedent — with no braces, the body would leave the head's else scope
and run after earlier falling-through branches — so its hint says to
nest the chain under the enclosing else first; a head-level or
then-nested redundant else dedents safely and keeps the upstream hint.

Every finding is correct code — this gate trades flatness, not bugs. Default
severity is ``warning``: findings are reported ``[WARN]`` but do not fail the
run; ``--severity error`` fails with exit 2, matching upstream's per-gate
severity config. Independently validated by DCM's ``avoid-collapsible-if``
and ``avoid-redundant-else``.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .analyzer import _relative_to_root
from .args import UsageErrorParser
from .constants import NO_FILES_MESSAGE
from .files import PathLike, expand_paths, find_source_files, is_test_file
from .ignore_filter import filter_ignored

#: Statements that leave their enclosing construct in every case. Bare
#: ``raise`` (rethrow) and ``raise X`` are the same node in Python's AST.
_JUMPS = (ast.Return, ast.Break, ast.Continue, ast.Raise)


@dataclass(frozen=True, slots=True)
class FlowFinding:
    """A single collapsible-flow finding."""

    file: str
    line: int
    message: str


@dataclass(frozen=True, slots=True)
class CollapsibleFlowResult:
    """Violations found plus how many if statements were checked."""

    violations: list[FlowFinding]
    constructs: int


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py collapsible-flow [paths...]``.

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
    result = filter_ignored("collapsible_flow", check_files(files, project_root), project_root)
    errored = args.severity == "error"
    for violation in result.violations:
        prefix = "" if errored else "[WARN] "
        print(f"{prefix}{violation.file}:{violation.line}: {violation.message}")
    print(summary(result))
    return 2 if errored and result.violations else 0


def check_files(files: Iterable[PathLike], project_root: PathLike) -> CollapsibleFlowResult:
    """Scan every non-test file's if statements for collapsible shapes."""
    violations: list[FlowFinding] = []
    constructs = 0
    for path in files:
        if is_test_file(path, project_root):
            continue
        try:
            source = Path(path).read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            print(f"Warning: could not parse {path}: {exc}", file=sys.stderr)
            continue
        relative = _relative_to_root(Path(path), Path(project_root))
        findings = file_violations(tree, relative)
        violations.extend(findings.violations)
        constructs += findings.constructs
    return CollapsibleFlowResult(violations, constructs)


def file_violations(tree: ast.Module, relative: str) -> CollapsibleFlowResult:
    """All collapsible-flow findings of one parsed file."""
    scanner = _Scanner(relative, tree)
    scanner.visit(tree)
    return CollapsibleFlowResult(scanner.violations, scanner.constructs)


def _elif_links(tree: ast.Module) -> set[int]:
    """id()s of ``If`` nodes that extend a chain — Python has no elif node,
    a link is the lone ``If`` in a head's ``orelse`` (the pattern
    ``identical_branches`` uses). A link's redundant else must not
    bare-dedent: with no braces, the body would leave the head's else
    scope entirely."""
    return {
        id(node.orelse[0])
        for node in ast.walk(tree)
        if isinstance(node, ast.If) and len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If)
    }


class _Scanner(ast.NodeVisitor):
    """Collects the collapsible-flow family over one file's AST."""

    def __init__(self, file: str, tree: ast.Module) -> None:
        self._file = file
        self._elif_links = _elif_links(tree)
        self.violations: list[FlowFinding] = []
        self.constructs = 0

    def visit_If(self, node: ast.If) -> None:
        self.constructs += 1
        self._check_collapsible(node)
        self._check_redundant_else(node)
        self.generic_visit(node)

    def _add(self, node: ast.stmt, message: str) -> None:
        self.violations.append(FlowFinding(self._file, node.lineno, message))

    def _check_collapsible(self, node: ast.If) -> None:
        """Check 1: `if a:` holding only an inner `if`, no else anywhere."""
        if node.orelse:
            return
        if len(node.body) != 1 or not isinstance(node.body[0], ast.If):
            return
        if node.body[0].orelse:
            return
        self._add(node, "nested if can be merged into a single condition with `and`")

    def _check_redundant_else(self, node: ast.If) -> None:
        """Checks 2 and 3: an always-exiting then-suite makes a plain `else`
        redundant (dedent) and turns an `elif` into a chain flattening hint."""
        if not node.orelse or not _exits(node.body):
            return
        if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            self._add(
                node.orelse[0],
                "else-if follows an always-exiting then branch — flatten the "
                "chain into guard clauses or a dispatch table",
            )
        elif id(node) in self._elif_links:
            # Python has no braces: bare-dedenting an elif link's else body
            # escapes the head's else scope when an earlier branch falls
            # through — nest the chain first, then dedent within it.
            self._add(
                node,
                "redundant else after an always-exiting then branch — nest "
                "the chain under the enclosing else and dedent the else body",
            )
        else:
            self._add(
                node,
                "redundant else after an always-exiting then branch — dedent the else body",
            )


def _exits(stmts: list[ast.stmt]) -> bool:
    """A suite exits when it is non-empty and its last statement exits."""
    return bool(stmts) and _always_exits(stmts[-1])


def _always_exits(stmt: ast.stmt | None) -> bool:
    """Whether ``stmt`` always exits its enclosing construct (the enclosing
    ``if``/loop/function). Conservative: an ``if`` without an ``else``
    falls through and never counts; ``match``/``for``/``while``/``with``
    bodies are not analyzed (Python fall-through or loop semantics)."""
    if isinstance(stmt, _JUMPS):
        return True
    if isinstance(stmt, ast.If):
        # A bare `if` falls through when its condition is false — the
        # else must exist and exit on both sides.
        return bool(stmt.orelse) and _exits(stmt.body) and _exits(stmt.orelse)
    if isinstance(stmt, (ast.Try, ast.TryStar)):
        return _exits_as_try(stmt)
    return False


def _exits_as_try(stmt: ast.Try | ast.TryStar) -> bool:
    """A try exits when its body exits and its finally is absent, empty
    (`pass` — Python cannot write an empty suite), or exits. Handlers are
    not analyzed (as upstream's catch clauses)."""
    return _exits(stmt.body) and (_is_noop(stmt.finalbody) or _exits(stmt.finalbody))


def _is_noop(stmts: list[ast.stmt]) -> bool:
    """An absent suite, or Python's only way to write one: a lone ``pass``."""
    return not stmts or (len(stmts) == 1 and isinstance(stmts[0], ast.Pass))


def summary(result: CollapsibleFlowResult) -> str:
    """One-line summary printed after the violations."""
    if not result.violations:
        return f"{result.constructs} if statements checked, no collapsible flow"
    return (
        f"{len(result.violations)} collapsible-flow findings "
        f"({result.constructs} if statements checked)"
    )


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py collapsible-flow",
        description=(
            "Flag control-flow nesting a semantics-preserving mechanical rewrite "
            "can flatten: collapsible nested ifs (`and` merge), redundant else "
            "after an always-exiting then branch, and elif chains after a "
            "terminal then."
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
