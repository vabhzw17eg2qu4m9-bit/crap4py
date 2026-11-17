"""``implicit-state`` subcommand: surfaces implicit finite state machines.

Port of the crap4dart ``implicit_state`` gate (34e2163080). Lifecycle logic
spread over boolean flag clusters, multi-mutator status fields and
conditions that branch on flag combinations behaves like a state machine
with unmodelled combination states: invalid states stay representable and
transitions are unauditable — the fix is an explicit model (an ``Enum``, a
sealed hierarchy of state classes). Four checks, resolved from the parsed
AST only, per class (nested classes are separate scans):

1. **Scattered lifecycle flag** — a single lifecycle-shaped ``bool`` field
   mutated from ``--min-mutators`` (default 2) distinct functions.
2. **Scattered status field** — an instance field named ``status``,
   ``state``, ``phase``, ``stage``, ``mode`` or ``lifecycle`` (any type)
   assigned from ``--min-mutators`` distinct functions.
3. **Boolean-cluster FSM** — ``--min-flags`` (default 3) assigned lifecycle
   flags of one class, at least one of them mutated from ``--min-mutators``
   distinct functions; one class-level finding.
4. **Flag-combination condition** — an ``if`` condition (each ``elif`` is
   its own ``If`` node) reading two or more declared lifecycle flags of the
   scanned class via ``self.flag``; reads through other objects
   (``other.is_ready``) do not count.

Python name-shape host-mapping: the Dart prefix ``is``/``has``/``was``/
``will``/``should``/``can`` + ``Capital`` maps to the snake_case prefixes
``is_``/``has_``/``was_``/``will_``/``should_``/``can_`` (``is_loading``,
``has_failed``) AND the camelCase shapes (``isLoading``, ``hasFailed``) are
accepted too, since Python code may carry them; the lifecycle keyword
suffixes (``Loaded``, ``Ready``, ``Failed``, …) map to ``_loaded``/
``_ready``/``_failed``/… plus the raw camelCase tails (``shard_loaded`` and
``shardLoaded``). Status names match exactly (lowercased).

Eligibility is deliberately syntactic: class-body annotated ``bool`` fields
(``is_done: bool``) or class-body assignments of ``True``/``False`` count —
``ClassVar``/``Final``-annotated fields never do; locals and parameters are
never fields. Mutation-site counting is by NAME shape: any ``X.attr = …``
(plain, annotated-with-value, or augmented assignment; any target object,
including bare-module-level shapes upstream counts too) whose attribute
name matches a declared field counts, attributed to the enclosing function
(closures attribute to their enclosing method, as upstream). Conditions
count ``self.attr`` reads — Python has no bare implicit-self field reads,
so the host-map of upstream's bare-identifier rule is exactly ``self.attr``;
``while``/``for``/ternary conditions are not visited (as upstream, if
statements only).

Upstream's cluster check hardcodes ``>= 2`` where its spec says the
``min_mutators`` knob; this port follows the spec (the knob), which agrees
with upstream at the defaults (3/2).

Default severity is ``warning``: findings are reported ``[WARN]`` but do
not fail the run; ``--severity error`` fails with exit 2, matching
upstream's per-gate severity config. ``--min-flags``/``--min-mutators``
stand in for upstream's per-gate config keys (unknown flags are usage
errors, exit 1). The gate is NOT ignorable: upstream defaults
``ignorable: false`` and this port has no opt-in config file, so ignore
markers do not apply.
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .analyzer import _relative_to_root
from .args import UsageErrorParser
from .constants import NO_FILES_MESSAGE
from .files import PathLike, expand_paths, find_source_files, is_test_file

#: Lifecycle-flag prefixes: snake_case (`is_loading`) and camelCase
#: (`isLoading`) spellings of Dart's `is/has/was/will/should/can + Capital`.
_CAMEL_PREFIX = re.compile(r"^(?:is|has|was|will|should|can)[A-Z]")
_SNAKE_PREFIXES = ("is_", "has_", "was_", "will_", "should_", "can_")

#: Lifecycle keyword suffixes in both spellings (`shard_loaded`/`shardLoaded`).
_LIFECYCLE_WORDS = (
    "loaded",
    "started",
    "stopped",
    "disposed",
    "pending",
    "ready",
    "done",
    "failed",
    "scheduled",
    "driving",
    "running",
    "closed",
    "opened",
    "initialized",
    "connected",
    "completed",
    "submitted",
    "finished",
    "canceled",
    "cancelled",
    "active",
    "busy",
    "alive",
    "mounted",
    "broken",
    "dirty",
    "locked",
    "expired",
    "cached",
)
_SNAKE_SUFFIXES = tuple(f"_{word}" for word in _LIFECYCLE_WORDS)
_CAMEL_SUFFIXES = tuple(word.capitalize() for word in _LIFECYCLE_WORDS)

#: Whole-name lifecycle stages: any type, exact lowercased match.
_STATUS_NAMES = frozenset({"status", "state", "phase", "stage", "mode", "lifecycle"})


def _is_state_flag(name: str) -> bool:
    """Whether a field name carries the lifecycle-flag shape."""
    return (
        name.startswith(_SNAKE_PREFIXES)
        or name.endswith(_SNAKE_SUFFIXES)
        or name.endswith(_CAMEL_SUFFIXES)
        or _CAMEL_PREFIX.match(name) is not None
    )


def _is_bool_annotation(annotation: ast.expr) -> bool:
    """Whether an annotation names plain ``bool`` (subscripts like
    ``ClassVar[bool]``/``Optional[bool]`` do not)."""
    return isinstance(annotation, ast.Name) and annotation.id == "bool"


def _is_classvar_or_final(annotation: ast.expr) -> bool:
    """Whether an annotation marks an immutable/class-level member
    (``ClassVar``/``Final``, bare or subscripted) — never a mutable flag."""
    if isinstance(annotation, ast.Name):
        return annotation.id in ("ClassVar", "Final")
    if isinstance(annotation, ast.Subscript):
        return _is_classvar_or_final(annotation.value)
    return False


@dataclass(frozen=True, slots=True)
class StateFinding:
    """A single implicit-state finding."""

    file: str
    line: int
    message: str


@dataclass(frozen=True, slots=True)
class ImplicitStateResult:
    """Violations found plus how many classes were scanned."""

    violations: list[StateFinding]
    classes: int


def _declared_field(stmt: ast.stmt) -> tuple[str, int, bool, bool] | None:
    """The ``(name, line, mutable, is_bool)`` classification of a direct
    class-body field declaration, or ``None`` for anything else.

    Annotated fields are eligible only when ``ClassVar``/``Final``-free
    (immutable members never count); plain assignments must be single-target
    ``Name`` binds — tuple targets and locals are not fields. ``is_bool`` is
    the bool-ness signal: a plain ``bool`` annotation, or a ``True``/``False``
    initializer.
    """
    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
        return (
            stmt.target.id,
            stmt.lineno,
            not _is_classvar_or_final(stmt.annotation),
            _is_bool_annotation(stmt.annotation),
        )
    if (
        isinstance(stmt, ast.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Name)
    ):
        value = stmt.value
        return (
            stmt.targets[0].id,
            stmt.lineno,
            True,
            isinstance(value, ast.Constant) and isinstance(value.value, bool),
        )
    return None


@dataclass
class _FieldSites:
    """A declared field's line and the set of functions assigning it."""

    line: int
    methods: set[str] = field(default_factory=set)


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py implicit-state [paths...]``.

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
    result = check_files(files, project_root, args.min_flags, args.min_mutators)
    errored = args.severity == "error"
    for violation in result.violations:
        prefix = "" if errored else "[WARN] "
        print(f"{prefix}{violation.file}:{violation.line}: {violation.message}")
    print(summary(result))
    return 2 if errored and result.violations else 0


