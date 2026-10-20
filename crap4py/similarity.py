"""``similarity`` subcommand: file-level near-duplicate detection.

Port of the crap4dart ``similarity`` gate + command (upstream 2b21966,
§11.23). Each scanned file is normalized per its profile — markdown drops
fenced code blocks, code drops comments/import-family lines and maps
literals to placeholders, raw is compared as-is — tokenized (markdown/raw:
2+ letter words lowercased; code: identifiers lowercased and literal
placeholders), and represented as the set of its 8-token shingles. Two
files are *similar* when Jaccard(A, B) >= ``--jaccard`` (default 0.8) OR
containment(A, B) >= ``--containment`` (default 0.95; intersection over
the smaller set — catches a small file largely copied into a big one).
Similar pairs merge transitively into clusters (union-find). Files with
fewer than ``--min-shingles`` shingles (default 50) are skipped — tiny
files carry no reliable signal.

Profiles are extension-mapped (``.md``/``.markdown``/``.mdx`` → markdown;
a 25-extension code family; anything else raw). The code profile is
language-agnostic: the generic engine strips block/line comments and
import-family lines, then tokenizes identifiers, strings and numbers —
``.py`` files instead go through the stdlib ``tokenize``/``ast`` path
(comments dropped natively, ``import``/from-import statements dropped,
strings/numbers mapped to ``S``/``N``, names lowercased) with the same
token alphabet.

Informational, like upstream: the command is the exploration tool for
tuning thresholds before relying on it, prints console or JSON reports,
and ALWAYS exits 0 (usage errors exit 1). Unlike upstream there is no
config file — the flags below are the whole config surface (kebab-case,
strictly validated).
"""

from __future__ import annotations

import ast
import fnmatch
import io
import json
import os
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path

from .args import UsageErrorParser

SHINGLE_SIZE = 8
DEFAULT_MIN_SHINGLES = 50
DEFAULT_JACCARD = 0.8
DEFAULT_CONTAINMENT = 0.95
DEFAULT_DIRS: tuple[str, ...] = (".",)
DEFAULT_INCLUDE: tuple[str, ...] = ("**/*.md",)
DEFAULT_EXCLUDE: tuple[str, ...] = (
    ".git/**",
    ".venv/**",
    "build/**",
    "coverage/**",
    "node_modules/**",
)
FORMAT_CHOICES = ("console", "json")
DEFAULT_FORMAT = "console"

MARKDOWN = "markdown"
CODE = "code"
RAW = "raw"

_MARKDOWN_EXTENSIONS = frozenset({".md", ".markdown", ".mdx"})
_CODE_EXTENSIONS = frozenset(
    {
        ".dart",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".jsx",
        ".java",
        ".py",
        ".kt",
        ".kts",
        ".swift",
        ".go",
        ".rs",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cs",
        ".rb",
        ".php",
        ".scala",
        ".gradle",
        ".groovy",
        ".sh",
        ".sql",
    }
)

# Line-comment markers across the code extension family.
_LINE_COMMENT = re.compile(r"^\s*(//|#|--|;)")
# Import-family directives across languages — boilerplate that would
# otherwise make every file of a project look like every other.
_IMPORT_LINES = (
    re.compile(r"^\s*(import|export|part|part of|library)\b"),
    re.compile(r"^\s*(from|require|require_once|using|package|include)\b"),
    re.compile(r"^#!"),
)

# Word tokens for prose and raw content: letters/digits runs of 2+
# characters (latin + cyrillic), lowercased.
_WORD_TOKEN = re.compile(r"[0-9A-Za-zА-Яа-яЁё]{2,}")
# Generic code tokens: identifiers, numbers and quoted strings.
_CODE_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|'[^']*'|\"[^\"]*\"|\d+(?:\.\d+)?")

