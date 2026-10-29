"""``enum-name-contract`` subcommand: flags built-in ``.name`` contracts inside enum bodies.

Port of the crap4dart ``enum_name_contract`` gate (418f1c58dc): flags reads of
the built-in ``.name`` member inside enum class bodies on provable enum
receivers. ``Color.RED.name`` is ``"RED"`` until the constant is renamed — and
every persisted YAML/JSON/DB row written from it drifts with the rename. The
fix is an explicit value field contracted on as ``.value`` (Python's analog of
upstream's ``final String value`` enhanced-enum field).

Provable receivers, audited inside enum class bodies only:

- ``self`` — ``self.name`` in an instance method;
- implicit ``self`` — a bare ``name`` identifier read in an instance method
  body with no local shadowing;
- the enum's own constants — bare ``RED.name`` or qualified ``Color.RED.name``;
- loop variables iterating the enum's values/members — ``for c in Color``,
  ``for c in list(Color)``, ``for k in Color.__members__``, plus the
  comprehension forms;
- parameters explicitly annotated with the enclosing enum type
  (``c: Color``; string spellings ``c: "Color"`` included).

Upstream also polices ``.index``; Python enums have no ``.index`` member
(``list(Enum).index(x)`` is a list method, not a built-in) — omitted. The
upstream ``values[i]`` receiver shape has no common Python idiom to track;
the ``for x in`` loop forms cover the iteration idiom. ``.value`` IS the
explicit contract and is never flagged.

Guards: an enum declaring its own ``name`` attribute shadows the built-in;
assignment and invocation positions are not reads (``x.name()`` is a call,
not a read, at any receiver; and ``x.name``'s ``name`` is a string
attribute in Python's AST, structurally never an implicit-self candidate);
``staticmethod``/``classmethod`` bodies have no implicit self; usages
outside enum bodies are Tier 2 (they need type matching) and out of scope.

Default ``warning``: findings are reported ``[WARN]`` but do not fail the
run; ``--severity error`` fails with exit 2.
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

#: Bases that make a class an enum (same family as reorder.py's
#: ``_ENUM_BASE_NAMES``), transitively extended by in-file enum subclasses.
_ENUM_BASE_NAMES = frozenset({"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"})

#: Method kinds audited inside an enum body.
_METHOD_KINDS = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True, slots=True)
class EnumNameContractHazard:
    """A single built-in ``.name`` contract finding."""

    file: str
    line: int
    message: str


@dataclass(frozen=True, slots=True)
class EnumNameContractsResult:
    """Violations found plus how many files were checked."""

    violations: list[EnumNameContractHazard]
    checked: int


@dataclass(frozen=True, slots=True)
class _Scope:
    """What one enum method body provably knows about its enclosing enum."""

    enum_name: str
    constants: frozenset[str]
    enum_locals: frozenset[str]
    locals_: frozenset[str]
    has_self: bool


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py enum-name-contract [paths...]``.

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
    result = filter_ignored("enum_name_contract", check_files(files, project_root), project_root)
    errored = args.severity == "error"
    for violation in result.violations:
        prefix = "" if errored else "[WARN] "
        print(f"{prefix}{violation.file}:{violation.line}: {violation.message}")
    print(summary(result))
    return 2 if errored and result.violations else 0


def check_files(files: Iterable[PathLike], project_root: PathLike) -> EnumNameContractsResult:
    """Scan every non-test file for built-in ``.name`` contracts in enum bodies."""
    violations: list[EnumNameContractHazard] = []
    checked = 0
    for path in files:
        if is_test_file(path, project_root):
            continue
        try:
            tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            print(f"Warning: could not parse {path}: {exc}", file=sys.stderr)
            continue
        checked += 1
        relative = _relative_to_root(Path(path), Path(project_root))
        violations.extend(file_violations(tree, relative))
    return EnumNameContractsResult(violations, checked)


def file_violations(tree: ast.Module, relative: str) -> list[EnumNameContractHazard]:
    """All enum-name-contract findings of one parsed file."""
    violations: list[EnumNameContractHazard] = []
    enums = _enum_class_names(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name in enums:
            violations.extend(_audit_enum(node, relative))
    return violations


def _enum_class_names(tree: ast.Module) -> set[str]:
    """In-file enum class names: known-base subclasses plus transitive in-file subclasses."""
    names: set[str] = set()
    while True:
        fresh = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and node.name not in names
            and _has_enum_base(node, names)
        ]
        if not fresh:
            return names
        names.update(node.name for node in fresh)


def _has_enum_base(node: ast.ClassDef, known: set[str]) -> bool:
    return any(
        _base_name(base) in _ENUM_BASE_NAMES or _base_name(base) in known for base in node.bases
    )


def _base_name(base: ast.expr) -> str | None:
    """The unqualified name of a base expression: ``Enum`` or ``enum.Enum`` → ``Enum``."""
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return None


def _audit_enum(node: ast.ClassDef, relative: str) -> list[EnumNameContractHazard]:
    """One enum body: audit every method for built-in ``.name`` reads."""
    if _shadows_name(node):
        return []
    scope_base = (node.name, _member_constants(node))
    hazards: list[EnumNameContractHazard] = []
    for member in node.body:
        if isinstance(member, _METHOD_KINDS):
            hazards.extend(_audit_method(member, *scope_base, relative))
    return hazards


def _shadows_name(node: ast.ClassDef) -> bool:
    """Whether the enum declares its own ``name`` member (shadows the built-in)."""
    return any(_declares_name(member) for member in node.body)


def _declares_name(member: ast.stmt) -> bool:
    if isinstance(member, _METHOD_KINDS):
        return member.name == "name"
    if isinstance(member, ast.Assign):
        return _assigns_name(member.targets)
    if isinstance(member, ast.AnnAssign):
        return isinstance(member.target, ast.Name) and member.target.id == "name"
    return False


def _assigns_name(targets: list[ast.expr]) -> bool:
    return any(isinstance(t, ast.Name) and t.id == "name" for t in targets)


def _member_constants(node: ast.ClassDef) -> frozenset[str]:
    """Enum member names assigned at class-body level."""
    out: set[str] = set()
    for member in node.body:
        if isinstance(member, ast.Assign):
            out.update(t.id for t in member.targets if isinstance(t, ast.Name))
        elif isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name):
            out.add(member.target.id)
    return frozenset(out)


def _audit_method(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    enum_name: str,
    constants: frozenset[str],
    relative: str,
) -> list[EnumNameContractHazard]:
    """One method body: flag ``.name`` reads on provable enum receivers."""
    # ponytail: one scope for the whole subtree — a nested def shares the
    # enclosing method's self/local view, matching upstream's body-wide walk.
    has_self = not _decorated(fn, {"staticmethod", "classmethod"})
    scope = _Scope(
        enum_name=enum_name,
        constants=constants,
        enum_locals=_enum_loop_vars(fn, enum_name) | _enum_params(fn, enum_name),
        locals_=_locals(fn),
        has_self=has_self,
    )
    parents = {child: p for p in ast.walk(fn) for child in ast.iter_child_nodes(p)}
    hazards: list[EnumNameContractHazard] = []
    for node in ast.walk(fn):
        message = _hazard_message(node, parents.get(node), scope)
        if message:
            hazards.append(EnumNameContractHazard(relative, node.lineno, message))
    return hazards


def _decorated(fn, names: set[str]) -> bool:
    return any(isinstance(d, ast.Name) and d.id in names for d in fn.decorator_list)


def _hazard_message(node: ast.expr, parent: ast.AST | None, scope: _Scope) -> str | None:
    """The finding for one node, or ``None`` when it is not a policed read."""
    if isinstance(node, ast.Attribute):
        if _is_invocation(node, parent):
            return None
        return _attribute_hazard(node, scope)
    if isinstance(node, ast.Name) and scope.has_self:
        return _bare_name_hazard(node, parent, scope)
    return None


def _is_invocation(node: ast.expr, parent: ast.AST | None) -> bool:
    """Whether the attribute is the called function: ``x.name()`` is a call, not a read."""
    return isinstance(parent, ast.Call) and parent.func is node


def _attribute_hazard(node: ast.Attribute, scope: _Scope) -> str | None:
    if node.attr == "name" and isinstance(node.ctx, ast.Load) and _provable(node.value, scope):
        return _message(scope.enum_name)
    return None


def _bare_name_hazard(node: ast.Name, parent: ast.AST | None, scope: _Scope) -> str | None:
    if node.id == "name" and isinstance(node.ctx, ast.Load) and _is_bare_read(node, parent, scope):
        return _message(scope.enum_name)
    return None


def _provable(receiver: ast.expr, scope: _Scope) -> bool:
    """Whether a ``.name`` receiver is provably an enum value of the audited enum."""
    if isinstance(receiver, ast.Name):
        return _provable_name(receiver.id, scope)
    return _provable_qualified(receiver, scope)


def _provable_name(name: str, scope: _Scope) -> bool:
    return (
        (name == "self" and scope.has_self) or name in scope.enum_locals or name in scope.constants
    )


def _provable_qualified(receiver: ast.expr, scope: _Scope) -> bool:
    """A qualified constant receiver: ``Color.RED``."""
    return (
        isinstance(receiver, ast.Attribute)
        and isinstance(receiver.value, ast.Name)
        and receiver.value.id == scope.enum_name
        and receiver.attr in scope.constants
    )


def _is_bare_read(node: ast.Name, parent: ast.AST | None, scope: _Scope) -> bool:
    """Whether a bare ``name`` identifier is an implicit-``self`` read.

    Not when a local shadows it, and not in invocation position (``name()``
    is a call, not a read). Assignment/del targets carry a non-Load context
    and never reach here; ``x.name``'s ``name`` is a string attribute in the
    AST, structurally not an identifier.
    """
    if node.id in scope.locals_:
        return False
    return not (isinstance(parent, ast.Call) and parent.func is node)


def _locals(fn) -> frozenset[str]:
    """All names bound anywhere in the function subtree.

    Params, assignments, ``for``/``with``/walrus targets (Store/Del names),
    and ``except ... as`` names. # ponytail: match-capture and import-alias
    bindings are string fields — add if a false positive ever ships.
    """
    out: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.arg):
            out.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            out.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            out.add(node.name)
    return frozenset(out)


def _enum_params(fn, enum_name: str) -> frozenset[str]:
    """Parameters explicitly annotated with the enclosing enum (string spellings too).

    Collected over the whole subtree — nested defs' enum-annotated params are
    enum locals of the member body, registered flat as upstream does.
    """
    out: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, _METHOD_KINDS):
            out.update(_annotated_args(node.args, enum_name))
    return frozenset(out)


def _annotated_args(args: ast.arguments, enum_name: str) -> set[str]:
    params = [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]
    return {
        p.arg
        for p in params
        if p is not None
        and p.annotation is not None
        and _annotation_name(p.annotation) == enum_name
    }


def _annotation_name(annotation: ast.expr) -> str | None:
    """The spelled name of an annotation: ``Color``, ``'Color'``, or ``mod.Color``."""
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        return annotation.value
    return None


def _enum_loop_vars(fn, enum_name: str) -> frozenset[str]:
    """Loop variables iterating the enum's values/members, loops and comprehensions."""
    out: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            if isinstance(node.target, ast.Name) and _is_enum_iterable(node.iter, enum_name):
                out.add(node.target.id)
    return frozenset(out)


