"""``duplicates`` subcommand: token-based copy-paste duplication gate.

Tokenizes every scanned file (comments and whitespace artifacts dropped,
lexemes kept), then indexes sliding windows of ``--min-tokens`` lexemes
with a Rabin-Karp hash (mod 2**64), jscpd-style, across all files at
once. A window participates when it spans at least ``--min-lines``
source lines. Every hash bucket of at least two window positions is
verified by token equality (fingerprints can collide) and extended to
the maximal extent its occurrences share; the resulting candidate clone
groups compete in one greedy longest-first selection that accepts only
non-overlapping occurrences (PMD CPD Match semantics) and marks their
tokens. Port of the crap4dart ``duplication`` gate up through the
CPD-parity commit d6c3ab6 (clones mode, ``ignore_identifiers`` /
``ignore_imports`` / ``skip_duplicate_files``) including the dc64e9c
per-gate ``sources`` union (``--source``).

Report modes: ``percentage`` (default) fails each file whose distinct
duplicated lines exceed ``--threshold`` percent of its lines; ``clones``
(``--report clones``) reports one violation per accepted clone group
with all its occurrence locations.

Opt-in normalizations extend detection to renamed (Type-2) clones:
``--ignore-locals`` renames function-local identifiers to ``$L<n>``
placeholders in first-use order per outermost function scope, and
``--ignore-literals`` masks string/numeric literals as ``$STR``/``$NUM``.
CPD-parity knobs: ``--ignore-identifiers`` masks every identifier-shaped
token (structure-only matching), ``--ignore-imports`` drops import and
from-import statements from tokenization, and ``--skip-duplicate-files``
analyzes a token-identical file only once. Detection unions the
raw-lexeme pass with the masked one, so exact copies are never lost when
scopes shift placeholder numbering.
"""

from __future__ import annotations

import ast
import bisect
import fnmatch
import io
import keyword
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
IGNORE_IDENTIFIERS = False
IGNORE_IMPORTS = False
SKIP_DUPLICATE_FILES = False
REPORT_CHOICES = ("percentage", "clones")
DEFAULT_REPORT = "percentage"

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
class _CloneOccurrence:
    """One occurrence of a clone group: ``[start_token, end_token)`` of
    one file's token stream."""

    file_index: int
    start_token: int
    end_token: int


@dataclass(slots=True)
class _CloneGroup:
    """One accepted clone group: non-overlapping occurrences of the same
    token block."""

    occurrences: list[_CloneOccurrence]

    @property
    def token_count(self) -> int:
        """Token count shared by every occurrence."""
        first = self.occurrences[0]
        return first.end_token - first.start_token


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
        ignore_identifiers=args.ignore_identifiers,
        ignore_imports=args.ignore_imports,
        skip_duplicate_files=args.skip_duplicate_files,
        report=args.report,
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
    ignore_identifiers: bool = IGNORE_IDENTIFIERS,
    ignore_imports: bool = IGNORE_IMPORTS,
    skip_duplicate_files: bool = SKIP_DUPLICATE_FILES,
    report: str = DEFAULT_REPORT,
) -> DuplicationResult:
    """Gate core: union ``--source`` paths into the scan set, tokenize,
    detect duplicates, and build violations plus the summary."""
    root = Path(project_root)
    paths = sorted({Path(f).resolve() for f in files} | set(_expand_sources(sources, root)))
    tokenized = _tokenized_files(
        paths,
        root,
        excludes,
        min_tokens,
        ignore_locals,
        ignore_literals,
        ignore_identifiers,
        ignore_imports,
        skip_duplicate_files,
    )
    if not tokenized:
        return DuplicationResult((), "no files with enough tokens")
    groups = _detect(tokenized, min_tokens, min_lines, ignore_locals)
    return _build_result(tokenized, groups, threshold, report, min_tokens, min_lines)


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
    ignore_identifiers: bool = IGNORE_IDENTIFIERS,
    ignore_imports: bool = IGNORE_IMPORTS,
    skip_duplicate_files: bool = SKIP_DUPLICATE_FILES,
) -> list[_FileTokens]:
    """Load and tokenize the scan set; files matching an ``--exclude``
    glob (fnmatch on the project-relative path) or holding fewer than
    ``min_tokens`` tokens are skipped from the scan entirely. With
    ``skip_duplicate_files`` a file whose masked token stream equals an
    earlier file's is analyzed once (CPD --skip-duplicate-files)."""
    tokenized = []
    for path in paths:
        rel = _relative_to_root(path, root)
        if any(fnmatch.fnmatch(rel, pattern) for pattern in excludes):
            continue
        loaded = _load(path, ignore_locals, ignore_literals, ignore_identifiers, ignore_imports)
        if loaded is None or len(loaded[1]) < min_tokens:
            continue
        source, tokens = loaded
        if skip_duplicate_files and _is_duplicate(tokens, tokenized):
            continue
        tokenized.append(_FileTokens(rel, tokens, _line_count(source)))
    return tokenized


