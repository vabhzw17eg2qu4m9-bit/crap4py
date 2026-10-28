"""``identical-branches`` subcommand: flags the copy-paste-edit bug family.

Port of the crap4dart ``identical_branches`` gate (c6b2425add): flags
syntactically identical pieces inside one construct where a second, slightly
different piece was intended. Six checks, all comparing by canonical form —
whitespace-, comment- and parenthesis-independent (Python's AST carries no
parentheses or comments, so both fall out of comparing AST structure instead
of source text), with operands of commutative operators sorted and
same-operator nests of associative operators flattened: ``a == b`` matches
``b == a``, ``a and b`` matches ``b and a``:

1. **Duplicate conditions** in one ``if``/``elif`` chain — every later
   branch becomes unreachable.
2. **Duplicate branch bodies** in one chain (``else`` == then; a
   single-statement block equals the naked statement — free in Python,
   where both are a one-element statement list) — one arm was supposed to
   be edited.
3. **Duplicate operands in one boolean chain** (``a or a``, ``a and a``)
   — the operand collapses to itself; ``ready() or ready()`` double-calls
   by accident. Only the outermost link of a same-operator chain reports.
4. **Absorption** — a top-level operand of a boolean chain that also
   appears as a conjunct (``or``) or disjunct (``and``) of a sibling
   operand makes the sibling's extra terms dead: ``a or b and a``
   collapses to ``a``.
5. **Identical ternary branches** (``x if c else x``) — the condition is
   dead.
6. **Duplicate positional arguments in one call** (``f(w, h, w)``) and
   **duplicate except bodies in one ``try``** — the wrong-variable /
   copy-paste-handling bugs. Keyword arguments are exempt (the name
   documents the slot), all-literal argument lists are exempt
   (uniform-value APIs like ``Rectangle(8, 8, 8, 8)`` stay quiet), and a
   literal is never reported as the repeat. Duplicate exception TYPES are
   deliberately not flagged — Python dispatches handlers per type, so the
   types are semantically distinct in a way Dart ``case`` labels are not;
   only the bodies are compared.

Only pieces of the *same* construct are compared; identical conditions in
separate ``if`` statements or nested branches are legal (the state may
change between them). ``match`` is excluded (upstream excludes ``switch``
for the same reason — duplicate patterns are a different gate's business).

Canonical-form operators, per the cross-port contract: ``==``, ``!=`` and
``in`` sort their two operands (``in`` is the Python-only member the
contract adds; membership reads symmetrically for the duplicate
heuristic), ``and``/``or``/``+``/``*``/``&``/``|``/``^`` flatten
same-operator nests and sort, ``is``/``is not``/``not in`` and the
directional comparisons (``< > <= >=``) and non-associative arithmetic
(``- / % // ** << >> @``) keep operand order. Unary operators render
``op(operand)``; leaves render as structural AST text.

Python specifics: there is no ``elif`` AST node (an elif is an ``If`` in
the head's ``orelse``), so an ``if`` after an ``else:`` is by construction
a chain link of the same chain. Test files are excluded by the default
selection, matching the port's other AST gates.

Default severity is ``warning`` (default: findings are reported ``[WARN]``
but do not fail the run; ``--severity error`` fails with exit 2, matching
upstream's per-gate severity config) — reordering is equivalence-safe only
for side-effect-free operands, which pure syntax cannot prove.
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

#: Comparison operators whose two operands may be commutatively sorted.
#: ``is``/``is not`` are deliberately absent — identity reads directionally.
_SORT_COMPARE = (ast.Eq, ast.NotEq, ast.In)

#: Binary operators whose operands may be commutatively sorted.
_SORT_BINOP = (ast.Add, ast.Mult, ast.BitAnd, ast.BitOr, ast.BitXor)

#: Binary operators whose same-operator nests flatten before comparison.
_FLATTEN_BINOP = (ast.Add, ast.Mult, ast.BitAnd, ast.BitOr, ast.BitXor)


@dataclass(frozen=True, slots=True)
class BranchFinding:
    """A single identical-branch finding."""

    file: str
    line: int
    message: str


@dataclass(frozen=True, slots=True)
class IdenticalBranchesResult:
    """Violations found plus how many constructs were checked."""

    violations: list[BranchFinding]
    constructs: int


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py identical-branches [paths...]``.

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
    result = filter_ignored("identical_branches", check_files(files, project_root), project_root)
    errored = args.severity == "error"
    for violation in result.violations:
        prefix = "" if errored else "[WARN] "
        print(f"{prefix}{violation.file}:{violation.line}: {violation.message}")
    print(summary(result))
    return 2 if errored and result.violations else 0


def check_files(files: Iterable[PathLike], project_root: PathLike) -> IdenticalBranchesResult:
    """Scan every non-test file's constructs for the copy-paste-edit family."""
    violations: list[BranchFinding] = []
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
    return IdenticalBranchesResult(violations, constructs)


