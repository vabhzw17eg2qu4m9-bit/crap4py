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

A digest-incremental cache (``.crap-mutation-cache.json``) replays killed
mutants of methods whose source-span digest is unchanged (survivors always
re-execute; a tool/operator/test_args change wipes the cache), and an
end-of-run integrity check re-verifies every mutated file against its
pre-run snapshot, re-restoring and reporting any out-of-band drift.

Diff-scoped execution (port of upstream 8228585): under ``--diff-base
<ref>`` the gate intersects candidate sites with the changed lines BEFORE
executing — a site runs only when its containing method intersects the
diff (the site's own line when it sits outside every method), so per-PR
runs pay only for the mutants of the changed methods. ``--scope-tests``
narrow each mutant's invocation to the derived test file of the mutated
file (``tests/test_<module>.py``), falling back to ``--test-args`` when no
candidate exists: a scoped kill is monotone (the scoped invocation is a
subset of the full suite) and is cached as a regular kill; a scoped
survivor — a timeout included — is re-confirmed once against the full
invocation, and only the confirmed outcome is reported and cached.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tokenize
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from . import __version__
from .args import UsageErrorParser
from .complexity import extract_methods
from .constants import DEFAULT_COVERAGE
from .coverage import FileCoverage, parse_coverage_dict
from .files import PathLike, expand_paths, find_source_files, parse_file
from .ignore_filter import filter_ignored

MIN_SCORE = 0.8
MAX_SURVIVORS = 0
TIMEOUT_SECONDS = 60
MAX_MUTANTS = 500

#: Digest-incremental cache file, written under the project root.
MUTATION_CACHE_FILE = ".crap-mutation-cache.json"

#: Cache envelope version; a mismatch discards the file.
MUTATION_CACHE_VERSION = 1

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

#: Per-mutant test runner: ``(project_root, timeout_seconds, test_args) -> outcome``.
#: ``test_args`` are the resolved per-site invocation arguments (test scoping).
MutantRunner = Callable[[str, int, Iterable[str]], str]

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


def fnv1a64_hex(text: str) -> str:
    """FNV-1a over 64-bit state, output-masked to 63 bits, as 16 hex chars.

    Zero dependencies, stable across platforms and runs (upstream parity:
    the mask keeps the digest a positive 64-bit-safe integer).
    """
    digest = 0xCBF29CE484222325
    for byte in text.encode():
        digest = ((digest ^ byte) * 0x1000001B3) & 0x7FFFFFFFFFFFFFFF
    return f"{digest:016x}"


@dataclass(frozen=True, slots=True)
class MethodRef:
    """Cache identity of one method: key, source-span digest, start offset.

    Site offsets are stored relative to ``start``, so an edit in an earlier
    method of the same file invalidates only that method's sites.
    """

    key: str
    digest: str
    start: int


@dataclass(frozen=True, slots=True)
class MutationCacheMeta:
    """Mutation-relevant config fingerprint stored in the cache envelope.

    Any mismatch wipes the cache (full re-execution) because cached
    outcomes were produced under different semantics.
    """

    tool: str

    operators: str

    test_args: str

    @classmethod
    def current(cls, operators: Iterable[str], test_args: Iterable[str]) -> MutationCacheMeta:
        return cls(
            tool=f"crap4py {__version__}",
            operators=",".join(sorted(operators)),
            test_args=",".join(test_args),
        )

    @classmethod
    def from_dict(cls, data: dict) -> MutationCacheMeta:
        return cls(
            tool=data.get("tool") or "",
            operators=data.get("operators") or "",
            test_args=data.get("test_args") or "",
        )

    def to_dict(self) -> dict:
        return {"tool": self.tool, "operators": self.operators, "test_args": self.test_args}


@dataclass(frozen=True, slots=True)
class CachedOutcome:
    """One cached per-site outcome; ``offset`` is relative to the method start."""

    offset: int

    line: int

    category: str

    original: str

    mutant: str

    status: str

    @classmethod
    def from_dict(cls, data: dict) -> CachedOutcome:
        # Malformed fields fall back to values that can never replay (offset -1).
        return cls(
            offset=data.get("offset", -1),
            line=data.get("line", 0),
            category=data.get("category", ""),
            original=data.get("original", ""),
            mutant=data.get("mutant", ""),
            status=data.get("status", ""),
        )

    def matches(self, offset: int, category: str, original: str, mutant: str) -> bool:
        """Whether this outcome sits at the given site (same span and replacement)."""
        return (
            self.offset == offset
            and self.category == category
            and self.original == original
            and self.mutant == mutant
        )

    def to_dict(self) -> dict:
        return {
            "offset": self.offset,
            "line": self.line,
            "category": self.category,
            "original": self.original,
            "mutant": self.mutant,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class CachedMethod:
    """Cached outcomes of one method, keyed by :meth:`MutationCache.methods_key`."""

    digest: str
    outcomes: tuple[CachedOutcome, ...]


class MutationCache:
    """Digest-incremental killed-stays-killed cache.

    Per-method outcomes keyed by source-text digest: only ``killed``
    outcomes are replayable; survivors are always re-executed.
    """

    def __init__(
        self, meta: MutationCacheMeta, methods: dict[str, CachedMethod] | None = None
    ) -> None:
        self.meta = meta
        self.methods: dict[str, CachedMethod] = methods if methods is not None else {}

    @staticmethod
    def methods_key(rel_path: str, method_name: str) -> str:
        """Stable cache key: relative file path plus the qualified method name."""
        return f"{rel_path}#{method_name}"

    @classmethod
    def load(cls, path: Path, meta: MutationCacheMeta) -> MutationCache:
        """Load the cache at ``path`` under ``meta``.

        A missing, corrupt, outdated or meta-mismatched file yields an
        empty cache — a full re-execution under the current semantics.
        """
        try:
            decoded = json.loads(path.read_text())
        except (OSError, ValueError):
            return cls(meta)
        if not isinstance(decoded, dict) or decoded.get("version") != MUTATION_CACHE_VERSION:
            return cls(meta)
        stored = MutationCacheMeta.from_dict(decoded.get("meta") or {})
        if stored != meta:
            return cls(meta)
        return cls(meta, _read_methods(decoded.get("methods")))

    def replay_kill(self, ref: MethodRef, site: MutationSite) -> CachedOutcome | None:
        """The cached killed outcome matching ``site``, or ``None`` (must execute).

        ``None`` means changed method, unknown site, or a survivor. Stored
        offsets are method-relative, so ``ref`` supplies the base.
        """
        method = self.methods.get(ref.key)
        if method is None or method.digest != ref.digest:
            return None
        base = site.start - ref.start
        for outcome in method.outcomes:
            if outcome.status == Killed and outcome.matches(
                base, site.category, site.original, site.mutant
            ):
                return outcome
        return None

    def merge_run(
        self, fresh: dict[str, tuple[str, list[CachedOutcome]]], live_keys: set[str]
    ) -> None:
        """Merge one run's outcomes: prune vanished methods, keep unsuperseded old ones."""
        self.methods = {k: v for k, v in self.methods.items() if k in live_keys}
        for key, (digest, outcomes) in fresh.items():
            old = self.methods.get(key)
            kept = [] if old is None else [o for o in old.outcomes if not _superseded(o, outcomes)]
            self.methods[key] = CachedMethod(digest, tuple(outcomes) + tuple(kept))

    def to_dict(self) -> dict:
        return {
            "version": MUTATION_CACHE_VERSION,
            "meta": self.meta.to_dict(),
            "methods": {
                key: {
                    "digest": method.digest,
                    "outcomes": [outcome.to_dict() for outcome in method.outcomes],
                }
                for key, method in self.methods.items()
            },
        }

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2))


