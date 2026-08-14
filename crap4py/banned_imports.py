"""``banned-imports`` subcommand: enforces architectural import boundaries.

Port of the crap4dart ``banned_imports`` gate: rules of ``--from``/``--forbid``
glob pairs (zipped in CLI order, optional ``--message`` each) — every file
matching a rule's ``from`` glob must not import anything matching the rule's
``forbid`` glob, either by raw dotted name or by resolved project path.
"""

from __future__ import annotations

import ast
import fnmatch
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .analyzer import _relative_to_root
from .args import UsageErrorParser
from .files import PathLike, expand_paths, find_source_files, is_test_file, parse_file
from .imports import from_import_paths, package_root, resolve_dotted

_IMPORT_TYPES = (ast.Import, ast.ImportFrom)


@dataclass(frozen=True, slots=True)
class ImportRule:
    """One ``--from GLOB --forbid GLOB [--message MSG]`` triple."""

    from_glob: str
    forbid_glob: str
    message: str | None


@dataclass(frozen=True, slots=True)
class ImportViolation:
    """A single banned import finding."""

    file: str
    line: int
    target: str
    rule: ImportRule


@dataclass(frozen=True, slots=True)
class BannedResult:
    """Violations found plus how many files were checked."""

    violations: list[ImportViolation]
    checked: int


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py banned-imports [...]``. Exit 2 iff violations."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if len(args.from_globs) != len(args.forbid_globs):
        parser.error("--from and --forbid must appear in pairs")
    rules = _zip_rules(args.from_globs, args.forbid_globs, args.messages)
    if not rules:
        print("No banned-import rules given — nothing to enforce.")
        return 0
    files = (
        expand_paths(args.paths, project_root) if args.paths else find_source_files(project_root)
    )
    if not files:
        print("No Python files to check.")
        return 0
    result = check_files(files, project_root, rules)
    _print_violations(result.violations)
    print(summary(result))
    return 2 if result.violations else 0


def _print_violations(violations: list[ImportViolation]) -> None:
    for violation in violations:
        message = f"{violation.file}:{violation.line}: import '{violation.target}' is banned"
        if violation.rule.message:
            message += f" — {violation.rule.message}"
        print(message)


def check_files(
    files: Iterable[PathLike], project_root: PathLike, rules: Sequence[ImportRule]
) -> BannedResult:
    """Apply ``rules`` to the imports of every non-test file."""
    root = Path(project_root).resolve()
    pkg_root = package_root(root)
    violations: list[ImportViolation] = []
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
        violations += _file_violations(tree, p.resolve(), rel, rules, root, pkg_root)
    return BannedResult(violations, checked)


def _zip_rules(
    from_globs: list[str], forbid_globs: list[str], messages: list[str]
) -> list[ImportRule]:
    return [
        ImportRule(from_glob, forbid_glob, messages[i] if i < len(messages) else None)
        for i, (from_glob, forbid_glob) in enumerate(zip(from_globs, forbid_globs, strict=True))
    ]


def _file_violations(
    tree: ast.Module,
    importer: Path,
    rel: str,
    rules: Sequence[ImportRule],
    root: Path,
    pkg_root: Path,
) -> list[ImportViolation]:
    applicable = [rule for rule in rules if fnmatch.fnmatch(rel, rule.from_glob)]
    if not applicable:
        return []
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, _IMPORT_TYPES):
            continue
        violations += _directive_violations(node, importer, rel, applicable, root, pkg_root)
    return violations


def _directive_violations(
    node: ast.Import | ast.ImportFrom,
    importer: Path,
    rel: str,
    rules: Sequence[ImportRule],
    root: Path,
    pkg_root: Path,
) -> list[ImportViolation]:
    violations = []
    for target in _import_targets(node, importer, pkg_root, root):
        rule = _first_match(rules, target)
        if rule is not None:
            violations.append(ImportViolation(rel, node.lineno, target, rule))
    return violations


def _first_match(rules: Sequence[ImportRule], target: str) -> ImportRule | None:
    """The first rule whose forbid glob matches ``target``, or None."""
    for rule in rules:
        if fnmatch.fnmatch(target, rule.forbid_glob):
            return rule
    return None


def _import_targets(
    node: ast.Import | ast.ImportFrom, importer: Path, pkg_root: Path, root: Path
) -> list[str]:
    """Everything an import directive can be matched against: raw dotted names
    plus the project-relative paths of the project files it resolves to."""
    if isinstance(node, ast.Import):
        names = [alias.name for alias in node.names]
        paths = set()
        for alias in node.names:
            paths |= resolve_dotted(alias.name, pkg_root)
    else:
        names = [node.module] if node.module else [alias.name for alias in node.names]
        paths = from_import_paths(node, importer, pkg_root)
    return names + [_relative_to_root(p, root) for p in sorted(paths)]


def summary(result: BannedResult) -> str:
    """One-line summary printed after the violations."""
    if result.violations:
        return f"{len(result.violations)} banned imports in {result.checked} files"
    return f"no banned imports in {result.checked} files"


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py banned-imports",
        description="Enforce --from/--forbid architectural import boundaries.",
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument(
        "--from",
        dest="from_globs",
        action="append",
        default=[],
        metavar="GLOB",
        help="glob of files the rule applies to (repeatable)",
    )
    parser.add_argument(
        "--forbid",
        dest="forbid_globs",
        action="append",
        default=[],
        metavar="GLOB",
        help="glob an import must not match (repeatable, paired with --from)",
    )
    parser.add_argument(
        "--message",
        dest="messages",
        action="append",
        default=[],
        metavar="MSG",
        help="optional explanation appended to each violation of this rule",
    )
    parser.add_argument(
        "paths", nargs="*", help="explicit files or directories (default: normal selection)"
    )
    return parser