def file_violations(tree: ast.Module, relative: str) -> IdenticalBranchesResult:
    """All identical-branch findings of one parsed file."""
    scanner = _Duplicates(relative, tree)
    scanner.visit(tree)
    return IdenticalBranchesResult(scanner.violations, scanner.constructs)


class _Duplicates(ast.NodeVisitor):
    """Collects the copy-paste-edit family over one file's AST."""

    def __init__(self, file: str, tree: ast.Module) -> None:
        self._file = file
        self.violations: list[BranchFinding] = []
        self.constructs = 0
        # id()s of same-op bool-chain links (only the outermost link
        # reports) and of elif links (the chain head scans them).
        self._bool_links, self._elif_links = _bool_chain_links(tree), _elif_chain_links(tree)

    def visit_If(self, node: ast.If) -> None:
        if id(node) not in self._elif_links:
            self._scan_chain(node)
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        if id(node) not in self._bool_links:
            self._check_chain(node)
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.constructs += 1
        if _canonical(node.body) == _canonical(node.orelse):
            self._add(
                node,
                "identical then/else branches of conditional expression: "
                f"'{_snippet(node.body)}' — the condition is dead",
            )
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        self.constructs += 1
        seen: dict[str, int] = {}
        for handler in node.handlers:
            key, line = _body_key(handler.body)
            self._report_repeat(
                seen,
                key,
                line,
                f"duplicate except body in try statement: '{_snippet(handler.body)}'",
            )
        self.generic_visit(node)

    # `except*` groups carry handlers of the same shape.
    visit_TryStar = visit_Try

    def visit_Call(self, node: ast.Call) -> None:
        self._check_call(node)
        self.generic_visit(node)

    def _scan_chain(self, head: ast.If) -> None:
        """One if/elif chain: head through the last elif/else link."""
        self.constructs += 1
        conditions: dict[str, int] = {}
        bodies: dict[str, int] = {}
        current: ast.If | None = head
        while current is not None:
            key, line = _body_key(current.body)
            self._report_repeat(
                bodies,
                key,
                line,
                f"duplicate branch body in if/elif chain: '{_snippet_list(current.body)}'",
            )
            self._report_repeat(
                conditions,
                _canonical(current.test),
                current.test.lineno,
                f"duplicate condition in if/elif chain: '{_snippet(current.test)}'",
            )
            orelse = current.orelse
            if len(orelse) == 1 and isinstance(orelse[0], ast.If):
                current = orelse[0]
                continue
            if orelse:
                key, line = _body_key(orelse)
                self._report_repeat(
                    bodies,
                    key,
                    line,
                    f"duplicate branch body in if/elif chain: '{_snippet_list(orelse)}'",
                )
            current = None

    def _check_chain(self, node: ast.BoolOp) -> None:
        self.constructs += 1
        label = _bool_label(node.op)
        operands = _chain_operands(type(node.op), node)
        seen: set[str] = set()
        for key, operand in operands:
            if key in seen:
                self._add(
                    node,
                    f"duplicate operand in '{label}' chain: '{_snippet(operand)}' repeats — "
                    "the chain collapses to the single operand",
                )
                return
            seen.add(key)
        self._check_absorption(node)

    def _check_absorption(self, node: ast.BoolOp) -> None:
        """Absorption law: an operand that also appears as a conjunct
        (``or``) or disjunct (``and``) of a sibling makes the sibling's
        extra terms dead — ``a or b and a`` collapses to ``a``."""
        op_label = _bool_label(node.op)
        nested_type = ast.Or if isinstance(node.op, ast.And) else ast.And
        standalone = {key for key, _ in _chain_operands(type(node.op), node)}
        for sibling_key, sibling in _chain_operands(type(node.op), node):
            for conjunct_key, conjunct in _chain_operands(nested_type, sibling):
                if conjunct_key == sibling_key or conjunct_key not in standalone:
                    continue
                self._add(
                    conjunct,
                    f"operand of '{op_label}' is implied by another operand "
                    f"(absorption): '{_snippet(conjunct)}' also appears "
                    "standalone — the extra terms are dead",
                )
                return

    def _check_call(self, node: ast.Call) -> None:
        if len(node.args) + len(node.keywords) < 2:
            return
        self.constructs += 1
        # Positional slots only: a keyword argument names its slot, so a
        # repeated value there is self-documenting (a=x, b=x), while a
        # repeated positional value is indistinguishable from a
        # wrong-variable bug. A `**` expansion is unnamed, so it counts as
        # positional too.
        positional = _positional_args(node)
        if len(positional) < 2 or all(isinstance(arg, ast.Constant) for arg in positional):
            return
        self._report_arg_repeats(node.func, positional)

    def _report_arg_repeats(self, func: ast.expr, positional: list[ast.expr]) -> None:
        """Report repeated non-literal positional arguments (a literal is
        never the repeat — deliberate uniform literals stay quiet)."""
        seen: dict[str, int] = {}
        callee = _callee_name(func)
        for index, argument in enumerate(positional, start=1):
            if isinstance(argument, ast.Constant):
                continue
            key = _canonical(argument)
            first = seen.get(key)
            if first is None:
                seen[key] = index
            else:
                self._add(
                    argument,
                    f"duplicate argument in call to '{callee}': "
                    f"'{_snippet(argument)}' (same as argument {first})",
                )

    def _report_repeat(
        self, seen: dict[str, int], key: str, line: int, message_prefix: str
    ) -> None:
        first = seen.get(key)
        if first is None:
            seen[key] = line
            return
        self.violations.append(
            BranchFinding(self._file, line, f"{message_prefix} (same as line {first})")
        )

    def _add(self, node: ast.expr, message: str) -> None:
        self.violations.append(BranchFinding(self._file, node.lineno, message))