def _read_methods(raw: object) -> dict[str, CachedMethod]:
    if not isinstance(raw, dict):
        return {}
    methods: dict[str, CachedMethod] = {}
    for key, entry in raw.items():
        if isinstance(key, str) and isinstance(entry, dict):
            outcomes = entry.get("outcomes")
            methods[key] = CachedMethod(
                entry.get("digest", ""),
                tuple(
                    CachedOutcome.from_dict(outcome)
                    for outcome in (outcomes if isinstance(outcomes, list) else ())
                    if isinstance(outcome, dict)
                ),
            )
    return methods


def _superseded(old: CachedOutcome, fresh: list[CachedOutcome]) -> bool:
    return any(
        fresh_outcome.matches(old.offset, old.category, old.original, old.mutant)
        for fresh_outcome in fresh
    )


class CacheSession(NamedTuple):
    """Everything the engine needs to consult and update the on-disk cache."""

    cache: MutationCache
    cache_file: Path
    #: Cache identity of every site inside an extracted method; sites
    #: outside methods are never cached.
    site_methods: dict[MutationSite, MethodRef]
    #: Keys of every method in the scanned scope — used for pruning.
    live_keys: set[str]


class MutantRecord(NamedTuple):
    """One site's outcome and whether the kill was replayed from the cache."""

    site: MutationSite
    outcome: str
    from_cache: bool


