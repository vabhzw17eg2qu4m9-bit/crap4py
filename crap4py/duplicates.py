"""``duplicates`` subcommand: token-based copy-paste duplication gate.

Tokenizes every scanned file (comments and whitespace artifacts dropped,
lexemes kept), then indexes sliding windows of ``--min-tokens`` lexemes
with a Rabin-Karp hash (mod 2**64), jscpd-style, across all files at
once. A window participates when it spans at least ``--min-lines``
source lines; any window whose hash occurs at least twice — within or
across files — marks its tokens duplicated. A file fails when its
distinct duplicated lines exceed ``--threshold`` percent of its lines.
Port of the crap4dart ``duplication`` gate including the dc64e9c
per-gate ``sources`` union (``--source``).

Two opt-in normalizations extend detection to renamed (Type-2) clones:
``--ignore-locals`` renames function-local identifiers to ``$L<n>``
placeholders in first-use order per outermost function scope, and
``--ignore-literals`` masks string/numeric literals as ``$STR``/``$NUM``.
Detection unions the raw-lexeme pass with the masked one, so exact
copies are never lost when scopes shift placeholder numbering.
"""

from __future__ import annotations

import ast
import bisect
import fnmatch
import io
import sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

from .analyzer import _relative_to_root
from .args import UsageErrorParser
from .files import PathLike, expand_paths, find_source_files

MIN_TOKENS = 50
MIN_LINES = 5
DEFAULT_THRESHOLD = 1.0
IGNORE_LOCALS = False
IGNORE_LITERALS = False

_BASE = 0x9E3779B97F4A7C15
_MASK = (1 << 64) - 1

# f-string token splits exist only on 3.12+; below that an f-string is one
# STRING token and indistinguishable from a plain string.
_FSTRING_START = getattr(tokenize, "FSTRING_START", None)
_FSTRING_END = getattr(tokenize, "FSTRING_END", None)
# Plain STRING plus (3.12+) per-part f-string tokens — all string-ish.
_STRING_TYPES = frozenset(
    t
    for t in (
        tokenize.STRING,
        _FSTRING_START,
        getattr(tokenize, "FSTRING_MIDDLE", None),
        _FSTRING_END,
    )
    if t
)
_SCOPE_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
_TYPE_PARAM_TYPES = tuple(
    t for t in (getattr(ast, name, None) for name in ("TypeVar", "ParamSpec", "TypeVarTuple")) if t
)

# Kept: NAME, NUMBER, STRING, OP, KEYWORD (f-strings tokenize per-part on
# 3.12+). Dropped: comments, logical/non-logical newlines, indentation
# markers, the encoding pseudo-token, EOF and error tokens — the Python
# map of upstream's "comments and synthetic tokens are ignored".
_SKIP_TYPES = frozenset(
    {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
        tokenize.ERRORTOKEN,
    }
)


@dataclass(frozen=True, slots=True)
class DuplicationViolation:
    """A file over the duplicated-lines threshold."""

    file: str
    line: int
    message: str


@dataclass(frozen=True, slots=True)
class DuplicationResult:
    """Violations plus the one-line summary printed after them."""

    violations: tuple[DuplicationViolation, ...]
    summary: str


@dataclass(slots=True)
class _Tok:
    """One token: raw lexeme, normalized value (equal when no masking
    applies), 1-based source line, and the duplicate flag."""

    lexeme: str
    value: str
    line: int
    duplicated: bool = False


@dataclass(slots=True)
class _FileTokens:
    """A tokenized file: token stream, project-relative path, line count."""

    rel: str
    tokens: list[_Tok]
    total_lines: int