def _canonical(node: ast.expr) -> str:
    """The canonical form of an expression: whitespace- and parenthesis-
    independent, with operands of commutative operators sorted and
    same-operator nests of associative operators flattened."""
    if isinstance(node, ast.BoolOp):
        return _boolop_canonical(node)
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, _FLATTEN_BINOP):
            operands = _flatten_binop(type(node.op), node)
            if isinstance(node.op, _SORT_BINOP):
                operands = sorted(operands)
            return f"{type(node.op).__name__}({','.join(operands)})"
        return f"{type(node.op).__name__}({_canonical(node.left)},{_canonical(node.right)})"
    if isinstance(node, ast.UnaryOp):
        return f"{type(node.op).__name__}({_canonical(node.operand)})"
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        return _compare_canonical(node)
    return ast.dump(node, annotate_fields=False, include_attributes=False)


def _boolop_canonical(node: ast.BoolOp) -> str:
    operands = [key for key, _ in _chain_operands(type(node.op), node)]
    return f"{_bool_label(node.op)}({','.join(sorted(operands))})"


def _compare_canonical(node: ast.Compare) -> str:
    """One comparison: commutative operands (``==``, ``!=``, ``in``) sort;
    directional comparisons and ``is`` keep their operand order."""
    label = type(node.ops[0]).__name__
    left, right = _canonical(node.left), _canonical(node.comparators[0])
    if isinstance(node.ops[0], _SORT_COMPARE):
        left, right = sorted((left, right))
    return f"{label}({left},{right})"