def _is_duplicate(tokens: list[_Tok], tokenized: list[_FileTokens]) -> bool:
    """Whether a token stream equals an earlier file's by masked value
    (CPD --skip-duplicate-files)."""
    return any(
        len(t.tokens) == len(tokens)
        and all(x.value == y.value for x, y in zip(t.tokens, tokens, strict=False))
        for t in tokenized
    )


def _load(
    path: Path,
    ignore_locals: bool = IGNORE_LOCALS,
    ignore_literals: bool = IGNORE_LITERALS,
    ignore_identifiers: bool = IGNORE_IDENTIFIERS,
    ignore_imports: bool = IGNORE_IMPORTS,
) -> tuple[str, list[_Tok]] | None:
    """Read a file and keep one ``_Tok`` per non-skipped token: raw lexeme
    plus normalized value (equal unless masking applies)."""
    try:
        source = path.read_text(encoding="utf-8")
        ranges = _directive_ranges(source) if ignore_imports else []
        raw = [
            t
            for t in tokenize.generate_tokens(io.StringIO(source).readline)
            if t.type not in _SKIP_TYPES and not _in_ranges(t.start, ranges)
        ]
        tokens = _mask_tokens(raw, source, ignore_locals, ignore_literals, ignore_identifiers)
    except (OSError, UnicodeDecodeError, SyntaxError, tokenize.TokenError) as exc:
        print(f"Warning: could not tokenize {path}: {exc}", file=sys.stderr)
        return None
    return source, tokens


def _directive_ranges(source: str) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """``(start, end)`` line/col ranges of import and from-import
    statements, for ``--ignore-imports`` skipping (upstream's
    import/export/part directives)."""
    return [
        ((node.lineno, node.col_offset), (node.end_lineno, node.end_col_offset))
        for node in ast.parse(source).body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]


def _in_ranges(
    start: tuple[int, int], ranges: list[tuple[tuple[int, int], tuple[int, int]]]
) -> bool:
    """Whether a token start position falls inside any range."""
    return any(lo <= start < hi for lo, hi in ranges)


def _mask_tokens(
    raw: list[tokenize.TokenInfo],
    source: str,
    ignore_locals: bool,
    ignore_literals: bool,
    ignore_identifiers: bool = IGNORE_IDENTIFIERS,
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
                _normalized(
                    t, scopes, in_fstring, ignore_locals, ignore_literals, ignore_identifiers
                ),
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
    ignore_identifiers: bool = IGNORE_IDENTIFIERS,
) -> str:
    """Masked value of one token: ``$STR``/``$NUM`` for literals and
    interpolated parts, ``$ID`` for every identifier when
    ``ignore_identifiers`` is set, ``$L<n>`` for function locals, else
    the lexeme."""
    text = t.string
    if ignore_literals:
        text = _literal_placeholder(t.type, text)
    if ignore_identifiers and _is_identifier_token(t.type, text):
        # CPD --ignore-identifiers: structure-only matching; makes the
        # per-scope renaming below redundant.
        return "$ID"
    if ignore_locals:
        text = _local_placeholder(t.type, text, t.start, scopes, in_fstring)
    return text


def _is_identifier_token(ttype: int, text: str) -> bool:
    """Identifier-shaped: a NAME that is not a hard keyword. Python's map
    of upstream's IDENTIFIER plus built-in/pseudo keywords — soft
    keywords (``match``, ``case``, ``type``, ``_``) and builtins (``len``,
    ``range``, ``print``, ...) occupy identifier slots and mask too."""
    return ttype == tokenize.NAME and not keyword.iskeyword(text)


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


