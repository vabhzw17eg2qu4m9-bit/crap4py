"""``mutation`` subcommand: mutation testing gate.

Port of the crap4dart ``mutation`` gate (upstream c865ec3, adapted from the
unclebob/mutator research conclusions). A declarative, category-tagged
operator table is matched on the parsed stdlib ``ast`` tree; replacement
spans come from AST node positions plus stdlib ``tokenize`` token offsets —
never regex. Covered sites mutate one at a time with a per-mutant re-run of
the project's test suite: a non-zero exit kills the mutant (import/compile
errors included, because the test command refuses to run), a clean pass
means the mutant survived, and a timeout counts as survived. Sites in
unexecuted code are excluded from the score. Expensive — the gate is an
explicit opt-in subcommand and never part of plain analysis.
"""

from __future__ import annotations

import ast
import fnmatch
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tokenize
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from .args import UsageErrorParser
from .complexity import extract_methods
from .coverage import FileCoverage, parse_coverage_dict
from .files import PathLike, expand_paths, find_source_files, parse_file

MIN_SCORE = 0.8
MAX_SURVIVORS = 0
TIMEOUT_SECONDS = 60
MAX_MUTANTS = 500

#: Operator categories of the ``mutation`` gate, in stable order (upstream's
#: ``mutationOperatorCategories``).
CATEGORIES = ("arithmetic", "boundary", "equality", "logical", "boolean", "constant", "unary")

#: The declarative operator table: replacing ``original`` with ``mutant``
#: produces one mutant, tagged ``category`` (upstream's ``mutationOperators``).
MUTATION_OPERATORS = (
    ("arithmetic", "+", "-"),
    ("arithmetic", "-", "+"),
    ("arithmetic", "*", "/"),
    ("arithmetic", "/", "*"),
    ("arithmetic", "/", "//"),
    ("boundary", ">", ">="),
    ("boundary", ">=", ">"),
    ("boundary", "<", "<="),
    ("boundary", "<=", "<"),
    ("boundary", ">", "<"),
    ("boundary", "<", ">"),
    ("boundary", ">=", "<="),
    ("boundary", "<=", ">="),
    ("equality", "==", "!="),
    ("equality", "!=", "=="),
    ("logical", "and", "or"),
    ("logical", "or", "and"),
    ("boolean", "True", "False"),
    ("boolean", "False", "True"),
    ("constant", "0", "1"),
    ("constant", "1", "0"),
    ("constant", "-1", "0"),
    ("unary", "not", ""),
)

_BINOP_LEXEMES = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.FloorDiv: "//"}
_COMPARE_LEXEMES = {
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Lt: "<",
    ast.LtE: "<=",
}
_LOGIC_LEXEMES = {ast.And: "and", ast.Or: "or"}
# Python's map of upstream's `!` prefix deletion.
_NOT_LEXEME = "not"

#: Outcome of running the test suite against a single mutant.
Killed = "killed"
Survived = "survived"
Timeout = "timeout"

#: Per-mutant test runner: ``(project_root, timeout_seconds) -> outcome``.
MutantRunner = Callable[[str, int], str]

_TOKENIZE_NOISE = frozenset(
    {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
    }
)


class MutationUnavailable(Exception):
    """The per-mutant test command could not be spawned at all."""


@dataclass(frozen=True, slots=True)
class MutationSite:
    """One place where one operator replacement applies.

    ``file`` is project-relative; ``start``/``end`` are absolute character
    offsets of the replaced span in the file content. ``method`` is the
    qualified name of the containing function, or ``None`` outside every
    function (module-level code).
    """

    file: str
    line: int
    method: str | None
    category: str
    original: str
    mutant: str
    start: int
    end: int

    def apply_to(self, content: str) -> str:
        return content[: self.start] + self.mutant + content[self.end :]

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.original} -> {self.mutant} ({self.category})"


@dataclass(frozen=True, slots=True)
class MutationViolation:
    """A single survived-mutant finding."""

    file: str
    line: int
    message: str


@dataclass(frozen=True, slots=True)
class MutationResult:
    """Violations plus the one-line summary printed after them."""

    violations: tuple[MutationViolation, ...]
    summary: str