def _flatten_binop(op_type: type, node: ast.expr) -> list[str]:
    out: list[str] = []
    if isinstance(node, ast.BinOp) and type(node.op) is op_type:
        out.extend(_flatten_binop(op_type, node.left))
        out.extend(_flatten_binop(op_type, node.right))
    else:
        out.append(_canonical(node))
    return out


def _bool_chain_links(tree: ast.Module) -> set[int]:
    """id()s of BoolOp nodes that extend a same-operator chain — only the
    outermost link of such a chain checks operand duplicates."""
    links: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.BoolOp):
            links.update(
                id(value)
                for value in node.values
                if isinstance(value, ast.BoolOp) and type(value.op) is type(node.op)
            )
    return links


def _elif_chain_links(tree: ast.Module) -> set[int]:
    """id()s of If nodes that extend a chain — Python has no elif node (an
    elif is an If in the head's orelse), so the chain head scans them."""
    links: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.If)
            and len(node.orelse) == 1
            and isinstance(node.orelse[0], ast.If)
        ):
            links.add(id(node.orelse[0]))
    return links


def _positional_args(node: ast.Call) -> list[ast.expr]:
    """The call's positional slots; a `**` expansion is unnamed, so it
    counts as positional too."""
    return list(node.args) + [kw.value for kw in node.keywords if kw.arg is None]


def _callee_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return _snippet(func)


def _chain_operands(op_type: type, node: ast.expr) -> list[tuple[str, ast.expr]]:
    """The top-level operands of an op chain rooted at ``node``, as
    canonical keys paired with their source nodes. Same-operator nests
    flatten (``a and b and c`` compares equal regardless of grouping);
    other operators keep their tree shape. A non-chain root yields the
    root itself."""
    out: list[tuple[str, ast.expr]] = []

    def add(part: ast.expr) -> None:
        if isinstance(part, ast.BoolOp) and type(part.op) is op_type:
            for value in part.values:
                add(value)
        else:
            out.append((_canonical(part), part))

    add(node)
    return out


def _bool_label(op: ast.boolop) -> str:
    return "and" if isinstance(op, ast.And) else "or"


def _body_key(stmts: list[ast.stmt]) -> tuple[str, int]:
    """The canonical key of a statement list plus the line of its first
    statement: a single-statement list equals the naked statement (free in
    Python, where both spellings are a one-element list — the join of one
    statement is that statement), so the violation points at the code, not
    at indentation."""
    return ";".join(_stmt_text(stmt) for stmt in stmts), stmts[0].lineno


def _stmt_text(node: ast.AST) -> str:
    return ast.dump(node, annotate_fields=False, include_attributes=False)


def _snippet(node: ast.expr) -> str:
    return _truncate(ast.unparse(node))


def _snippet_list(stmts: list[ast.stmt]) -> str:
    return _truncate("; ".join(ast.unparse(stmt) for stmt in stmts))


def _truncate(text: str) -> str:
    return text if len(text) <= 40 else f"{text[:37]}..."


def summary(result: IdenticalBranchesResult) -> str:
    """One-line summary printed after the violations."""
    if not result.violations:
        return f"{result.constructs} constructs checked, no identical-branch findings"
    return (
        f"{len(result.violations)} identical-branch findings "
        f"({result.constructs} constructs checked)"
    )


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py identical-branches",
        description=(
            "Flag the copy-paste-edit family: duplicate conditions, branch bodies, "
            "boolean operands, ternary branches, positional arguments and except "
            "bodies inside one construct."
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