_FSTRING_START = getattr(tokenize, "FSTRING_START", None)
_FSTRING_END = getattr(tokenize, "FSTRING_END", None)
_STRING_TYPES = frozenset(
    t for t in (tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", None)) if t
)


def profile_for(path: str) -> str:
    """The profile for a file path (by extension); ``raw`` for unknown."""
    ext = Path(path).suffix.lower()
    if ext in _MARKDOWN_EXTENSIONS:
        return MARKDOWN
    if ext in _CODE_EXTENSIONS:
        return CODE
    return RAW


def normalize_markdown(content: str) -> str:
    """Markdown prose: drops fenced code blocks, keeps heading/list words."""
    prose: list[str] = []
    in_fence = False
    for line in content.split("\n"):
        trimmed = line.strip()
        if trimmed.startswith("```") or trimmed.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence or not trimmed:
            continue
        prose.append(trimmed)
    return " ".join(prose)


def normalize_code(content: str) -> str:
    """Generic code: strips block/line comments and import-family lines."""
    kept: list[str] = []
    in_block_comment = False
    for line in content.split("\n"):
        stripped, in_block_comment = _strip_block_comments(line, in_block_comment)
        if _is_code_noise(stripped):
            continue
        kept.append(stripped.strip())
    return "\n".join(kept)


def _strip_block_comments(line: str, in_block_comment: bool) -> tuple[str, bool]:
    """The line with block comments removed; carries an unterminated ``/*``."""
    if in_block_comment:
        close = line.find("*/")
        if close < 0:
            return "", True
        return _strip_block_comments(line[close + 2 :], False)
    open_pos = line.find("/*")
    if open_pos < 0:
        return line, False
    close = line.find("*/", open_pos + 2)
    if close < 0:
        return line[:open_pos], True
    rest, still_open = _strip_block_comments(line[:open_pos] + line[close + 2 :], False)
    return rest, still_open


def _is_code_noise(line: str) -> bool:
    """Empty, a line comment, or an import-family directive."""
    trimmed = line.strip()
    if not trimmed or _LINE_COMMENT.match(trimmed):
        return True
    return any(pattern.match(trimmed) for pattern in _IMPORT_LINES)


def normalize(content: str, profile: str, path: str = "") -> str:
    """The normalizer for ``profile``; ``.py`` code is not line-normalized
    (the stdlib tokenizer drops its noise natively)."""
    if profile == MARKDOWN:
        return normalize_markdown(content)
    if profile == CODE and not path.endswith(".py"):
        return normalize_code(content)
    return content


def _import_line_numbers(source: str) -> set[int]:
    """Lines of import/from-import statements (multi-line parenthesized
    imports included) — the Python map of upstream's import-family lines."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            end = getattr(node, "end_lineno", None) or node.lineno
            lines.update(range(node.lineno, end + 1))
    return lines


def _python_tokens(source: str) -> list[str]:
    """Tokens for ``.py`` source: names lowercased, literals as ``S``/``N``,
    comments and import/from-import statements gone, f-strings opaque."""
    import_lines = _import_line_numbers(source)
    tokens: list[str] = []
    depth = 0
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.start[0] in import_lines:
                continue
            value, depth = _python_token_value(tok, depth)
            if value is not None:
                tokens.append(value)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return _generic_code_tokens(normalize_code(source))
    return tokens


def _python_token_value(tok: tokenize.TokenInfo, depth: int) -> tuple[str | None, int]:
    """The comparison value of one Python token (``None`` when dropped)
    and the next f-string depth — inside an f-string everything but the
    opening delimiter is opaque, so interpolated names never leak."""
    ttype, text = tok.type, tok.string
    if ttype == _FSTRING_START:
        return "S", depth + 1
    if ttype == _FSTRING_END:
        return None, max(0, depth - 1)
    if depth:
        return None, depth
    if ttype == tokenize.NAME:
        return text.lower(), depth
    if ttype == tokenize.NUMBER:
        return "N", depth
    if ttype in _STRING_TYPES:
        return "S", depth
    return None, depth


def _generic_code_tokens(text: str) -> list[str]:
    """Identifiers lowercased; strings/numbers become ``S``/``N`` so clones
    differing only in literal values still match."""
    tokens = []
    for match in _CODE_TOKEN.finditer(text):
        token = match.group(0)
        if token.startswith(("'", '"')):
            tokens.append("S")
        elif token[0].isdigit():
            tokens.append("N")
        else:
            tokens.append(token.lower())
    return tokens


def tokens_for(content: str, profile: str, path: str = "") -> list[str]:
    """Comparison tokens for already-normalized content under ``profile``."""
    if profile in (MARKDOWN, RAW):
        return [m.group(0).lower() for m in _WORD_TOKEN.finditer(content)]
    if path.endswith(".py"):
        return _python_tokens(content)
    return _generic_code_tokens(content)


def shingles_for(content: str, profile: str, path: str = "") -> frozenset[str]:
    """The set of 8-token shingles, empty below one shingle (such files
    carry no similarity signal). Content is normalized for the profile
    first — raw bytes would make unrelated files look alike."""
    tokens = tokens_for(normalize(content, profile, path), profile, path)
    if len(tokens) < SHINGLE_SIZE:
        return frozenset()
    return frozenset(
        "\x1f".join(tokens[i : i + SHINGLE_SIZE]) for i in range(len(tokens) - SHINGLE_SIZE + 1)
    )


def intersection_count(a: frozenset[str] | set[str], b: frozenset[str] | set[str]) -> int:
    """The number of shingles present in both sets."""
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    return sum(1 for shingle in small if shingle in large)


def jaccard(a: frozenset[str] | set[str], b: frozenset[str] | set[str]) -> float:
    """Intersection over union: 1.0 identical, 0.0 disjoint."""
    if not a or not b:
        return 0.0
    shared = intersection_count(a, b)
    return shared / (len(a) + len(b) - shared)


def containment(a: frozenset[str] | set[str], b: frozenset[str] | set[str]) -> float:
    """Intersection over the smaller set: how much of the smaller file the
    larger one reproduces (Jaccard stays low when one file is small)."""
    if not a or not b:
        return 0.0
    return intersection_count(a, b) / min(len(a), len(b))


@dataclass(frozen=True, slots=True)
class SimilarityFile:
    """One analyzed file: its project-relative path and shingle set."""

    path: str
    shingles: frozenset[str]


@dataclass(frozen=True, slots=True)
class SimilarityPair:
    """A pair whose similarity crosses at least one threshold."""

    a: str
    b: str
    jaccard: float
    containment: float


@dataclass(frozen=True, slots=True)
class SimilarityCluster:
    """A connected group of similar files (transitively merged pairs)."""

    files: tuple[str, ...]
    pairs: tuple[SimilarityPair, ...]


@dataclass(frozen=True, slots=True)
class SimilarityReport:
    """The outcome of a similarity scan."""

    scanned: int
    skipped: int
    pairs: tuple[SimilarityPair, ...]
    clusters: tuple[SimilarityCluster, ...]

    @property
    def clustered_paths(self) -> tuple[str, ...]:
        """The paths inside all clusters."""
        return tuple(path for cluster in self.clusters for path in cluster.files)

    @property
    def clean(self) -> bool:
        """Whether nothing crossed the thresholds."""
        return not self.pairs

    def metrics_for(self, path: str) -> tuple[float, float] | None:
        """The strongest pair metrics ``path`` participates in, or None."""
        best: tuple[float, float] | None = None
        for pair in self.pairs:
            if pair.a != path and pair.b != path:
                continue
            if best is None or pair.jaccard > best[0]:
                best = (pair.jaccard, pair.containment)
        return best


class _UnionFind:
    """Union-find over file paths, with path compression."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def add(self, item: str) -> None:
        self._parent[item] = item

    def _find(self, item: str) -> str:
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, a: str, b: str) -> None:
        root_a, root_b = self._find(a), self._find(b)
        if root_a != root_b:
            self._parent[root_b] = root_a