class _Source(NamedTuple):
    """Parsed file text: source, line-start offsets, code tokens."""

    text: str
    starts: list[int]
    tokens: list[tokenize.TokenInfo]

    def at(self, row: int, col: int) -> int:
        return self.starts[row - 1] + col

    def start(self, node: ast.AST) -> int:
        return self.at(node.lineno, node.col_offset)

    def end(self, node: ast.AST) -> int:
        return self.at(node.end_lineno, node.end_col_offset)

    def gap_token(self, lo: int, hi: int) -> tokenize.TokenInfo | None:
        """The one code token starting in ``[lo, hi)`` — the operator itself."""
        for tok in self.tokens:
            if self.at(*tok.start) >= hi:
                return None
            if self.at(*tok.start) >= lo:
                return tok
        return None


def _source(text: str) -> _Source:
    tokens = [
        t
        for t in tokenize.generate_tokens(io.StringIO(text).readline)
        if t.type not in _TOKENIZE_NOISE
    ]
    starts, off = [0], 0
    for line in text.splitlines(keepends=True):
        off += len(line)
        starts.append(off)
    return _Source(text, starts, tokens)


def _mutants_for(lexeme: str, categories: Iterable[str]) -> list[tuple[str, str]]:
    enabled = set(categories)
    return [(c, m) for c, o, m in MUTATION_OPERATORS if o == lexeme and c in enabled]


def detect_sites(
    tree: ast.Module, text: str, rel_path: str, categories: Iterable[str] = CATEGORIES
) -> list[MutationSite]:
    """Every mutation site in ``text``, sorted by offset (file order upstream)."""
    src = _source(text)
    methods = extract_methods(text, rel_path)
    sites: list[MutationSite] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp):
            _binop_sites(src, node, rel_path, categories, methods, sites)
        elif isinstance(node, ast.Compare):
            _compare_sites(src, node, rel_path, categories, methods, sites)
        elif isinstance(node, ast.BoolOp):
            _boolop_sites(src, node, rel_path, categories, methods, sites)
        elif isinstance(node, ast.Constant):
            _constant_sites(src, node, rel_path, categories, methods, sites)
        elif isinstance(node, ast.UnaryOp):
            _unary_sites(src, node, rel_path, categories, methods, sites)
    sites.sort(key=lambda s: s.start)
    return sites


def _add(
    sites: list[MutationSite],
    src: _Source,
    rel_path: str,
    category: str,
    original: str,
    mutant: str,
    start: int,
    end: int,
    line: int,
    methods: list,
) -> None:
    sites.append(
        MutationSite(
            file=rel_path,
            line=line,
            method=_method_at(methods, line),
            category=category,
            original=original,
            mutant=mutant,
            start=start,
            end=end,
        )
    )


def _binop_sites(
    src: _Source,
    node: ast.BinOp,
    rel_path: str,
    categories: Iterable[str],
    methods: list,
    sites: list[MutationSite],
) -> None:
    lexeme = _BINOP_LEXEMES.get(type(node.op))
    if lexeme is None:
        return
    tok = src.gap_token(src.end(node.left), src.start(node.right))
    if tok is None or tok.string != lexeme:
        return
    start = src.at(*tok.start)
    for category, mutant in _mutants_for(lexeme, categories):
        _add(
            sites,
            src,
            rel_path,
            category,
            lexeme,
            mutant,
            start,
            src.at(*tok.end),
            tok.start[0],
            methods,
        )


def _compare_sites(
    src: _Source,
    node: ast.Compare,
    rel_path: str,
    categories: Iterable[str],
    methods: list,
    sites: list[MutationSite],
) -> None:
    for i, op in enumerate(node.ops):
        lexeme = _COMPARE_LEXEMES.get(type(op))
        if lexeme is None:
            continue
        left = node.left if i == 0 else node.comparators[i - 1]
        tok = src.gap_token(src.end(left), src.start(node.comparators[i]))
        if tok is None or tok.string != lexeme:
            continue
        start = src.at(*tok.start)
        for category, mutant in _mutants_for(lexeme, categories):
            _add(
                sites,
                src,
                rel_path,
                category,
                lexeme,
                mutant,
                start,
                src.at(*tok.end),
                tok.start[0],
                methods,
            )


def _boolop_sites(
    src: _Source,
    node: ast.BoolOp,
    rel_path: str,
    categories: Iterable[str],
    methods: list,
    sites: list[MutationSite],
) -> None:
    lexeme = _LOGIC_LEXEMES.get(type(node.op))
    if lexeme is None:
        return
    for i in range(len(node.values) - 1):
        tok = src.gap_token(src.end(node.values[i]), src.start(node.values[i + 1]))
        if tok is None or tok.string != lexeme:
            continue
        start = src.at(*tok.start)
        for category, mutant in _mutants_for(lexeme, categories):
            _add(
                sites,
                src,
                rel_path,
                category,
                lexeme,
                mutant,
                start,
                src.at(*tok.end),
                tok.start[0],
                methods,
            )