def _detect(
    files: list[_FileTokens], min_tokens: int, min_lines: int, ignore_locals: bool
) -> list[_CloneGroup]:
    """Detect duplicated blocks, mark the involved tokens and return the
    accepted clone groups (longest-first, non-overlapping — the PMD CPD
    Match semantics). The raw-lexeme pass (Type-1) and — when local
    renaming is on — the masked pass (Type-2) union their candidates:
    enabling ``ignore_locals`` only adds findings, never losing exact
    copies whose enclosing scopes shift placeholder numbering."""
    keys = ("lexeme", "value") if ignore_locals else ("value",)
    candidates = [
        group for key in keys for group in _collect_groups(files, min_tokens, min_lines, key)
    ]
    # Longest clones first, then most occurrences, then position — the
    # selection order that keeps reporting deterministic.
    candidates.sort(key=_group_priority)
    return _accept_groups(files, candidates)


def _group_priority(group: _CloneGroup) -> tuple[int, int, int, int]:
    """The reporting order: longest clones first, then most occurrences,
    then first position."""
    first = group.occurrences[0]
    return (-group.token_count, -len(group.occurrences), first.file_index, first.start_token)


def _accept_groups(files: list[_FileTokens], candidates: list[_CloneGroup]) -> list[_CloneGroup]:
    """Greedily accepts non-overlapping groups and marks their tokens."""
    accepted: list[_CloneGroup] = []
    taken: dict[int, list[tuple[int, int]]] = {}
    for group in candidates:
        if any(
            _overlaps(taken.get(o.file_index), o.start_token, o.end_token)
            for o in group.occurrences
        ):
            continue
        for o in group.occurrences:
            taken.setdefault(o.file_index, []).append((o.start_token, o.end_token))
            for tok in files[o.file_index].tokens[o.start_token : o.end_token]:
                tok.duplicated = True
        accepted.append(group)
    return accepted


def _overlaps(ranges: list[tuple[int, int]] | None, start: int, end: int) -> bool:
    """Whether ``start..end`` overlaps any accepted range of one file."""
    if not ranges:
        return False
    return any(start < e and s < end for s, e in ranges)


def _collect_groups(
    files: list[_FileTokens], min_tokens: int, min_lines: int, key: str
) -> list[_CloneGroup]:
    """One detection pass: index every valid window of every file into one
    hash map keyed by the ``key`` field, then turn each bucket of at
    least two window positions into one candidate group. Windows are
    verified against the bucket reference by actual token equality
    (Rabin-Karp fingerprints can collide) and extended to the maximal
    extent all occurrences share, clipped to the common token count."""
    occurrences: dict[int, list[tuple[int, int]]] = {}
    for file_index, file_tokens in enumerate(files):
        _index_file(file_tokens, file_index, min_tokens, min_lines, occurrences, key)
    values = [[getattr(t, key) for t in f.tokens] for f in files]
    groups = []
    for positions in occurrences.values():
        if len(positions) < 2:
            continue
        group = _group_from_bucket(files, values, positions, min_tokens, min_lines)
        if group is not None:
            groups.append(group)
    return groups


def _group_from_bucket(
    files: list[_FileTokens],
    values: list[list[str]],
    positions: list[tuple[int, int]],
    min_tokens: int,
    min_lines: int,
) -> _CloneGroup | None:
    """One candidate clone group from a bucket of window positions, or
    None when the bucket holds no real duplicate of at least
    ``min_lines`` lines."""
    ref_index, ref_start = positions[0]
    occurrences = [positions[0]]
    extents = []
    for file_index, start in positions[1:]:
        if (
            values[file_index][start : start + min_tokens]
            != values[ref_index][ref_start : ref_start + min_tokens]
        ):
            continue  # fingerprint collision, not a real duplicate
        occurrences.append((file_index, start))
        extents.append(_extent(values[file_index], start, values[ref_index], ref_start, min_tokens))
    if len(occurrences) < 2:
        return None
    # The reference must not extend against itself — that would grow its
    # extent over the whole file. The common length therefore comes from
    # the other occurrences' shared extents, capped at the window size;
    # the reference is anchored at its own window and clipped to the
    # same length, the other occurrences at their extended start.
    common = min(min_tokens, min(e - s for s, e in extents))
    ref_tokens = files[ref_index].tokens
    line_span = ref_tokens[ref_start + common - 1].line - ref_tokens[ref_start].line + 1
    if line_span < min_lines:
        return None
    return _CloneGroup(
        [_CloneOccurrence(ref_index, ref_start, ref_start + common)]
        + [
            _CloneOccurrence(file_index, s, s + common)
            for (file_index, _), (s, _) in zip(occurrences[1:], extents, strict=True)
        ]
    )