def analyze(
    root: str | Path,
    *,
    dirs: list[str] | tuple[str, ...],
    include: list[str] | tuple[str, ...],
    exclude: list[str] | tuple[str, ...],
    jaccard_threshold: float,
    containment_threshold: float,
    min_shingles: int,
) -> SimilarityReport:
    """Scans ``dirs`` under ``root`` and clusters near-duplicate content.

    Files below ``min_shingles`` are skipped; pairs cross when Jaccard >=
    ``jaccard_threshold`` OR containment >= ``containment_threshold``.
    """
    root = Path(root)
    analyzed: list[SimilarityFile] = []
    skipped = 0
    for rel, path in _collect(root, dirs, include, exclude):
        content = path.read_text(encoding="utf-8", errors="replace")
        shingles = shingles_for(content, profile_for(rel), rel)
        if len(shingles) < min_shingles:
            skipped += 1
            continue
        analyzed.append(SimilarityFile(rel, shingles))
    pairs = _pairs_over_thresholds(analyzed, jaccard_threshold, containment_threshold)
    return SimilarityReport(
        scanned=len(analyzed),
        skipped=skipped,
        pairs=pairs,
        clusters=_clusters(analyzed, pairs),
    )


def _collect(
    root: Path,
    dirs: list[str] | tuple[str, ...],
    include: list[str] | tuple[str, ...],
    exclude: list[str] | tuple[str, ...],
) -> list[tuple[str, Path]]:
    """Non-excluded files matching include under ``dirs``, sorted by
    project-relative path (missing directories are skipped silently)."""
    collected: dict[str, Path] = {}
    for dir_name in dirs:
        directory = Path(dir_name) if Path(dir_name).is_absolute() else root / dir_name
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            rel = Path(os.path.relpath(path, root)).as_posix()
            if _kept(rel, collected, include, exclude):
                collected[rel] = path
    return sorted(collected.items())