def _constant_sites(
    src: _Source,
    node: ast.Constant,
    rel_path: str,
    categories: Iterable[str],
    methods: list,
    sites: list[MutationSite],
) -> None:
    enabled = set(categories)
    if isinstance(node.value, bool):
        _bool_site(src, node, rel_path, "boolean" in enabled, methods, sites)
    elif isinstance(node.value, int):
        _int_site(src, node, rel_path, "constant" in enabled, methods, sites)


def _bool_site(
    src: _Source,
    node: ast.Constant,
    rel_path: str,
    enabled: bool,
    methods: list,
    sites: list[MutationSite],
) -> None:
    if not enabled:
        return
    original = src.text[src.start(node) : src.end(node)]
    mutant = "False" if node.value else "True"
    _add(
        sites,
        src,
        rel_path,
        "boolean",
        original,
        mutant,
        src.start(node),
        src.end(node),
        node.lineno,
        methods,
    )


def _int_site(
    src: _Source,
    node: ast.Constant,
    rel_path: str,
    enabled: bool,
    methods: list,
    sites: list[MutationSite],
) -> None:
    if not enabled or node.value not in (0, 1):
        return
    original = src.text[src.start(node) : src.end(node)]
    mutant = "1" if node.value == 0 else "0"
    _add(
        sites,
        src,
        rel_path,
        "constant",
        original,
        mutant,
        src.start(node),
        src.end(node),
        node.lineno,
        methods,
    )


def _unary_sites(
    src: _Source,
    node: ast.UnaryOp,
    rel_path: str,
    categories: Iterable[str],
    methods: list,
    sites: list[MutationSite],
) -> None:
    enabled = set(categories)
    if isinstance(node.op, ast.Not):
        _not_site(src, node, rel_path, "unary" in enabled, methods, sites)
    elif isinstance(node.op, ast.USub):
        _neg_one_site(src, node, rel_path, "constant" in enabled, methods, sites)


def _not_site(
    src: _Source,
    node: ast.UnaryOp,
    rel_path: str,
    enabled: bool,
    methods: list,
    sites: list[MutationSite],
) -> None:
    """Delete the `not` prefix — Python's map of upstream's `!` deletion."""
    if not enabled:
        return
    tok = src.gap_token(src.start(node), src.start(node.operand))
    if tok is None or tok.string != _NOT_LEXEME:
        return
    _add(
        sites,
        src,
        rel_path,
        "unary",
        _NOT_LEXEME,
        "",
        src.at(*tok.start),
        src.at(*tok.end),
        tok.start[0],
        methods,
    )


def _neg_one_site(
    src: _Source,
    node: ast.UnaryOp,
    rel_path: str,
    enabled: bool,
    methods: list,
    sites: list[MutationSite],
) -> None:
    """`-1` becomes `0` (the whole `-1` span is replaced)."""
    operand = node.operand
    if not enabled or not (
        isinstance(operand, ast.Constant) and type(operand.value) is int and operand.value == 1
    ):
        return
    _add(
        sites,
        src,
        rel_path,
        "constant",
        "-1",
        "0",
        src.start(node),
        src.end(node),
        node.lineno,
        methods,
    )


def _containing(methods: list, line: int) -> object | None:
    """The extracted method whose line range contains ``line``, or ``None``."""
    for method in methods:
        if method.start_line <= line <= method.end_line:
            return method
    return None


def _method_at(methods: list, line: int) -> str | None:
    """The qualified name of the function containing ``line``, or ``None``."""
    method = _containing(methods, line)
    return method.name if method else None


def _is_covered(site: MutationSite, coverage: FileCoverage | None, methods: list) -> bool:
    """Covered = containing method executed and the site line not missing.

    Sites outside every function fall back to raw line coverage.
    """
    if coverage is None:
        return False
    method = _containing(methods, site.line)
    if method is None:
        return site.line in coverage.executed_lines
    if not any(method.start_line <= line <= method.end_line for line in coverage.executed_lines):
        return False
    return site.line not in coverage.missing_lines


