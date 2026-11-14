"""``profile`` subcommand: source-instrumentation profiler.

Copies the project to a temp dir, reports method entry/exit for every
function/method body to an injected collector (stdlib ``ast``), runs the
test suite against the instrumented copy, and reports per-function timing
(inclusive TOTAL plus SELF — inclusive minus nested instrumented calls).
Port of the crap4dart profiler.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .analyzer import _relative_to_root
from .args import UsageErrorParser
from .complexity import _FUNCTION_TYPES, _qualified, extract_methods
from .files import expand_paths, find_source_files

_OUTPUT_NAME = ".crap_profile.json"
_REPORTS_DIR = "profile-reports"
_COLLECTOR_NAME = "_crap_collector.py"
_IMPORTS = "import _crap_collector as __crap_cc\n"

_COPY_IGNORE = shutil.ignore_patterns(
    ".git",
    ".venv",
    ".venv-score",
    "venv",
    "__pycache__",
    "build",
    "dist",
    "*.egg-info",
    ".ruff_cache",
    ".pytest_cache",
    _REPORTS_DIR,
    ".crap_profile_temp",
)

_PROFILE_HEADER = (
    f"{'TOTAL':>9} {'SELF':>9} {'%':>7} {'CALLS':>6} {'MEAN(µs)':>9} "
    f"{'MAX(µs)':>8} {'@60fps(ms)':>10}  {'METHOD':<24} FILE:LINE"
)


@dataclass(frozen=True, slots=True)
class ProfileEntry:
    """A method inventory location merged with its measured timing."""

    method: str
    file: str
    line: int
    calls: int
    total_micros: float
    self_micros: float
    min_micros: float
    max_micros: float

    @property
    def mean_micros(self) -> float:
        return self.total_micros / self.calls if self.calls else 0.0


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py profile [options] [paths...]``."""
    args = _build_parser().parse_args(argv)
    files = (
        expand_paths(args.paths, project_root) if args.paths else find_source_files(project_root)
    )
    if not files:
        print("No Python files to profile.")
        return 0
    timings = collect_timings(files, project_root, args.name)
    entries = attribute(timings, files, project_root)
    _write_reports(entries, args.threshold, project_root)
    print(format_report(entries, args.top, args.threshold))
    if args.threshold is not None and _exceeds_threshold(entries, args.threshold):
        print(
            f"Profile threshold exceeded: {_worst_ms(entries):.2f} > {args.threshold:.2f}ms",
            file=sys.stderr,
        )
        return 2
    return 0


# --- instrumentation -----------------------------------------------------------


def instrument_source(source: str, filename: str = "<source>", module: str | None = None) -> str:
    """Wrap every function body in a timer; returns the source unchanged when
    there is nothing to instrument. ``module`` prefixes timing keys (e.g.
    ``"pkg.mod"`` → ``"pkg.mod.func"``) so same-named methods in different
    modules never merge into one timing row.
    """
    tree = ast.parse(source, filename=filename)
    if not _wrap_functions(tree, prefix=module):
        return source
    index = _import_insert_index(tree.body)
    tree.body[index:index] = ast.parse(_IMPORTS).body
    return ast.unparse(tree) + "\n"


def _import_insert_index(body: list[ast.stmt]) -> int:
    """Where the collector imports go: after any docstring and ``__future__`` imports."""
    index = 1 if _is_docstring(body) else 0
    while index < len(body) and _is_future_import(body[index]):
        index += 1
    return index


def _is_docstring(body: list[ast.stmt]) -> bool:
    first = body[0] if body else None
    return (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    )


def _is_future_import(node: ast.stmt) -> bool:
    return isinstance(node, ast.ImportFrom) and node.module == "__future__"


def _wrap_functions(node: ast.AST, prefix: str | None) -> bool:
    """Wrap each descendant function body; key = qualified name (complexity
    rules), nested under the fully qualified parent (``mod.run.inner``)."""
    wrapped = False
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            wrapped = _wrap_functions(child, _qualified(prefix, child.name)) or wrapped
        elif isinstance(child, _FUNCTION_TYPES):
            qualified = _qualified(prefix, child.name)
            _wrap_functions(child, qualified)
            child.body = _wrapped_body(qualified, child.body)
            wrapped = True
    return wrapped


