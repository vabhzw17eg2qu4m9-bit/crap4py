"""``complexity`` subcommand: flags methods over the complexity limit.

The ``complexity`` gate: fails methods whose complexity exceeds the limit
(default 10). ``--metric`` selects the metric — ``cyclomatic`` (default) or
``cognitive`` (SonarSource-style, nesting-weighted). Port of the crap4dart
``complexity`` gate. The CRAP score of the ``analyze`` command always uses
cyclomatic complexity regardless of this setting.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .analyzer import _relative_to_root
from .args import UsageErrorParser
from .cognitive import cognitive_complexity
from .complexity import cyclomatic_complexity
from .constants import NO_FILES_MESSAGE
from .files import PathLike, expand_paths, find_source_files, is_test_file, parse_file
from .ignore_filter import filter_ignored

MAX_COMPLEXITY = 10

_FUNCTION_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)
_METRICS: dict[str, Callable[[ast.FunctionDef | ast.AsyncFunctionDef], int]] = {
    "cyclomatic": cyclomatic_complexity,
    "cognitive": cognitive_complexity,
}


@dataclass(frozen=True, slots=True)
class ComplexityViolation:
    """A single over-complex method finding."""

    file: str
    line: int
    function: str
    metric_name: str
    score: int
    max_complexity: int


@dataclass(frozen=True, slots=True)
class ComplexityResult:
    """Violations found plus how many methods were checked."""

    violations: list[ComplexityViolation]
    checked: int


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py complexity [paths...]``. Exit 2 iff violations."""
    args = _build_parser().parse_args(argv)
    files = (
        expand_paths(args.paths, project_root) if args.paths else find_source_files(project_root)
    )
    if not files:
        print(NO_FILES_MESSAGE)
        return 0
    result = check_files(files, project_root, args.metric, args.max)
    result = filter_ignored("complexity", result, project_root)
    for violation in result.violations:
        print(
            f"{violation.file}:{violation.line}: {violation.function} "
            f"{violation.metric_name}={violation.score} > max {violation.max_complexity}"
        )
    print(summary(result, args.metric))
    return 2 if result.violations else 0


def check_files(
    files: Iterable[PathLike],
    project_root: PathLike,
    metric: str = "cyclomatic",
    max_complexity: int = MAX_COMPLEXITY,
) -> ComplexityResult:
    """Check every method of every non-test file against the complexity limit."""
    root = Path(project_root)
    score_of = _METRICS[metric]
    metric_name = "CC" if metric == "cyclomatic" else metric
    violations: list[ComplexityViolation] = []
    checked = 0
    for file_path in files:
        p = Path(file_path)
        if is_test_file(p, root):
            continue
        tree = parse_file(p)
        if tree is None:
            continue
        rel = _relative_to_root(p, root)
        for name, line, score in _method_scores(tree, score_of):
            checked += 1
            if score > max_complexity:
                violations.append(
                    ComplexityViolation(rel, line, name, metric_name, score, max_complexity)
                )
    return ComplexityResult(violations, checked)


def summary(result: ComplexityResult, metric: str = "cyclomatic") -> str:
    """One-line summary printed after the violations."""
    if result.violations:
        return f"{len(result.violations)}/{result.checked} methods over their {metric} limit"
    return f"{result.checked} methods within their {metric} limit"


def _method_scores(
    tree: ast.Module, score_of: Callable[[ast.FunctionDef | ast.AsyncFunctionDef], int]
) -> Iterator[tuple[str, int, int]]:
    """Yield ``(qualified_name, lineno, score)`` for every function in ``tree``."""
    yield from _collect_scores(tree, None, score_of)


def _collect_scores(
    node: ast.AST,
    prefix: str | None,
    score_of: Callable[[ast.FunctionDef | ast.AsyncFunctionDef], int],
) -> Iterator[tuple[str, int, int]]:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            yield from _collect_scores(child, _qualified(prefix, child.name), score_of)
        elif isinstance(child, _FUNCTION_TYPES):
            yield _qualified(prefix, child.name), child.lineno, score_of(child)
            yield from _collect_scores(child, child.name, score_of)


def _qualified(prefix: str | None, name: str) -> str:
    return f"{prefix}.{name}" if prefix else name


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py complexity",
        description="Flag methods whose complexity exceeds the limit.",
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument(
        "--metric",
        choices=("cyclomatic", "cognitive"),
        default="cyclomatic",
        help="complexity metric: cyclomatic (default) or cognitive",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=MAX_COMPLEXITY,
        help=f"maximum allowed complexity (default: {MAX_COMPLEXITY})",
    )
    parser.add_argument(
        "paths", nargs="*", help="explicit files or directories (default: normal selection)"
    )
    return parser