def _kept(
    rel: str,
    collected: dict[str, Path],
    include: list[str] | tuple[str, ...],
    exclude: list[str] | tuple[str, ...],
) -> bool:
    """Not already collected, matching include, and not excluded."""
    return rel not in collected and _matches_any(include, rel) and not _matches_any(exclude, rel)


def _matches_any(patterns: list[str] | tuple[str, ...], rel: str) -> bool:
    """fnmatch against the project-relative path; a leading ``**/`` also
    matches at the root (upstream glob's zero-directories semantics)."""
    return any(
        fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel, pattern.removeprefix("**/"))
        for pattern in patterns
    )


def _pairs_over_thresholds(
    files: list[SimilarityFile],
    jaccard_threshold: float,
    containment_threshold: float,
) -> tuple[SimilarityPair, ...]:
    """All pairs crossing either threshold, strongest Jaccard first."""
    pairs: list[SimilarityPair] = []
    for i in range(len(files)):
        for j in range(i + 1, len(files)):
            # ponytail: O(n²) pairwise scan — MinHash/LSH when corpora get big.
            first, second = files[i], files[j]
            j_score = jaccard(first.shingles, second.shingles)
            c_score = containment(first.shingles, second.shingles)
            if j_score < jaccard_threshold and c_score < containment_threshold:
                continue
            pairs.append(SimilarityPair(first.path, second.path, j_score, c_score))
    pairs.sort(key=lambda pair: (-pair.jaccard, pair.a, pair.b))
    return tuple(pairs)


def _clusters(
    files: list[SimilarityFile], pairs: tuple[SimilarityPair, ...]
) -> tuple[SimilarityCluster, ...]:
    """Transitive clusters of the threshold-crossing pairs, largest first."""
    union = _UnionFind()
    for file in files:
        union.add(file.path)
    for pair in pairs:
        union.union(pair.a, pair.b)
    groups: dict[str, list[str]] = {}
    for file in files:
        groups.setdefault(union._find(file.path), []).append(file.path)
    clusters = []
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort()
        clusters.append(
            SimilarityCluster(
                tuple(members),
                tuple(pair for pair in pairs if pair.a in members and pair.b in members),
            )
        )
    clusters.sort(key=lambda cluster: (-len(cluster.files), cluster.files[0]))
    return tuple(clusters)