def _wrapped_body(key: str, body: list[ast.stmt]) -> list[ast.stmt]:
    """``__crap_cc.enter(key); try: <body> finally: __crap_cc.exit(key)`` —
    the collector owns per-call timers on its call stack, so it can report
    self time (inclusive minus nested instrumented calls)."""
    enter = ast.parse(f"__crap_cc.enter({key!r})").body[0]
    exit_ = ast.parse(f"__crap_cc.exit({key!r})").body[0]
    return [enter, ast.Try(body=list(body), handlers=[], orelse=[], finalbody=[exit_])]


# --- instrumented run ----------------------------------------------------------


def collect_timings(files: list[Path], project_root: Path, name: str | None) -> dict[str, dict]:
    """Create the instrumented copy, run tests in it, return raw timings."""
    temp_dir = _create_temp_copy(project_root)
    try:
        _instrument_files(files, project_root, temp_dir)
        _run_tests(temp_dir, name)
        return _load_timings(temp_dir / _OUTPUT_NAME)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _create_temp_copy(project_root: Path) -> Path:
    temp_dir = project_root / ".crap_profile_temp"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    shutil.copytree(project_root, temp_dir, ignore=_COPY_IGNORE, symlinks=True)
    return temp_dir


def _instrument_files(files: list[Path], project_root: Path, temp_dir: Path) -> None:
    root = project_root.resolve()
    for file_path in files:
        try:
            rel = Path(file_path).resolve().relative_to(root)
        except ValueError:
            continue
        target = temp_dir / rel
        if target.is_file():
            source = Path(file_path).read_text(encoding="utf-8")
            target.write_text(
                instrument_source(source, str(file_path), _module_prefix(rel)),
                encoding="utf-8",
            )
    (temp_dir / _COLLECTOR_NAME).write_text(COLLECTOR_SOURCE, encoding="utf-8")


def _module_prefix(rel: Path) -> str:
    """Dotted module path of a project-relative file (``pkg/sub/mod.py`` →
    ``pkg.sub.mod``); prefixes timing keys so cross-module names stay apart."""
    return rel.with_suffix("").as_posix().replace("/", ".")


def _run_tests(temp_dir: Path, name: str | None) -> None:
    """Run the test suite in the instrumented copy; warn (not fail) on errors."""
    print("Running instrumented tests...", file=sys.stderr)
    env = {
        **os.environ,
        "CRAP_PROFILE_OUTPUT": str(temp_dir / _OUTPUT_NAME),
        "PYTHONPATH": _pythonpath(temp_dir),
    }
    for cmd in _test_commands(temp_dir, name):
        result = subprocess.run(
            cmd, cwd=str(temp_dir), env=env, capture_output=True, text=True, check=False
        )
        if result.returncode == 0:
            return
        _warn_failed(cmd, result)


def _test_commands(temp_dir: Path, name: str | None) -> list[list[str]]:
    filter_args = ["-k", name] if name else []
    discover_args = ["discover", "-s", "tests"] if (temp_dir / "tests").is_dir() else ["discover"]
    return [
        [sys.executable, "-m", "pytest", "-q", *filter_args],
        [sys.executable, "-m", "unittest", *discover_args, *filter_args],
    ]


def _pythonpath(temp_dir: Path) -> str:
    entries = [str(temp_dir)]
    src = temp_dir / "src"
    if src.is_dir():
        entries.append(str(src))
    return os.pathsep.join(entries)


def _warn_failed(cmd: list[str], result: subprocess.CompletedProcess) -> None:
    """Reports a failed test run. The LAST 30 lines are shown, not the
    first: test runners print progress lines for every file before the
    failure, so the actual error lives at the tail. The message is
    explicitly prefixed ``TESTS FAILED`` — the profile command can still
    exit 2 on a threshold verdict, and that must not be mistakable for
    the test run's own failure."""
    print(
        f"TESTS FAILED ({' '.join(cmd)} exited with code {result.returncode}) — profiling data "
        "may be incomplete; this is separate from any threshold verdict below.",
        file=sys.stderr,
    )
    for stream in (result.stdout, result.stderr):
        tail = "\n".join(stream.splitlines()[-30:])
        if tail:
            print(tail, file=sys.stderr)


def _load_timings(path: Path) -> dict[str, dict]:
    """Reads and merges the profiling output: the collector writes one
    shard per writer (``<output>.<shardId>.json``, each holding that
    writer's cumulative stats); calls and totals are summed, min
    minimized, max maximized — a writer's shard is never re-read into
    another writer's data, so summing cannot double-count."""
    merged: dict[str, dict] = {}
    for shard in _shard_files(path):
        _merge_shard(merged, shard)
    if not merged:
        print("Warning: no profiling data was produced.", file=sys.stderr)
    return merged


