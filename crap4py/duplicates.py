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
"""

from __future__ import annotations

import fnmatch
import io
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path

from .analyzer import _relative_to_root
from .args import UsageErrorParser
from .files import PathLike, expand_paths, find_source_files

MIN_TOKENS = 50
MIN_LINES = 5
DEFAULT_THRESHOLD = 1.0

_BASE = 0x9E3779B97F4A7C15
_MASK = (1 << 64) - 1

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
    """One lexeme and its 1-based source line, with the duplicate flag."""

    value: str
    line: int
    duplicated: bool = False


@dataclass(slots=True)
class _FileTokens:
    """A tokenized file: token stream, project-relative path, line count."""

    rel: str
    tokens: list[_Tok]
    total_lines: int


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
) -> DuplicationResult:
    """Gate core: union ``--source`` paths into the scan set, tokenize,
    detect duplicates, and build violations plus the summary."""
    root = Path(project_root)
    paths = sorted({Path(f).resolve() for f in files} | set(_expand_sources(sources, root)))
    tokenized = _tokenized_files(paths, root, excludes, min_tokens)
    if not tokenized:
        return DuplicationResult((), "no files with enough tokens")
    _detect(tokenized, min_tokens, min_lines)
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
    paths: list[Path], root: Path, excludes: list[str] | tuple[str, ...], min_tokens: int
) -> list[_FileTokens]:
    """Load and tokenize the scan set; files matching an ``--exclude``
    glob (fnmatch on the project-relative path) or holding fewer than
    ``min_tokens`` tokens are skipped from the scan entirely."""
    tokenized = []
    for path in paths:
        rel = _relative_to_root(path, root)
        if any(fnmatch.fnmatch(rel, pattern) for pattern in excludes):
            continue
        loaded = _load(path)
        if loaded is None or len(loaded[1]) < min_tokens:
            continue
        source, tokens = loaded
        tokenized.append(_FileTokens(rel, tokens, _line_count(source)))
    return tokenized


def _load(path: Path) -> tuple[str, list[_Tok]] | None:
    """Read a file and keep ``(lexeme, line)`` for every non-skipped token."""
    try:
        source = path.read_text(encoding="utf-8")
        tokens = [
            _Tok(t.string, t.start[0])
            for t in tokenize.generate_tokens(io.StringIO(source).readline)
            if t.type not in _SKIP_TYPES
        ]
    except (OSError, UnicodeDecodeError, SyntaxError, tokenize.TokenError) as exc:
        print(f"Warning: could not tokenize {path}: {exc}", file=sys.stderr)
        return None
    return source, tokens


def _detect(files: list[_FileTokens], min_tokens: int, min_lines: int) -> None:
    """Index every valid window of every file into one hash map, then mark
    the tokens of each window whose hash occurred at least twice."""
    occurrences: dict[int, list[tuple[int, int]]] = {}
    for file_index, file_tokens in enumerate(files):
        _index_file(file_tokens, file_index, min_tokens, min_lines, occurrences)
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
) -> None:
    """Rabin-Karp roll over one file's windows: hash = polynome over the
    lexeme hashes mod 2**64 (upstream's rolling update)."""
    tokens = file_tokens.tokens
    if len(tokens) < min_tokens:
        return
    lines = [t.line for t in tokens]
    codes = [hash(t.value) & _MASK for t in tokens]
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
    return parser