@dataclass(frozen=True, slots=True)
class SimilarityConfig:
    """The similarity config surface (upstream ``SimilarityGateConfig``):
    this port has no config file, so the kebab-case flags are it. Strictly
    validated — invalid values are usage errors (exit 1)."""

    dirs: tuple[str, ...] = DEFAULT_DIRS
    include: tuple[str, ...] = DEFAULT_INCLUDE
    exclude: tuple[str, ...] = DEFAULT_EXCLUDE
    min_shingles: int = DEFAULT_MIN_SHINGLES
    jaccard: float = DEFAULT_JACCARD
    containment: float = DEFAULT_CONTAINMENT
    format: str = DEFAULT_FORMAT

    def validate(self) -> None:
        """Raises ValueError on out-of-range thresholds, a non-positive
        shingle floor, or an unknown output format."""
        for name in ("jaccard", "containment"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be within 0.0..1.0, got {value}")
        if self.min_shingles < 1:
            raise ValueError(f"min-shingles must be >= 1, got {self.min_shingles}")
        if self.format not in FORMAT_CHOICES:
            raise ValueError(f"format must be one of {FORMAT_CHOICES}, got {self.format}")


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py similarity",
        description="Scan for near-duplicate files (docs, code) and print the "
        "similarity clusters. Informational: always exits 0.",
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument(
        "--format",
        choices=FORMAT_CHOICES,
        default=DEFAULT_FORMAT,
        help="output format (default: console)",
    )
    parser.add_argument(
        "--jaccard",
        type=float,
        default=DEFAULT_JACCARD,
        help=f"Jaccard threshold override 0.0-1.0 (default: {DEFAULT_JACCARD})",
    )
    parser.add_argument(
        "--containment",
        type=float,
        default=DEFAULT_CONTAINMENT,
        help=f"containment threshold override 0.0-1.0 (default: {DEFAULT_CONTAINMENT})",
    )
    parser.add_argument(
        "--min-shingles",
        type=int,
        default=DEFAULT_MIN_SHINGLES,
        help=f"files below this shingle count are skipped (default: {DEFAULT_MIN_SHINGLES})",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="GLOB",
        help="glob of files to analyze (repeatable; default: **/*.md)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="glob of files to skip (repeatable)",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="directories to scan, project-relative (default: the whole project)",
    )
    return parser


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py similarity [options] [dirs...]``.

    Informational: always exits 0; usage/config errors exit 1.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    config = SimilarityConfig(
        dirs=tuple(args.paths) or DEFAULT_DIRS,
        include=tuple(args.include) or DEFAULT_INCLUDE,
        exclude=tuple(args.exclude) or DEFAULT_EXCLUDE,
        min_shingles=args.min_shingles,
        jaccard=args.jaccard,
        containment=args.containment,
        format=args.format,
    )
    try:
        config.validate()
    except ValueError as exc:
        parser.error(str(exc))  # exits 1
    report = analyze(
        project_root,
        dirs=config.dirs,
        include=config.include,
        exclude=config.exclude,
        jaccard_threshold=config.jaccard,
        containment_threshold=config.containment,
        min_shingles=config.min_shingles,
    )
    if config.format == "json":
        _print_json(report, config)
    else:
        _print_console(report, config)
    return 0


def _print_console(report: SimilarityReport, config: SimilarityConfig) -> None:
    """The console report."""
    print(
        f"similarity: {report.scanned} files scanned "
        f"({report.skipped} skipped below {config.min_shingles} shingles), "
        f"thresholds jaccard >= {config.jaccard} / containment >= {config.containment}"
    )
    if report.clean:
        print("no similar files")
        return
    for index, cluster in enumerate(report.clusters, 1):
        print()
        print(f"cluster {index} — {len(cluster.files)} files:")
        for file in cluster.files:
            metrics = report.metrics_for(file)
            assert metrics is not None  # a clustered file always has a pair
            print(f"  {file} (jaccard {metrics[0]:.2f}, containment {metrics[1]:.2f})")
    print()
    print(f"{len(report.clustered_paths)} files in {len(report.clusters)} clusters")


def _print_json(report: SimilarityReport, config: SimilarityConfig) -> None:
    """The JSON report (stdout stays JSON-only)."""
    print(
        json.dumps(
            {
                "scanned": report.scanned,
                "skipped": report.skipped,
                "thresholds": {
                    "jaccard": config.jaccard,
                    "containment": config.containment,
                    "min_shingles": config.min_shingles,
                },
                "pairs": [
                    {
                        "a": pair.a,
                        "b": pair.b,
                        "jaccard": round(pair.jaccard, 4),
                        "containment": round(pair.containment, 4),
                    }
                    for pair in report.pairs
                ],
                "clusters": [{"files": list(cluster.files)} for cluster in report.clusters],
            },
            indent=2,
        )
    )