def _shard_files(path: Path) -> list[Path]:
    """``path`` itself (if it exists — legacy or hand-written output) plus
    every collector shard beside it (``<name>.<shardId>.json``), excluding
    in-flight ``.tmp`` files."""
    files = [path] if path.exists() else []
    prefix, suffix = f"{path.name}.", ".json"
    try:
        siblings = sorted(path.parent.iterdir())
    except OSError:
        return files  # vanished/never-existing temp dir — nothing to merge
    return files + [
        entry
        for entry in siblings
        if entry.name.startswith(prefix)
        and entry.name.endswith(suffix)
        and ".tmp" not in entry.name
    ]


def _merge_shard(merged: dict[str, dict], shard: Path) -> None:
    """Decodes ``shard`` and merges its per-method stats into ``merged``.
    Corrupt or non-map content is skipped (a concurrent rename can leave a
    reader mid-file; the remaining shards still report). ``#``-prefixed
    keys are collector diagnostics (e.g. ``#flushes``) and are skipped."""
    try:
        raw = json.loads(shard.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(raw, dict):
        return
    for key, stats in raw.items():
        if key.startswith("#") or not isinstance(stats, dict):
            continue
        _merge_entry(merged, key, stats)


def _merge_entry(merged: dict[str, dict], key: str, stats: dict) -> None:
    """Merges one method's ``stats`` into ``merged`` under ``key``."""
    existing = merged.get(key)
    if existing is None:
        merged[key] = stats
        return
    for field in ("calls", "totalMicros", "totalSelfMicros"):
        existing[field] = existing.get(field, 0) + stats.get(field, 0)
    existing["minMicros"] = _min_of(existing.get("minMicros"), stats.get("minMicros"))
    existing["maxMicros"] = max(existing.get("maxMicros", 0), stats.get("maxMicros", 0))


def _min_of(a: float | None, b: float | None) -> float | None:
    """Smaller of two optional minima; a missing field is no constraint
    (never a false 0)."""
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


# --- attribution & report ------------------------------------------------------


def attribute(
    timings: dict[str, dict], files: list[Path], project_root: Path
) -> list[ProfileEntry]:
    """Match timing keys to the method inventory; unmatched entries are ignored."""
    locations = _method_locations(files, project_root)
    entries = []
    for key, stats in timings.items():
        loc = locations.get(key)
        if loc is not None:
            entries.append(_entry(key, loc, stats))
    entries.sort(key=lambda e: -e.total_micros)
    return entries


def format_report(entries: list[ProfileEntry], top: int | None, threshold_ms: float | None) -> str:
    """Console table sorted by TOTAL desc, limited to ``top`` rows."""
    total = sum(e.total_micros for e in entries)
    lines = [
        f"Profile Report ({len(entries)} methods, total {fmt_total(total / 1000)})",
        _PROFILE_HEADER,
    ]
    ordered = sorted(entries, key=lambda e: -e.total_micros)
    shown = ordered[:top] if top is not None else ordered
    lines.extend(_format_row(e, total) for e in shown)
    if threshold_ms is not None:
        lines.append("")
        lines.append(_threshold_line(entries, threshold_ms))
    return "\n".join(lines)


def _method_locations(files: list[Path], project_root: Path) -> dict[str, tuple[str, int]]:
    """Inventory: module-qualified method name -> (relative file, start line).

    Keys carry the module prefix (``pkg.mod.func``) exactly like the
    instrumented ``record()`` calls, so same-named methods in different
    modules attribute unambiguously.
    """
    locations: dict[str, tuple[str, int]] = {}
    for file_path in files:
        source = Path(file_path).read_text(encoding="utf-8")
        rel = _relative_to_root(Path(file_path), project_root)
        prefix = _module_prefix(Path(rel))
        for desc in extract_methods(source, filename=str(file_path)):
            locations.setdefault(f"{prefix}.{desc.name}", (rel, desc.start_line))
    return locations


def _entry(key: str, loc: tuple[str, int], stats: dict) -> ProfileEntry:
    file, line = loc
    return ProfileEntry(
        method=key,
        file=file,
        line=line,
        calls=int(stats.get("calls", 0)),
        total_micros=float(stats.get("totalMicros", 0.0)),
        self_micros=float(stats.get("totalSelfMicros", 0.0)),
        min_micros=float(stats.get("minMicros") or 0.0),
        max_micros=float(stats.get("maxMicros", 0.0)),
    )


def _format_row(entry: ProfileEntry, total_micros: float) -> str:
    pct = entry.total_micros / total_micros * 100.0 if total_micros else 0.0
    mean = _format_mean(entry.mean_micros)
    return (
        f"{fmt_total(entry.total_micros / 1000):>9} {fmt_total(entry.self_micros / 1000):>9} "
        f"{pct:>6.1f}% {entry.calls:>6} "
        f"{mean:>9} {int(entry.max_micros):>8} "
        f"{entry.mean_micros * 60 / 1000:>10.2f}  {entry.method:<24} {entry.file}:{entry.line}"
    )


def _format_mean(mean_micros: float) -> str:
    """Mean with one decimal; ``~`` marks sub-30µs means where instrumentation
    overhead dominates (read CALLS/TOTAL deltas there instead — 0.9.2)."""
    return f"~{mean_micros:.1f}" if mean_micros < 30 else f"{mean_micros:.1f}"


def fmt_total(millis: float) -> str:
    """TOTAL/SELF with adaptive units: at extreme call counts a plain
    ``.2f`` renders walls of digits that blow the column width up — unit
    suffixes (``82.50ms``, ``13.89s``, ``22.50m``, ``13.89h``) stay compact
    at any magnitude (0.9.5)."""
    if millis < 1000:
        return f"{millis:.2f}ms"
    if millis < 60000:
        return f"{millis / 1000:.2f}s"
    if millis < 3600000:
        return f"{millis / 60000:.2f}m"
    return f"{millis / 3600000:.2f}h"


def _threshold_line(entries: list[ProfileEntry], threshold_ms: float) -> str:
    count = sum(1 for e in entries if e.total_micros / 1000 > threshold_ms)
    if count:
        return f"Threshold: {threshold_ms:.2f}ms — {count} method{'s' if count > 1 else ''} exceed"
    return f"Threshold: {threshold_ms:.2f}ms — all methods OK"


def _exceeds_threshold(entries: list[ProfileEntry], threshold_ms: float) -> bool:
    return any(e.total_micros / 1000 > threshold_ms for e in entries)


def _worst_ms(entries: list[ProfileEntry]) -> float:
    return max((e.total_micros / 1000 for e in entries), default=0.0)


def _write_reports(
    entries: list[ProfileEntry], threshold_ms: float | None, project_root: Path
) -> None:
    """Write the full (untruncated) text and JSON reports to ``profile-reports/``."""
    out_dir = project_root / _REPORTS_DIR
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    text = format_report(entries, top=None, threshold_ms=threshold_ms)
    (out_dir / f"profile-{stamp}.txt").write_text(text + "\n", encoding="utf-8")
    payload = {
        "generated": stamp,
        "totalMicros": sum(e.total_micros for e in entries),
        "methods": [_entry_dict(e) for e in entries],
    }
    (out_dir / f"profile-{stamp}.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def _entry_dict(entry: ProfileEntry) -> dict:
    return {
        "method": entry.method,
        "file": entry.file,
        "line": entry.line,
        "calls": entry.calls,
        "totalMicros": entry.total_micros,
        "totalSelfMicros": entry.self_micros,
        "minMicros": entry.min_micros,
        "maxMicros": entry.max_micros,
    }


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py profile",
        description="Run tests against instrumented source and report per-method timing.",
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument("--name", help="only run tests matching this pattern (pytest -k)")
    parser.add_argument(
        "--threshold", type=float, help="exit 2 when any method's total exceeds this (ms)"
    )
    parser.add_argument(
        "--top", type=int, default=20, help="limit console table rows (default: 20)"
    )
    parser.add_argument(
        "paths", nargs="*", help="explicit files/dirs to instrument (default: normal selection)"
    )
    return parser


# Source of the collector module injected into the instrumented copy.
# Flush design (upstream ad4f27b9f14d, observer-effect fix): each WRITER
# (test process; threads inside it share one shard) owns a private shard
# file `<output>.<shardId>.json` holding its CUMULATIVE stats, replaced
# atomically on every flush. Shards never read other writers' data, so
# concurrent writers cannot lose each other's updates by construction;
# the runner merges all shards at the end.
COLLECTOR_SOURCE = '''\
"""Injected profiling collector (generated by `crap4py profile` — do not edit)."""

import atexit
import json
import os
import threading
import time

_STATS = {}
_CALLS = 0
_FLUSH_SEQ = 0
# Throttle anchor (upstream: a Stopwatch started at construction) — at
# most one outermost flush per _FLUSH_MS interval from process start.
_LAST_FLUSH = time.monotonic()
# One call stack per thread: concurrent test threads must not interleave
# frames (Dart keeps one global stack — its runs are single-threaded).
_LOCAL = threading.local()
# Threads share _STATS and this process's shard file; the lock spans the
# stats update and the whole snapshot+write so two threads flushing at
# once cannot interleave writes. Cross-PROCESS writers never touch each
# other's files — separate shards (upstream needed no lock: single-
# threaded Dart, but its per-isolate shards are the same design).
_LOCK = threading.Lock()
_SHARD_ID = os.environ.get("CRAP_PROFILE_SHARD") or "%d-%d" % (time.time_ns(), os.getpid())


def _env_int(name, fallback):
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return fallback


# CRAP_PROFILE_FLUSH_MS (default 0): throttle outermost flushes to at most
# one per interval — an OPT-IN trade that keeps outermost-heavy loops
# I/O-light but loses the tail after the last throttled exit unless an
# explicit flush() runs. CRAP_PROFILE_FLUSH_EVERY (default 100000): the
# backstop interval bounding loss when the stack is stuck non-empty.
_FLUSH_MS = _env_int("CRAP_PROFILE_FLUSH_MS", 0)
_FLUSH_EVERY = _env_int("CRAP_PROFILE_FLUSH_EVERY", 100000)


def _stack():
    try:
        return _LOCAL.frames
    except AttributeError:
        _LOCAL.frames = []
        return _LOCAL.frames


def enter(key):
    _stack().append({"key": key, "start": time.perf_counter(), "child": 0.0})


def exit(key):
    """Records inclusive time for the call and self time (inclusive minus
    nested instrumented calls that completed while the frame was open).
    NESTED EXITS NEVER TOUCH THE FILESYSTEM — the outermost exit of a call
    tree is the only flush point (always, by default: it is the only
    reliable delivery point for a process that dies after its last
    instrumented call), plus the every-_FLUSH_EVERY-calls backstop for
    stuck stacks. The write is O(#methods), never O(#calls)."""
    global _CALLS
    stack = _stack()
    if not stack:
        return
    frame = stack.pop()
    inclusive = (time.perf_counter() - frame["start"]) * 1e6
    self_micros = max(0.0, inclusive - frame["child"])
    outermost = not stack
    if not outermost:
        stack[-1]["child"] += inclusive
    with _LOCK:
        s = _STATS.get(key)
        if s is None:
            s = _STATS[key] = {
                "calls": 0, "totalMicros": 0.0, "totalSelfMicros": 0.0,
                "minMicros": None, "maxMicros": 0.0,
            }
        s["calls"] += 1
        s["totalMicros"] += inclusive
        s["totalSelfMicros"] += self_micros
        if s["minMicros"] is None or inclusive < s["minMicros"]:
            s["minMicros"] = inclusive
        s["maxMicros"] = max(s["maxMicros"], inclusive)
        _CALLS += 1
        due = outermost or _CALLS % _FLUSH_EVERY == 0
    if not due:
        return
    if outermost and _FLUSH_MS > 0 and time.monotonic() - _LAST_FLUSH < _FLUSH_MS / 1000.0:
        return  # throttled: documented bounded tail loss, flush() still delivers
    flush()


def flush():
    """Cumulative snapshot of THIS process only — the shard file is private
    to us, so replacing it wholesale is race-free (no read-merge of other
    writers' data, no lost updates). O(#methods)."""
    global _FLUSH_SEQ, _LAST_FLUSH
    with _LOCK:
        path = os.environ.get("CRAP_PROFILE_OUTPUT")
        if not path or not _STATS:
            return
        snapshot = dict(_STATS)
        # Diagnostic flush counter (a `#`-prefixed key; the runner merge
        # skips it) so tests and debugging can observe shard writes. Only
        # advanced on a successful write so a failed flush retries the
        # same number.
        snapshot["#flushes"] = {"calls": _FLUSH_SEQ + 1}
        shard_path = "%s.%s.json" % (path, _SHARD_ID)
        tmp_path = "%s.tmp%d" % (shard_path, _FLUSH_SEQ)
        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(snapshot, fh)
            os.replace(tmp_path, shard_path)
        except OSError:
            return  # best effort — the next checkpoint retries
        _FLUSH_SEQ += 1
        _LAST_FLUSH = time.monotonic()


atexit.register(flush)
'''