def default_test_command(project_root: str) -> list[str]:
    """The per-mutant test command: pytest when importable, unittest otherwise."""
    if importlib.util.find_spec("pytest") is not None:
        return [sys.executable, "-m", "pytest"]
    base = "tests" if (Path(project_root) / "tests").is_dir() else "test"
    return [sys.executable, "-m", "unittest", "discover", "-s", base]


def run_mutant_tests(project_root: str, timeout_seconds: int, spawn=None) -> str:
    """Run the project's test suite once, killing it when it outlives the timeout.

    A non-zero exit kills the mutant (import/compile errors included); a
    clean pass means the mutant survived; a timeout counts as survived.
    """
    spawn = spawn or subprocess.Popen
    cmd = default_test_command(project_root)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        proc = spawn(
            cmd, cwd=project_root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env
        )
    except OSError as exc:
        raise MutationUnavailable(f"test command not found: {cmd[0]}: {exc}") from exc
    try:
        code = proc.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        return Timeout
    return Survived if code == 0 else Killed


def _clear_pycache(root: Path) -> None:
    """Remove regenerable ``__pycache__`` dirs (venv/cache dirs pruned).

    CPython validates ``.pyc`` by source mtime (1-second granularity) and
    size — table mutants have the same size and run within the same second,
    so a stale cache would shadow one mutant with the previous mutant's
    bytecode (false kills). The engine clears caches once and runs the
    suite with ``PYTHONDONTWRITEBYTECODE=1`` so no fresh cache can lie.
    """
    skip = {".venv", "venv", "__pycache__", ".git", ".tox", "node_modules", "site-packages"}
    for base, dirs, _files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip]
        cache = Path(base) / "__pycache__"
        if cache.is_dir():
            shutil.rmtree(cache, ignore_errors=True)


def run_mutants(
    project_root: str,
    sites: list[MutationSite],
    timeout_seconds: int,
    runner: MutantRunner | None = None,
) -> list[tuple[MutationSite, str]]:
    """Mutate every site one at a time, run the tests, restore the file."""
    runner = runner or run_mutant_tests
    outcomes: list[tuple[MutationSite, str]] = []
    root = Path(project_root)
    _clear_pycache(root)
    for site in sites:
        path = root / site.file
        original = path.read_text()
        path.write_text(site.apply_to(original))
        try:
            outcome = runner(str(root), timeout_seconds)
        finally:
            # Always restore the file, also after timeouts and errors.
            path.write_text(original)
        outcomes.append((site, outcome))
    return outcomes


def _covered_sites(
    files: list[Path],
    root: Path,
    coverage: dict[str, FileCoverage],
    max_mutants: int,
    categories: Iterable[str],
) -> tuple[list[MutationSite], int]:
    """Detect sites in every file and keep the covered ones, capped at
    ``max_mutants`` (file order, then offset — the cap is deterministic)."""
    covered: list[MutationSite] = []
    uncovered = 0
    for path in files:
        if len(covered) >= max_mutants:
            break
        rel = _rel(path, root)
        tree = parse_file(path)
        if tree is None:
            continue
        text = path.read_text()
        methods = extract_methods(text, rel)
        file_coverage = coverage.get(rel)
        for site in detect_sites(tree, text, rel, categories):
            if len(covered) >= max_mutants:
                break
            if _is_covered(site, file_coverage, methods):
                covered.append(site)
            else:
                uncovered += 1
    return covered, uncovered


def scan_files(
    files: Iterable[PathLike],
    project_root: PathLike,
    coverage: dict[str, FileCoverage] | None,
    *,
    min_score: float = MIN_SCORE,
    max_survivors: int = MAX_SURVIVORS,
    timeout_seconds: int = TIMEOUT_SECONDS,
    max_mutants: int = MAX_MUTANTS,
    operators: Iterable[str] = CATEGORIES,
    excludes: Iterable[str] = (),
    runner: MutantRunner | None = None,
) -> MutationResult:
    """Gate core: detect covered sites, mutate one at a time, report survivors."""
    if coverage is None:
        return MutationResult((), "skipped: no coverage data")
    if not _has_tests_dir(project_root):
        return MutationResult((), "skipped: no tests directory")
    root = Path(project_root)
    selected = _select(files, root, excludes)
    sites, uncovered = _covered_sites(selected, root, coverage, max_mutants, tuple(operators))
    if not sites:
        summary = (
            "no mutation sites"
            if uncovered == 0
            else f"no covered mutation sites ({uncovered} uncovered)"
        )
        return MutationResult((), summary)
    try:
        outcomes = run_mutants(str(root), sites, timeout_seconds, runner)
    except MutationUnavailable as exc:
        return MutationResult((), f"skipped: {exc}")
    return _verdict(outcomes, uncovered, min_score, max_survivors)