class MutationRun(NamedTuple):
    """Result of one engine run: records, integrity paths, scoping counters.

    ``scoped`` counts the sites whose scoped first run did not kill (they
    owed a confirmation against the full invocation); ``confirmed`` counts
    those confirmations that killed the mutant.
    """

    records: list[MutantRecord]
    integrity: list[str]
    scoped: int
    confirmed: int


class MutationScan(NamedTuple):
    """Covered sites for one run plus the cache identities bound to them."""

    sites: list[MutationSite]
    uncovered: int
    site_methods: dict[MutationSite, MethodRef]
    live_keys: set[str]


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

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.original} -> {self.mutant} ({self.category})"

    def apply_to(self, content: str) -> str:
        return content[: self.start] + self.mutant + content[self.end :]


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


def _line_starts(text: str) -> list[int]:
    """Char offset where each 1-based line starts, plus an end sentinel."""
    starts, off = [0], 0
    for line in text.splitlines(keepends=True):
        off += len(line)
        starts.append(off)
    return starts


def _source(text: str) -> _Source:
    tokens = [
        t
        for t in tokenize.generate_tokens(io.StringIO(text).readline)
        if t.type not in _TOKENIZE_NOISE
    ]
    return _Source(text, _line_starts(text), tokens)


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


def default_test_command(project_root: str, test_args: Iterable[str] = ()) -> list[str]:
    """The per-mutant test command: pytest when importable, unittest otherwise.

    ``test_args`` scopes the run (e.g. ``["tests/test_analyzer.py"]``);
    the default runs the whole suite. Scoping is the caller's
    responsibility: a mutant is killed only if the scoped suite would
    kill it. unittest discovery cannot take file paths, so scoped file
    arguments run as dotted modules (``tests/test_a.py`` ->
    ``python -m unittest tests.test_a``).
    """
    if importlib.util.find_spec("pytest") is not None:
        return [sys.executable, "-m", "pytest", *test_args]
    if test_args:
        modules = [arg[: -len(".py")].replace("/", ".") for arg in test_args if arg.endswith(".py")]
        if modules:
            return [sys.executable, "-m", "unittest", *modules]
    base = "tests" if (Path(project_root) / "tests").is_dir() else "test"
    return [sys.executable, "-m", "unittest", "discover", "-s", base, *test_args]


def run_mutant_tests(
    project_root: str, timeout_seconds: int, test_args: Iterable[str] = (), spawn=None
) -> str:
    """Run the project's test suite once, killing it when it outlives the timeout.

    A non-zero exit kills the mutant (import/compile errors included); a
    clean pass means the mutant survived; a timeout counts as survived.
    """
    spawn = spawn or subprocess.Popen
    cmd = default_test_command(project_root, test_args)
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


#: Unified-diff hunk header: ``@@ -l,s +l,s @@`` — new start and line count.
_HUNK_RE = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _added_lines_in_hunk(header: str) -> tuple[int, int] | None:
    """(new start, line count) of a hunk header, or None when unparseable."""
    match = _HUNK_RE.match(header)
    if match is None:
        return None
    count = 1 if match.group(2) is None else int(match.group(2))
    return int(match.group(1)), count