@dataclass(slots=True)
class _Scope:
    """One outermost function scope: source range plus its declared local
    names and the `$L<n>` placeholders assigned in first-use order."""

    start: tuple[int, int]
    end: tuple[int, int]
    names: frozenset[str]
    placeholders: dict[str, str] = field(default_factory=dict)


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py duplicates [options] [paths...]``. Exit 2 iff violations."""
    args = _build_parser().parse_args(argv)
    files = (
        expand_paths(args.paths, project_root) if args.paths else find_source_files(project_root)
    )
    result = scan_files(
        files,
        project_root,
        min_tokens=args.min_tokens,
        min_lines=args.min_lines,
        threshold=args.threshold,
        excludes=args.exclude,
        sources=args.source,
        ignore_locals=args.ignore_locals,
        ignore_literals=args.ignore_literals,
    )
    for violation in result.violations:
        print(f"{violation.file}:{violation.line}: {violation.message}")
    print(result.summary)
    return 2 if result.violations else 0


def scan_files(
    files: list[PathLike],
    project_root: PathLike,
    *,
    min_tokens: int = MIN_TOKENS,
    min_lines: int = MIN_LINES,
    threshold: float = DEFAULT_THRESHOLD,
    excludes: list[str] | tuple[str, ...] = (),
    sources: list[str] | tuple[str, ...] = (),
    ignore_locals: bool = IGNORE_LOCALS,
    ignore_literals: bool = IGNORE_LITERALS,
) -> DuplicationResult:
    """Gate core: union ``--source`` paths into the scan set, tokenize,
    detect duplicates, and build violations plus the summary."""
    root = Path(project_root)
    paths = sorted({Path(f).resolve() for f in files} | set(_expand_sources(sources, root)))
    tokenized = _tokenized_files(paths, root, excludes, min_tokens, ignore_locals, ignore_literals)
    if not tokenized:
        return DuplicationResult((), "no files with enough tokens")
    _detect(tokenized, min_tokens, min_lines, ignore_locals)
    return _build_result(tokenized, threshold)


def _expand_sources(sources: list[str] | tuple[str, ...], root: Path) -> list[Path]:
    """``--source`` paths resolved against the project root: directories
    walk recursively for ``.py`` files, ``.py`` files are taken directly,
    missing paths are skipped silently (upstream dc64e9c)."""
    resolved: list[Path] = []
    for source in sources:
        p = Path(source)
        p = p if p.is_absolute() else root / p
        if p.is_file():
            if p.suffix == ".py":
                resolved.append(p.resolve())
        elif p.is_dir():
            resolved.extend(q.resolve() for q in sorted(p.rglob("*.py")))
    return resolved


def _tokenized_files(
    paths: list[Path],
    root: Path,
    excludes: list[str] | tuple[str, ...],
    min_tokens: int,
    ignore_locals: bool,
    ignore_literals: bool,
) -> list[_FileTokens]:
    """Load and tokenize the scan set; files matching an ``--exclude``
    glob (fnmatch on the project-relative path) or holding fewer than
    ``min_tokens`` tokens are skipped from the scan entirely."""
    tokenized = []
    for path in paths:
        rel = _relative_to_root(path, root)
        if any(fnmatch.fnmatch(rel, pattern) for pattern in excludes):
            continue
        loaded = _load(path, ignore_locals, ignore_literals)
        if loaded is None or len(loaded[1]) < min_tokens:
            continue
        source, tokens = loaded
        tokenized.append(_FileTokens(rel, tokens, _line_count(source)))
    return tokenized


def _load(
    path: Path,
    ignore_locals: bool = IGNORE_LOCALS,
    ignore_literals: bool = IGNORE_LITERALS,
) -> tuple[str, list[_Tok]] | None:
    """Read a file and keep one ``_Tok`` per non-skipped token: raw lexeme
    plus normalized value (equal unless masking applies)."""
    try:
        source = path.read_text(encoding="utf-8")
        raw = [
            t
            for t in tokenize.generate_tokens(io.StringIO(source).readline)
            if t.type not in _SKIP_TYPES
        ]
        tokens = _mask_tokens(raw, source, ignore_locals, ignore_literals)
    except (OSError, UnicodeDecodeError, SyntaxError, tokenize.TokenError) as exc:
        print(f"Warning: could not tokenize {path}: {exc}", file=sys.stderr)
        return None
    return source, tokens


def _mask_tokens(
    raw: list[tokenize.TokenInfo],
    source: str,
    ignore_locals: bool,
    ignore_literals: bool,
) -> list[_Tok]:
    """Normalized token stream: literal/interpolation placeholders and
    first-use-order local renames applied per enabled knob."""
    scopes = _scopes(source) if ignore_locals else []
    tokens = []
    in_fstring = 0
    for t in raw:
        if t.type == _FSTRING_START:
            in_fstring += 1
        elif t.type == _FSTRING_END:
            in_fstring -= 1
        tokens.append(
            _Tok(
                t.string,
                _normalized(t, scopes, in_fstring, ignore_locals, ignore_literals),
                t.start[0],
            )
        )
    return tokens


def _normalized(
    t: tokenize.TokenInfo,
    scopes: list[_Scope],
    in_fstring: int,
    ignore_locals: bool,
    ignore_literals: bool,
) -> str:
    """Masked value of one token: ``$STR``/``$NUM`` for literals and
    interpolated parts, ``$L<n>`` for function locals, else the lexeme."""
    text = t.string
    if ignore_literals:
        text = _literal_placeholder(t.type, text)
    if ignore_locals:
        text = _local_placeholder(t.type, text, t.start, scopes, in_fstring)
    return text


def _literal_placeholder(ttype: int, text: str) -> str:
    """String and numeric literals become opaque placeholders."""
    if ttype == tokenize.NUMBER:
        return "$NUM"
    if ttype in _STRING_TYPES:
        return "$STR"
    return text


def _local_placeholder(
    ttype: int,
    text: str,
    start: tuple[int, int],
    scopes: list[_Scope],
    in_fstring: int,
) -> str:
    """Interpolated strings go opaque (their raw lexeme embeds locals);
    declared locals rename consistently in first-use order."""
    if ttype in _STRING_TYPES and (in_fstring or not _FSTRING_START):
        return "$STR"
    return _renamed(ttype, text, start, scopes)


def _renamed(ttype: int, text: str, start: tuple[int, int], scopes: list[_Scope]) -> str:
    """A declared local becomes ``$L<n>`` (first use per scope assigns the
    number); API-surface identifiers keep their lexeme."""
    if ttype != tokenize.NAME:
        return text
    scope = _scope_at(scopes, start)
    if scope is None or text not in scope.names:
        return text
    return scope.placeholders.setdefault(text, f"$L{len(scope.placeholders) + 1}")


def _scope_at(scopes: list[_Scope], start: tuple[int, int]) -> _Scope | None:
    """The scope containing this position (scopes are disjoint and sorted,
    so one binary search suffices)."""
    i = bisect.bisect_right(scopes, start, key=lambda s: s.start) - 1
    if i >= 0 and start < scopes[i].end:
        return scopes[i]
    return None


def _scopes(source: str) -> list[_Scope]:
    """Outermost function scopes (``def``/``async def``/``lambda``), disjoint
    and sorted; nested functions land inside their enclosing scope — no
    inner-scope shadow tracking, matching upstream."""
    tree = ast.parse(source)
    scopes: list[_Scope] = []

    def visit(node: ast.AST, inside: bool) -> None:
        for child in ast.iter_child_nodes(node):
            scope_node = isinstance(child, _SCOPE_TYPES)
            if scope_node and not inside:
                scopes.append(_scope(child))
            visit(child, inside or scope_node)

    visit(tree, False)
    scopes.sort(key=lambda s: s.start)
    return scopes


def _scope(node: ast.AST) -> _Scope:
    """One scope spanning from its first parameter (def-site names and
    decorators stay outside — API surface) through the body end."""
    return _Scope(
        _scope_start(node),
        (node.end_lineno, node.end_col_offset),
        frozenset(_local_names(node)),
    )


def _scope_start(node: ast.AST) -> tuple[int, int]:
    """First parameter position, or the body start for empty signatures —
    always strictly after the def-site name (which stays API surface)."""
    args = node.args
    first = next(
        (
            a
            for a in (
                *args.posonlyargs,
                *args.args,
                args.vararg,
                *args.kwonlyargs,
                args.kwarg,
            )
            if a is not None
        ),
        None,
    )
    body = node.body[0] if isinstance(node.body, list) else node.body
    return (first.lineno, first.col_offset) if first else (body.lineno, body.col_offset)


def _local_names(node: ast.AST) -> set[str]:
    """Names bound anywhere in the subtree, minus ``global``/``nonlocal``
    redeclarations (those stay API-visible). The scope node's own name is
    excluded — recursive self-calls are API surface, exactly as upstream
    keeps an outermost declaration's name visible; nested declarations
    inside the scope do rename."""
    names: set[str] = set()
    excluded: set[str] = set()
    for sub in ast.walk(node):
        if sub is node:
            continue
        if isinstance(sub, (ast.Global, ast.Nonlocal)):
            excluded.update(sub.names)
            continue
        name = _binding_name(sub) or _pattern_name(sub)
        if name:
            names.add(name)
    return names - excluded


def _binding_name(sub: ast.AST) -> str | None:
    """The plain name a node binds: parameters (except ``self``/``cls``
    API receivers), locally declared defs/classes, stored/deleted names."""
    if isinstance(sub, ast.arg):
        return None if sub.arg in ("self", "cls") else sub.arg
    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return sub.name
    if isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Store, ast.Del)):
        return sub.id
    return _pattern_name(sub)


def _pattern_name(sub: ast.AST) -> str | None:
    """Except-as, match-capture, and 3.12+ type-parameter names."""
    if isinstance(sub, ast.ExceptHandler):
        return sub.name or ""
    if isinstance(sub, (ast.MatchAs, ast.MatchStar)):
        return sub.name
    if isinstance(sub, ast.MatchMapping):
        return sub.rest
    return _type_param_name(sub)


def _type_param_name(sub: ast.AST) -> str | None:
    if _TYPE_PARAM_TYPES and isinstance(sub, _TYPE_PARAM_TYPES):
        return sub.name
    return None


def _detect(files: list[_FileTokens], min_tokens: int, min_lines: int, ignore_locals: bool) -> None:
    """Mark duplicated windows. The raw-lexeme pass (Type-1) and — when
    local renaming is on — the masked pass (Type-2) union their marks:
    enabling ``ignore_locals`` only adds findings, never losing exact
    copies whose enclosing scopes shift placeholder numbering."""
    keys = ("lexeme", "value") if ignore_locals else ("value",)
    for key in keys:
        _mark_duplicates(files, min_tokens, min_lines, key)


def _mark_duplicates(files: list[_FileTokens], min_tokens: int, min_lines: int, key: str) -> None:
    """One detection pass: index every valid window of every file into one
    hash map keyed by the ``key`` field, then mark the tokens of each
    window whose hash occurred at least twice."""
    occurrences: dict[int, list[tuple[int, int]]] = {}
    for file_index, file_tokens in enumerate(files):
        _index_file(file_tokens, file_index, min_tokens, min_lines, occurrences, key)
    for positions in occurrences.values():
        if len(positions) < 2:
            continue
        for file_index, start in positions:
            for tok in files[file_index].tokens[start : start + min_tokens]:
                tok.duplicated = True


def _index_file(
    file_tokens: _FileTokens,
    file_index: int,
    min_tokens: int,
    min_lines: int,
    occurrences: dict[int, list[tuple[int, int]]],
    key: str,
) -> None:
    """Rabin-Karp roll over one file's windows: hash = polynome over the
    lexeme (or normalized) hashes mod 2**64 (upstream's rolling update)."""
    tokens = file_tokens.tokens
    if len(tokens) < min_tokens:
        return
    lines = [t.line for t in tokens]
    codes = [hash(getattr(t, key)) & _MASK for t in tokens]
    power = pow(_BASE, min_tokens - 1, _MASK + 1)
    h = 0
    for code in codes[:min_tokens]:
        h = ((h * _BASE) + code) & _MASK
    _record_window(occurrences, h, file_index, 0, lines, min_tokens, min_lines)
    for start in range(1, len(tokens) - min_tokens + 1):
        h = (h - (codes[start - 1] * power)) & _MASK
        h = ((h * _BASE) + codes[start + min_tokens - 1]) & _MASK
        _record_window(occurrences, h, file_index, start, lines, min_tokens, min_lines)


def _record_window(
    occurrences: dict[int, list[tuple[int, int]]],
    h: int,
    file_index: int,
    start: int,
    lines: list[int],
    min_tokens: int,
    min_lines: int,
) -> None:
    """A window participates only when it spans at least ``min_lines`` lines."""
    if lines[start + min_tokens - 1] - lines[start] + 1 >= min_lines:
        occurrences.setdefault(h, []).append((file_index, start))


def _build_result(files: list[_FileTokens], threshold: float) -> DuplicationResult:
    """Per-file duplicated-line share, violations, and the summary line
    (counting only the files that entered the scan — tokenized ones)."""
    violations: list[DuplicationViolation] = []
    checked_lines = 0
    duplicated_lines = 0
    for file_tokens in files:
        dup_lines = {t.line for t in file_tokens.tokens if t.duplicated}
        checked_lines += file_tokens.total_lines
        duplicated_lines += len(dup_lines)
        violation = _violation(file_tokens, dup_lines, threshold)
        if violation is not None:
            violations.append(violation)
    if violations:
        summary = f"{len(violations)}/{len(files)} files over {threshold}% duplication"
    else:
        total = duplicated_lines / checked_lines * 100.0
        summary = f"{len(files)} files, {total:.2f}% duplicated lines"
    return DuplicationResult(tuple(violations), summary)


def _violation(
    file_tokens: _FileTokens, dup_lines: set[int], threshold: float
) -> DuplicationViolation | None:
    """A violation when the file's duplicated-line share exceeds the
    threshold; reported at the first duplicated line."""
    percent = len(dup_lines) / file_tokens.total_lines * 100.0
    if percent <= threshold:
        return None
    return DuplicationViolation(
        file_tokens.rel,
        min(dup_lines),
        f"{percent:.2f}% duplicated lines > {threshold}%",
    )


def _line_count(source: str) -> int:
    """Newline-terminated line count (upstream's ``_lineCount``)."""
    if not source:
        return 0
    return source.count("\n") + (0 if source.endswith("\n") else 1)


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py duplicates",
        description="Flag files whose duplicated-line share exceeds a threshold.",
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"fail a file above this %% duplicated lines (default: {DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--min-tokens",
        type=int,
        default=MIN_TOKENS,
        help=f"minimum tokens in a duplicated block (default: {MIN_TOKENS})",
    )
    parser.add_argument(
        "--min-lines",
        type=int,
        default=MIN_LINES,
        help=f"minimum source lines a duplicated block spans (default: {MIN_LINES})",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="explicit files or directories (default: normal selection)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="skip files whose project-relative path matches this fnmatch glob",
    )
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        metavar="PATH",
        help="extra file/dir unioned into the scan (dirs expand recursively; missing skipped)",
    )
    parser.add_argument(
        "--ignore-locals",
        action="store_true",
        help="rename function locals consistently before hashing (detects renamed clones)",
    )
    parser.add_argument(
        "--ignore-literals",
        action="store_true",
        help="mask string and numeric literals before hashing",
    )
    return parser