def _select(files: Iterable[PathLike], root: Path, excludes: Iterable[str]) -> list[Path]:
    return [f for f in (Path(p) for p in files) if not _excluded(_rel(f, root), excludes)]


def _has_tests_dir(project_root: PathLike) -> bool:
    root = Path(project_root)
    return (root / "tests").is_dir() or (root / "test").is_dir()


def _excluded(rel: str, excludes: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(rel, pattern) for pattern in excludes)


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _verdict(
    outcomes: list[tuple[MutationSite, str]],
    uncovered: int,
    min_score: float,
    max_survivors: int,
) -> MutationResult:
    """Score = killed / (killed + survived); one violation per survivor."""
    killed = sum(1 for _, outcome in outcomes if outcome == Killed)
    survived = len(outcomes) - killed
    score = 1.0 if not outcomes else killed / (killed + survived)
    summary = (
        f"score {score * 100:.1f}% ({killed} killed, {survived} survived, {uncovered} uncovered)"
    )
    if score >= min_score and survived <= max_survivors:
        return MutationResult((), summary)
    violations = tuple(
        MutationViolation(
            file=site.file,
            line=site.line,
            message=_survivor_message(site),
        )
        for site, outcome in outcomes
        if outcome != Killed
    )
    return MutationResult(violations, f"{summary}; {_survivors_by_category(outcomes)}")


def _survivor_message(site: MutationSite) -> str:
    method = f" in {site.method}" if site.method else ""
    return f"survived {site.category} mutant `{site.original}` -> `{site.mutant}`{method}"


def _survivors_by_category(outcomes: list[tuple[MutationSite, str]]) -> str:
    counts: dict[str, int] = {}
    for site, outcome in outcomes:
        if outcome != Killed:
            counts[site.category] = counts.get(site.category, 0) + 1
    detail = ", ".join(f"{name}={counts[name]}" for name in sorted(counts))
    return f"survivors by category: {detail}"


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py mutation [options] [paths...]``. Exit 2 iff violations."""
    args = _build_parser().parse_args(argv)
    files = (
        expand_paths(args.paths, project_root) if args.paths else find_source_files(project_root)
    )
    coverage_path = Path(args.coverage)
    if not coverage_path.is_absolute():
        coverage_path = project_root / coverage_path
    if not coverage_path.exists():
        print(
            "skipped: no coverage data (generate coverage.json first, e.g. `crap4py --run-tests`)"
        )
        return 0
    coverage = parse_coverage_dict(json.loads(coverage_path.read_text()), project_root)
    result = scan_files(
        files,
        project_root,
        coverage,
        min_score=args.min_score,
        max_survivors=args.max_survivors,
        timeout_seconds=args.timeout_seconds,
        max_mutants=args.max_mutants,
        operators=args.operator or list(CATEGORIES),
        excludes=args.exclude,
    )
    for violation in result.violations:
        print(f"{violation.file}:{violation.line}: {violation.message}")
    print(result.summary)
    return 2 if result.violations else 0


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py mutation",
        description="Mutation testing: mutate one operator at a time, re-run tests per mutant.",
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument(
        "--coverage",
        default="coverage.json",
        help="coverage.py JSON report, relative to the project root (default: coverage.json)",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=MIN_SCORE,
        help=f"minimum mutation score, killed / (killed + survived) (default: {MIN_SCORE})",
    )
    parser.add_argument(
        "--max-survivors",
        type=int,
        default=MAX_SURVIVORS,
        help=f"maximum tolerated survived mutants (default: {MAX_SURVIVORS})",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=TIMEOUT_SECONDS,
        help=f"per-mutant test timeout; a timeout counts as survived (default: {TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--max-mutants",
        type=int,
        default=MAX_MUTANTS,
        help=f"performance cap on executed covered sites (default: {MAX_MUTANTS})",
    )
    parser.add_argument(
        "--operator",
        action="append",
        choices=CATEGORIES,
        default=[],
        metavar="CATEGORY",
        help="enable one operator category (repeatable; default: all)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="skip files whose project-relative path matches this fnmatch glob",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="explicit files or directories (default: normal selection)",
    )
    return parser