def check_files(
    files: Iterable[PathLike],
    project_root: PathLike,
    min_flags: int = 3,
    min_mutators: int = 2,
) -> ImplicitStateResult:
    """Scan every non-test file's classes for implicit state machines."""
    violations: list[StateFinding] = []
    classes = 0
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
        findings = file_violations(tree, relative, min_flags, min_mutators)
        violations.extend(findings.violations)
        classes += findings.classes
    return ImplicitStateResult(violations, classes)


def file_violations(
    tree: ast.Module, relative: str, min_flags: int = 3, min_mutators: int = 2
) -> ImplicitStateResult:
    """All implicit-state findings of one parsed file (one scan per class)."""
    violations: list[StateFinding] = []
    classes = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            scanner = _Scanner(node, relative, min_flags, min_mutators)
            violations.extend(scanner.violations())
            classes += 1
    return ImplicitStateResult(violations, classes)


class _Scanner(ast.NodeVisitor):
    """One class scan: lifecycle flags, status fields, their mutation sites
    per function, and flag-combination conditions."""

    def __init__(self, cls: ast.ClassDef, file: str, min_flags: int, min_mutators: int) -> None:
        self._cls = cls
        self._file = file
        self._min_flags = min_flags
        self._min_mutators = min_mutators
        self._flags: dict[str, _FieldSites] = {}
        self._statuses: dict[str, _FieldSites] = {}
        self._funcs: list[str] = []
        self._combos: list[StateFinding] = []

    def violations(self) -> list[StateFinding]:
        """Visit the class body, then emit the per-field and cluster findings."""
        self.visit(self._cls)
        findings = list(self._combos)
        findings.extend(
            self._field_findings(self._flags, "lifecycle flag", "part of an implicit state machine")
        )
        findings.extend(
            self._field_findings(
                self._statuses, "status field", "implicit transitions; centralize them"
            )
        )
        assigned = [sites for sites in self._flags.values() if sites.methods]
        scattered = any(len(sites.methods) >= self._min_mutators for sites in assigned)
        if len(assigned) >= self._min_flags and scattered:
            findings.append(
                StateFinding(
                    self._file,
                    self._cls.lineno,
                    f"class holds {len(assigned)} lifecycle flags mutated "
                    "across methods — a boolean-cluster state machine; model it "
                    "with an enum/sealed state",
                )
            )
        return findings

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # Nested classes are scanned separately by file_violations, but their
        # field declarations and methods also sit in this scan's subtree (as
        # upstream's recursive visitor).
        self._collect_fields(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._funcs.append(node.name)
        self.generic_visit(node)
        self._funcs.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node: ast.Assign) -> None:
        self._register_targets(node.targets)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._register_targets([node.target])

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._register_targets([node.target])

    def visit_If(self, node: ast.If) -> None:
        names = {
            sub.attr
            for sub in ast.walk(node.test)
            if isinstance(sub, ast.Attribute)
            and isinstance(sub.value, ast.Name)
            and sub.value.id == "self"
            and sub.attr in self._flags
        }
        if len(names) >= 2:
            self._combos.append(
                StateFinding(
                    self._file,
                    node.lineno,
                    f"condition branches on {len(names)} lifecycle flags "
                    f"({', '.join(names)}) — an implicit state combination; "
                    "model the states explicitly",
                )
            )
        self.generic_visit(node)

    def _register_targets(self, targets: list[ast.expr]) -> None:
        """Count every assignment whose target attribute name matches a
        declared field, regardless of the target object (pure name shape)."""
        if not self._funcs:
            return
        member = self._funcs[0]
        for target in targets:
            for sub in ast.walk(target):
                if not isinstance(sub, ast.Attribute):
                    continue
                if sub.attr in self._flags:
                    self._flags[sub.attr].methods.add(member)
                if sub.attr in self._statuses:
                    self._statuses[sub.attr].methods.add(member)

    def _collect_fields(self, cls: ast.ClassDef) -> None:
        """Register the class's direct instance fields (locals and
        parameters never count; ``ClassVar``/``Final`` never count)."""
        for stmt in cls.body:
            declared = _declared_field(stmt)
            if declared is not None:
                self._register_field(*declared)

    def _register_field(self, name: str, line: int, mutable: bool, is_bool: bool) -> None:
        """Classify one declared field: a mutable ``bool`` with the
        lifecycle-flag shape becomes a flag; any mutable status-named field
        becomes a status field (any type)."""
        if mutable and is_bool and _is_state_flag(name):
            self._flags[name] = _FieldSites(line)
        elif mutable and name.lower() in _STATUS_NAMES:
            self._statuses[name] = _FieldSites(line)

    def _field_findings(
        self, fields: dict[str, _FieldSites], kind: str, tail: str
    ) -> list[StateFinding]:
        return [
            StateFinding(
                self._file,
                sites.line,
                f"{kind} '{name}' is mutated from {len(sites.methods)} places — {tail}",
            )
            for name, sites in fields.items()
            if len(sites.methods) >= self._min_mutators
        ]


def summary(result: ImplicitStateResult) -> str:
    """One-line summary printed after the violations."""
    if not result.violations:
        return f"{result.classes} classes scanned, no implicit state machines"
    return (
        f"{len(result.violations)} implicit state machine findings "
        f"({result.classes} classes scanned)"
    )


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py implicit-state",
        description=(
            "Surface implicit finite state machines: boolean-cluster FSMs "
            "(lifecycle flags mutated across methods), lifecycle/status fields "
            "mutated from many places, and if-conditions branching on flag "
            "combinations — candidates for an explicit enum/sealed-state model."
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
    parser.add_argument(
        "--min-flags",
        type=int,
        default=3,
        help="minimum assigned lifecycle flags before a class is reported (default: 3)",
    )
    parser.add_argument(
        "--min-mutators",
        type=int,
        default=2,
        help="minimum distinct functions mutating one flag/status field (default: 2)",
    )
    return parser