def parse_changed_lines(diff_output: str) -> dict[str, set[int]]:
    """Parse ``git diff --unified=0`` output into new-path -> added lines.

    Keys are the new-side project-relative paths of non-deleted files; the
    values are the 1-based line numbers added or changed in the diff (the
    Python equivalent of upstream's ``DiffLineMap.addedLines``). A file
    with only deletions maps to an empty set; deleted files are absent.
    """
    added: dict[str, set[int]] = {}
    current: str | None = None
    for line in diff_output.splitlines():
        if line.startswith("+++ "):
            header = line[4:].strip()
            current = None if header == "/dev/null" else header.removeprefix("b/")
        elif line.startswith("@@") and current is not None:
            hunk = _added_lines_in_hunk(line)
            if hunk is not None:
                start, count = hunk
                added.setdefault(current, set()).update(range(start, start + count))
    return added


def load_changed_lines(project_root: Path, base: str) -> dict[str, set[int]] | None:
    """Changed lines vs ``base`` via fixed-argv ``git diff`` (no shell).

    Returns ``None`` on any git failure (unknown ref, not a repository) —
    the caller reports it and exits with a usage error, mirroring
    upstream's ``_loadDiff``.
    """
    proc = subprocess.run(
        ["git", "diff", "--unified=0", base, "--", "*.py"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(f"git diff failed: {proc.stderr.strip()}", file=sys.stderr)
        return None
    return parse_changed_lines(proc.stdout)


def derived_test_file(site_file: str) -> str:
    """The derived test file of a mutated file: ``tests/test_<module>.py``.

    The repo's layout convention (upstream derives ``test/…`` mirrors of
    ``lib/…``; this port mirrors the module stem into ``tests/``).
    """
    return f"tests/test_{Path(site_file).stem}.py"


def run_mutants(
    project_root: str,
    sites: list[MutationSite],
    timeout_seconds: int,
    runner: MutantRunner | None = None,
    caching: CacheSession | None = None,
    test_args_for: Callable[[MutationSite], list[str]] | None = None,
) -> MutationRun:
    """Mutate every site one at a time, run the tests, restore the file.

    With ``caching``, killed mutants of digest-unchanged methods replay
    from the cache instead of executing; survivors always re-execute, and
    the merged cache is persisted after the run. With ``test_args_for``
    (test scoping), each site's first run uses the resolved per-site
    invocation — a SUBSET of the full suite, so a scoped kill is monotone
    and cached as a regular kill, while a scoped survivor (timeouts
    included) is not: it is re-confirmed once against the full invocation
    and only the confirmed outcome is reported and cached. Returns the
    per-site records, the integrity violations (files the end-of-run
    check had to re-restore — empty when the tree healed trivially) and
    the scoping counters.
    """
    runner = runner or run_mutant_tests
    root = Path(project_root)
    _clear_pycache(root)
    originals = _snapshot_originals(root, sites)
    fresh: dict[str, tuple[str, list[CachedOutcome]]] = {}
    records: list[MutantRecord] = []
    deferred: list[int] = []
    try:
        _execute_all(
            root, sites, timeout_seconds, runner, caching, test_args_for, records, fresh, deferred
        )
        confirmed = _confirm_scoped(
            root, records, deferred, timeout_seconds, runner, caching, fresh
        )
    finally:
        # End-of-run integrity check: restore any file that does not match
        # its snapshot, even when the loop died mid-mutant. A per-site
        # finally-restore can be defeated out-of-process (external write,
        # killed interpreter); silent tree corruption must become a loud,
        # self-healing error instead.
        violations = _verify_integrity(originals)
    _persist(caching, fresh)
    return MutationRun(records, violations, len(deferred), confirmed)


def _execute_all(
    root: Path,
    sites: list[MutationSite],
    timeout_seconds: int,
    runner: MutantRunner,
    caching: CacheSession | None,
    test_args_for: Callable[[MutationSite], list[str]] | None,
    records: list[MutantRecord],
    fresh: dict[str, tuple[str, list[CachedOutcome]]],
    deferred: list[int],
) -> None:
    """Run every site once: cache replays, scoped or full executions.

    Scoped survivors are deferred (their record indices land in
    ``deferred``) for :func:`_confirm_scoped`; everything else merges
    into ``fresh``.
    """
    for site in sites:
        replayed = _replay(caching, site)
        if replayed is not None:
            _accumulate(fresh, caching.site_methods[site], replayed)
            records.append(MutantRecord(site, Killed, True))
            continue
        scoped = test_args_for(site) if test_args_for is not None else None
        outcome = _execute_site(root, site, timeout_seconds, runner, scoped or ())
        records.append(MutantRecord(site, outcome, False))
        if scoped is not None and outcome != Killed:
            # Scoped survival is unverified: the scoped run may omit
            # the killing test. Confirm before caching or reporting.
            deferred.append(len(records) - 1)
        else:
            _fresh_outcome(caching, fresh, site, outcome)


def _replay(caching: CacheSession | None, site: MutationSite) -> CachedOutcome | None:
    """The cached kill for ``site``, or None (also None without a session)."""
    if caching is None:
        return None
    ref = caching.site_methods.get(site)
    return caching.cache.replay_kill(ref, site) if ref is not None else None


def _fresh_outcome(
    caching: CacheSession | None,
    fresh: dict[str, tuple[str, list[CachedOutcome]]],
    site: MutationSite,
    outcome: str,
) -> None:
    """Merge a fresh outcome into ``fresh`` (cache-bound sites only)."""
    ref = caching.site_methods.get(site) if caching else None
    if ref is not None:
        _accumulate(fresh, ref, _cached_outcome(site, outcome, ref))


def _confirm_scoped(
    root: Path,
    records: list[MutantRecord],
    deferred: list[int],
    timeout_seconds: int,
    runner: MutantRunner,
    caching: CacheSession | None,
    fresh: dict[str, tuple[str, list[CachedOutcome]]],
) -> int:
    """Re-run every deferred scoped survivor against the full invocation.

    Overwrites the deferred record with the confirmed outcome (and caches
    it); returns how many confirmations killed the mutant.
    """
    confirmed = 0
    for index in deferred:
        record = records[index]
        outcome = _execute_site(root, record.site, timeout_seconds, runner, ())
        records[index] = MutantRecord(record.site, outcome, False)
        if outcome == Killed:
            confirmed += 1
        _fresh_outcome(caching, fresh, record.site, outcome)
    return confirmed


def _execute_site(
    root: Path,
    site: MutationSite,
    timeout_seconds: int,
    runner: MutantRunner,
    test_args: Iterable[str] = (),
) -> str:
    path = root / site.file
    original = path.read_text()
    path.write_text(site.apply_to(original))
    try:
        return runner(str(root), timeout_seconds, test_args)
    finally:
        # Always restore the file, also after timeouts and errors.
        path.write_text(original)


def _snapshot_originals(root: Path, sites: list[MutationSite]) -> dict[Path, str]:
    """The pre-run content of every file ``sites`` touch."""
    originals: dict[Path, str] = {}
    for site in sites:
        path = root / site.file
        if path not in originals:
            originals[path] = path.read_text()
    return originals


def _verify_integrity(originals: dict[Path, str]) -> list[str]:
    """Re-restores any file that drifted from its snapshot; returns those paths.

    Empty when every file is byte-identical to the snapshot (the healthy case).
    """
    violations = []
    for path, original in originals.items():
        if path.read_text() != original:
            path.write_text(original)
            violations.append(str(path))
    return violations


def _accumulate(
    fresh: dict[str, tuple[str, list[CachedOutcome]]], ref: MethodRef, outcome: CachedOutcome
) -> None:
    digest, outcomes = fresh.get(ref.key, (ref.digest, []))
    outcomes.append(outcome)
    fresh[ref.key] = (digest, outcomes)


def _cached_outcome(site: MutationSite, outcome: str, ref: MethodRef) -> CachedOutcome:
    """Site outcome with the offset made method-relative (stable across edits elsewhere)."""
    return CachedOutcome(
        offset=site.start - ref.start,
        line=site.line,
        category=site.category,
        original=site.original,
        mutant=site.mutant,
        status=outcome,
    )


def _persist(
    caching: CacheSession | None, fresh: dict[str, tuple[str, list[CachedOutcome]]]
) -> None:
    if caching is None:
        return
    caching.cache.merge_run(fresh, caching.live_keys)
    caching.cache.save(caching.cache_file)


def _covered_sites(
    files: list[Path],
    root: Path,
    coverage: dict[str, FileCoverage],
    max_mutants: int,
    categories: Iterable[str],
    diff: dict[str, set[int]] | None = None,
) -> MutationScan:
    """Detect sites in every file and keep the covered ones, capped at
    ``max_mutants`` (file order, then offset — the cap is deterministic).

    In a diff run only sites whose containing method intersects the
    changed lines are kept at all: everything outside the diff is not a
    mutant of this change and is neither executed nor counted. Every
    file's methods become live cache keys (the cap only limits executed
    sites), and each kept site inside a function is bound to that
    function's cache identity.
    """
    covered: list[MutationSite] = []
    site_methods: dict[MutationSite, MethodRef] = {}
    live_keys: set[str] = set()
    uncovered = 0
    for path in files:
        found = _scan_file(
            path, root, coverage, max_mutants - len(covered), categories, live_keys, diff
        )
        covered.extend(found.sites)
        site_methods.update(found.site_methods)
        uncovered += found.uncovered
    return MutationScan(covered, uncovered, site_methods, live_keys)


def _in_diff(diff: dict[str, set[int]] | None, rel: str, site: MutationSite, methods: list) -> bool:
    """Whether the site's containing method intersects the changed lines.

    Falls back to the site's own line when it sits outside every method
    (module-level code). ``None`` (full run) intersects everything.
    """
    if diff is None:
        return True
    changed = diff.get(rel, ())
    if not changed:
        return False
    method = _containing(methods, site.line)
    if method is not None:
        return any(method.start_line <= line <= method.end_line for line in changed)
    return site.line in changed


def _scan_file(
    path: Path,
    root: Path,
    coverage: dict[str, FileCoverage],
    budget: int,
    categories: Iterable[str],
    live_keys: set[str],
    diff: dict[str, set[int]] | None = None,
) -> MutationScan:
    """The covered sites (up to ``budget``) of one file, each bound to its method.

    Every method of the file becomes a live cache key regardless of the
    budget — the cap only limits executed sites, never cache pruning.
    """
    rel = _rel(path, root)
    tree = parse_file(path)
    if tree is None:
        return MutationScan([], 0, {}, set())
    text = path.read_text()
    methods = extract_methods(text, rel)
    live_keys.update(MutationCache.methods_key(rel, method.name) for method in methods)
    return _kept_sites(rel, text, tree, methods, coverage.get(rel), budget, categories, diff)


def _kept_sites(
    rel: str,
    text: str,
    tree: ast.Module,
    methods: list,
    file_coverage: FileCoverage | None,
    budget: int,
    categories: Iterable[str],
    diff: dict[str, set[int]] | None,
) -> MutationScan:
    """The kept sites of one parsed file: diff-intersecting and covered.

    Diff-excluded sites come first: in a diff run everything outside the
    changed lines is not a mutant of this change and is neither executed
    nor counted.
    """
    starts = _line_starts(text)
    sites: list[MutationSite] = []
    site_methods: dict[MutationSite, MethodRef] = {}
    uncovered = 0
    for site in detect_sites(tree, text, rel, categories):
        if not _in_diff(diff, rel, site, methods):
            continue
        if len(sites) >= budget:
            break
        if not _is_covered(site, file_coverage, methods):
            uncovered += 1
            continue
        sites.append(site)
        ref = _method_ref(rel, text, starts, methods, site)
        if ref is not None:
            site_methods[site] = ref
    return MutationScan(sites, uncovered, site_methods, set())


def _method_ref(
    rel: str, text: str, starts: list[int], methods: list, site: MutationSite
) -> MethodRef | None:
    """The cache identity of the method containing ``site``; None outside functions."""
    method = _containing(methods, site.line)
    if method is None:
        return None
    start = starts[method.start_line - 1]
    end = starts[method.end_line] if method.end_line < len(starts) else len(text)
    digest = fnv1a64_hex(text[start:end])
    return MethodRef(MutationCache.methods_key(rel, method.name), digest, start)


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
    use_cache: bool = True,
    test_args: Iterable[str] = (),
    diff: dict[str, set[int]] | None = None,
    scope_tests: bool = False,
) -> MutationResult:
    """Gate core: detect covered sites, mutate one at a time, report survivors."""
    if coverage is None:
        return MutationResult((), "skipped: no coverage data")
    if not _has_tests_dir(project_root):
        return MutationResult((), "skipped: no tests directory")
    root = Path(project_root)
    selected = _select(files, root, excludes)
    scan = _covered_sites(selected, root, coverage, max_mutants, tuple(operators), diff)
    if not scan.sites:
        return MutationResult((), _empty_summary(scan.uncovered))
    try:
        run = _run_scanned(
            root,
            scan,
            timeout_seconds,
            runner,
            tuple(operators),
            tuple(test_args),
            use_cache,
            scope_tests,
        )
    except MutationUnavailable as exc:
        return MutationResult((), f"skipped: {exc}")
    if run.integrity:
        print(
            f"crap4py mutation: working tree did not restore cleanly "
            f"({len(run.integrity)} file(s) re-restored: {', '.join(run.integrity)}). "
            "Treat this run as suspect and re-run.",
            file=sys.stderr,
        )
    return _verdict(
        run.records,
        scan.uncovered,
        min_score,
        max_survivors,
        run.integrity,
        run.scoped,
        run.confirmed,
    )


def _empty_summary(uncovered: int) -> str:
    return (
        "no mutation sites"
        if uncovered == 0
        else f"no covered mutation sites ({uncovered} uncovered)"
    )


def _run_scanned(
    root: Path,
    scan: MutationScan,
    timeout_seconds: int,
    runner: MutantRunner | None,
    operators: tuple[str, ...],
    test_args: tuple[str, ...],
    use_cache: bool,
    scope_tests: bool,
) -> MutationRun:
    """Open the cache (when enabled), execute every site; records + counters."""
    caching = _open_cache(root, operators, test_args, scan) if use_cache else None
    test_args_for = _scoped_args(root, test_args) if scope_tests else None
    return run_mutants(str(root), scan.sites, timeout_seconds, runner, caching, test_args_for)


def _scoped_args(root: Path, test_args: tuple[str, ...]) -> Callable[[MutationSite], list[str]]:
    """Per-site invocation resolver for ``scope_tests``.

    Narrows each mutant's run to the derived test file of the mutated
    file, falling back to the full ``test_args`` when no candidate exists
    (upstream coalesces the fallback, so a fallback survivor is
    re-confirmed against the very same full invocation).
    """

    def resolve(site: MutationSite) -> list[str]:
        candidate = derived_test_file(site.file)
        return [candidate] if (root / candidate).is_file() else list(test_args)

    return resolve


def _open_cache(
    root: Path,
    operators: tuple[str, ...],
    test_args: tuple[str, ...],
    scan: MutationScan,
) -> CacheSession:
    """Load the digest cache at the project root (empty on any mismatch), bound to the scan."""
    cache_file = root / MUTATION_CACHE_FILE
    cache = MutationCache.load(cache_file, MutationCacheMeta.current(operators, test_args))
    return CacheSession(cache, cache_file, scan.site_methods, scan.live_keys)


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


def _summary(
    records: list[MutantRecord],
    uncovered: int,
    killed: int,
    survived: int,
    score: float,
    integrity: tuple[str, ...],
    scoped: int,
    confirmed: int,
) -> str:
    """The one-line run summary: score, scoping counters, integrity note."""
    cached = sum(1 for record in records if record.from_cache)
    summary = (
        f"score {score * 100:.1f}% "
        f"({killed} killed, {survived} survived, {uncovered} uncovered, {cached} cached)"
    )
    if scoped > 0:
        summary = f"{summary}; test-scoped {scoped} ({confirmed} re-confirmed killed)"
    if integrity:
        summary = f"{summary}; INTEGRITY: re-restored after run: {', '.join(integrity)}"
    return summary


def _verdict(
    records: list[MutantRecord],
    uncovered: int,
    min_score: float,
    max_survivors: int,
    integrity: tuple[str, ...] = (),
    scoped: int = 0,
    confirmed: int = 0,
) -> MutationResult:
    """Score = killed / (killed + survived); one violation per survivor."""
    killed = sum(1 for record in records if record.outcome == Killed)
    survived = len(records) - killed
    score = killed / (killed + survived) if records else 1.0
    summary = _summary(records, uncovered, killed, survived, score, integrity, scoped, confirmed)
    if score >= min_score and survived <= max_survivors:
        return MutationResult((), summary)
    return MutationResult(
        _survivor_violations(records), f"{summary}; {_survivors_by_category(records)}"
    )


def _survivor_violations(records: list[MutantRecord]) -> tuple[MutationViolation, ...]:
    return tuple(
        MutationViolation(
            file=record.site.file,
            line=record.site.line,
            message=_survivor_message(record.site),
        )
        for record in records
        if record.outcome != Killed
    )


def _survivor_message(site: MutationSite) -> str:
    method = f" in {site.method}" if site.method else ""
    return f"survived {site.category} mutant `{site.original}` -> `{site.mutant}`{method}"


def _survivors_by_category(records: list[MutantRecord]) -> str:
    counts: dict[str, int] = {}
    for record in records:
        if record.outcome != Killed:
            counts[record.site.category] = counts.get(record.site.category, 0) + 1
    detail = ", ".join(f"{name}={counts[name]}" for name in sorted(counts))
    return f"survivors by category: {detail}"


def _load_coverage(args: argparse.Namespace, project_root: Path) -> dict[str, FileCoverage] | None:
    """The parsed coverage report, or ``None`` when the file is missing (gate skips)."""
    coverage_path = Path(args.coverage)
    if not coverage_path.is_absolute():
        coverage_path = project_root / coverage_path
    if not coverage_path.exists():
        return None
    return parse_coverage_dict(json.loads(coverage_path.read_text()), project_root)


def _diff_arg(
    args: argparse.Namespace, project_root: Path
) -> tuple[dict[str, set[int]] | None, bool]:
    """Changed lines for ``--diff-base``; ``(None, False)`` when absent.

    A git failure reports to stderr and returns ``(None, True)`` — a
    usage error, mirroring upstream's ``_loadDiff``.
    """
    if args.diff_base is None:
        return None, False
    diff = load_changed_lines(project_root, args.diff_base)
    return diff, diff is None


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py mutation [options] [paths...]``. Exit 2 iff violations."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if any(arg == "" for arg in args.test_args):
        parser.error("--test-args: arguments must be non-empty strings")
    diff, diff_failed = _diff_arg(args, project_root)
    if diff_failed:
        return 1
    files = (
        expand_paths(args.paths, project_root) if args.paths else find_source_files(project_root)
    )
    coverage = _load_coverage(args, project_root)
    if coverage is None:
        print(
            "skipped: no coverage data (generate coverage.json first, e.g. `crap4py --run-tests`)"
        )
        return 0
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
        use_cache=args.cache,
        test_args=args.test_args,
        diff=diff,
        scope_tests=args.scope_tests,
    )
    result = filter_ignored("mutation", result, project_root)
    return _print_result(result)