def _is_enum_iterable(expr: ast.expr, enum_name: str) -> bool:
    """Whether an iterable expression is the enum: ``Enum``/``Enum.__members__``/``list(Enum)``."""
    if isinstance(expr, ast.Name):
        return expr.id == enum_name
    if isinstance(expr, ast.Attribute):
        return isinstance(expr.value, ast.Name) and expr.value.id == enum_name
    return _is_list_of_enum(expr, enum_name)


def _is_list_of_enum(expr: ast.expr, enum_name: str) -> bool:
    if not (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name)):
        return False
    return expr.func.id == "list" and _sole_enum_arg(expr.args, enum_name)


def _sole_enum_arg(args: list[ast.expr], enum_name: str) -> bool:
    return len(args) == 1 and isinstance(args[0], ast.Name) and args[0].id == enum_name


def _message(enum_name: str) -> str:
    return (
        f"enum '{enum_name}' contracts on the built-in '.name' — the persisted value "
        "changes when the identifier is renamed; contract on an explicit value field "
        "('.value') instead"
    )


def summary(result: EnumNameContractsResult) -> str:
    """One-line summary printed after the violations."""
    if result.violations:
        return f"{len(result.violations)} enum name contract(s) in {result.checked} files"
    return f"{result.checked} files free of enum name contracts"


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py enum-name-contract",
        description=(
            "Flag built-in .name contracts inside enum bodies — the persisted value "
            "drifts when a constant is renamed."
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