def _extent(
    values: list[str], start: int, ref: list[str], ref_start: int, min_tokens: int
) -> tuple[int, int]:
    """Maximal extent of the window at ``start`` shared with the reference
    window at ``ref_start``: grown left and right while the surrounding
    tokens match."""
    s, rs = start, ref_start
    while s > 0 and rs > 0 and values[s - 1] == ref[rs - 1]:
        s -= 1
        rs -= 1
    e, re_end = start + min_tokens, ref_start + min_tokens
    while e < len(values) and re_end < len(ref) and values[e] == ref[re_end]:
        e += 1
        re_end += 1
    return s, e


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


def _build_result(
    files: list[_FileTokens],
    groups: list[_CloneGroup],
    threshold: float,
    report: str,
    min_tokens: int,
    min_lines: int,
) -> DuplicationResult:
    """Violations plus the summary line for the configured report mode:
    ``percentage`` (default) — per-file duplicated-line share over the
    threshold — or ``clones`` — one violation per clone group."""
    if report == "clones":
        return _clones_result(files, groups, min_tokens, min_lines)
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


def _clones_result(
    files: list[_FileTokens], groups: list[_CloneGroup], min_tokens: int, min_lines: int
) -> DuplicationResult:
    """One violation per clone group, with every occurrence location in
    the message (PMD CPD style)."""
    violations = [
        DuplicationViolation(
            files[group.occurrences[0].file_index].rel,
            files[group.occurrences[0].file_index].tokens[group.occurrences[0].start_token].line,
            _clone_message(files, group),
        )
        for group in groups
    ]
    if violations:
        summary = f"{len(violations)} clone groups (>= {min_tokens} tokens, >= {min_lines} lines)"
    else:
        summary = f"no clones >= {min_lines} lines across {len(files)} files"
    return DuplicationResult(tuple(violations), summary)


def _clone_message(files: list[_FileTokens], group: _CloneGroup) -> str:
    """``clone: N tokens / M lines, K occurrences:`` plus up to three
    ``file:line-line`` locations and a ``(+N more)`` tail."""
    locations = []
    for o in group.occurrences:
        file_tokens = files[o.file_index]
        last = file_tokens.tokens[o.end_token - 1].line
        first = file_tokens.tokens[o.start_token].line
        locations.append(f"{file_tokens.rel}:{first}-{last}")
    shown = "; ".join(locations[:3])
    more = f" (+{len(locations) - 3} more)" if len(locations) > 3 else ""
    first = group.occurrences[0]
    first_file = files[first.file_index]
    line_span = (
        first_file.tokens[first.end_token - 1].line - first_file.tokens[first.start_token].line + 1
    )
    return (
        f"clone: {group.token_count} tokens / {line_span} lines, "
        f"{len(locations)} occurrences: {shown}{more}"
    )


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
    parser.add_argument(
        "--ignore-identifiers",
        action="store_true",
        help="mask all identifiers to one placeholder before hashing (CPD --ignore-identifiers)",
    )
    parser.add_argument(
        "--ignore-imports",
        action="store_true",
        help="exclude import and from-import statements from tokenization",
    )
    parser.add_argument(
        "--skip-duplicate-files",
        action="store_true",
        help="analyze a file whose token stream equals an earlier file's only once",
    )
    parser.add_argument(
        "--report",
        choices=REPORT_CHOICES,
        default=DEFAULT_REPORT,
        help="percentage: per-file duplicated-line share; clones: one violation per clone group",
    )
    return parser