def _print_result(result: MutationResult) -> int:
    """Violations first, then the one-line summary. Exit 2 iff violations."""
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
        default=DEFAULT_COVERAGE,
        help=f"coverage.py JSON report, relative to the project root (default: {DEFAULT_COVERAGE})",
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
        "--cache",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "replay killed mutants of unchanged methods from "
            f"{MUTATION_CACHE_FILE} instead of re-executing them; a config or "
            "operator change wipes it (default: on)"
        ),
    )
    parser.add_argument(
        "--test-args",
        action="append",
        default=[],
        metavar="ARG",
        help=(
            "extra per-mutant test command argument, repeatable, e.g. "
            "--test-args tests/test_analyzer.py to scope the suite "
            "(default: the whole suite)"
        ),
    )
    parser.add_argument(
        "--scope-tests",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "narrow each mutant's run to the derived test file of the mutated "
            "file (tests/test_<module>.py); a scoped kill is cached, a scoped "
            "survivor is re-confirmed against the full suite (default: off)"
        ),
    )
    parser.add_argument(
        "--diff-base",
        default=None,
        metavar="REF",
        help=(
            "diff-scoped execution: only execute mutants of methods "
            "intersecting the lines changed vs this git ref (e.g. origin/main; "
            "`git diff --unified=0 REF -- '*.py'`)"
        ),
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
