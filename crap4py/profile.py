"""``profile`` subcommand: source-instrumentation profiler.

Copies the project to a temp dir, wraps every function/method body in
``time.perf_counter()`` + ``try/finally`` (stdlib ``ast``), injects a
collector module, runs the test suite against the instrumented copy, and
reports per-function timing. Port of the crap4dart profiler.
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
_IMPORTS = "from time import perf_counter as __crap_pc\nimport _crap_collector as __crap_cc\n"

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
    "profile-reports",
    ".crap_profile_temp",
)

_PROFILE_HEADER = (
    f"{'TOTAL(ms)':>9} {'%':>7} {'CALLS':>6} {'MEAN(µs)':>9} "
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


def instrument_source(source: str, filename: str = "<source>") -> str:
    """Wrap every function body in a timer; returns the source unchanged when
    there is nothing to instrument."""
    tree = ast.parse(source, filename=filename)
    if not _wrap_functions(tree, prefix=None):
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
    """Wrap each descendant function body; key = qualified name (complexity rules)."""
    wrapped = False
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            wrapped = _wrap_functions(child, _qualified(prefix, child.name)) or wrapped
        elif isinstance(child, _FUNCTION_TYPES):
            _wrap_functions(child, child.name)
            child.body = _wrapped_body(_qualified(prefix, child.name), child.body)
            wrapped = True
    return wrapped


def _wrapped_body(key: str, body: list[ast.stmt]) -> list[ast.stmt]:
    """``t0 = pc(); try: <body> finally: record(key, (pc() - t0) * 1e6)``."""
    start = ast.parse("__crap_t0 = __crap_pc()").body[0]
    record = ast.parse(f"__crap_cc.record({key!r}, (__crap_pc() - __crap_t0) * 1e6)").body[0]
    return [start, ast.Try(body=list(body), handlers=[], orelse=[], finalbody=[record])]


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
            target.write_text(instrument_source(source, str(file_path)), encoding="utf-8")
    (temp_dir / _COLLECTOR_NAME).write_text(COLLECTOR_SOURCE, encoding="utf-8")


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
    print(f"Warning: {' '.join(cmd)} exited with code {result.returncode}.", file=sys.stderr)
    for stream in (result.stdout, result.stderr):
        tail = "\n".join(stream.splitlines()[-30:])
        if tail:
            print(tail, file=sys.stderr)


def _load_timings(path: Path) -> dict[str, dict]:
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        print("Warning: no profiling data was produced.", file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


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
        f"Profile Report ({len(entries)} methods, total {total / 1000:.2f}ms)",
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
    """Inventory: qualified method name -> (relative file, start line), first match wins."""
    locations: dict[str, tuple[str, int]] = {}
    for file_path in files:
        source = Path(file_path).read_text(encoding="utf-8")
        for desc in extract_methods(source, filename=str(file_path)):
            locations.setdefault(
                desc.name, (_relative_to_root(Path(file_path), project_root), desc.start_line)
            )
    return locations


def _entry(key: str, loc: tuple[str, int], stats: dict) -> ProfileEntry:
    file, line = loc
    return ProfileEntry(
        method=key,
        file=file,
        line=line,
        calls=int(stats.get("calls", 0)),
        total_micros=float(stats.get("totalMicros", 0.0)),
        min_micros=float(stats.get("minMicros") or 0.0),
        max_micros=float(stats.get("maxMicros", 0.0)),
    )


def _format_row(entry: ProfileEntry, total_micros: float) -> str:
    pct = entry.total_micros / total_micros * 100.0 if total_micros else 0.0
    return (
        f"{entry.total_micros / 1000:>9.2f} {pct:>6.1f}% {entry.calls:>6} "
        f"{entry.mean_micros:>9.1f} {int(entry.max_micros):>8} "
        f"{entry.mean_micros * 60 / 1000:>10.2f}  {entry.method:<24} {entry.file}:{entry.line}"
    )


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
# Multiple test processes merge into one file via atomic rename on flush.
COLLECTOR_SOURCE = '''\
"""Injected profiling collector (generated by `crap4py profile` — do not edit)."""

import atexit
import json
import os
import time

_STATS = {}


def record(key, micros):
    s = _STATS.get(key)
    if s is None:
        s = _STATS[key] = {
            "calls": 0, "totalMicros": 0.0, "minMicros": None, "maxMicros": 0.0,
        }
    s["calls"] += 1
    s["totalMicros"] += micros
    if s["minMicros"] is None or micros < s["minMicros"]:
        s["minMicros"] = micros
    s["maxMicros"] = max(s["maxMicros"], micros)


def flush():
    path = os.environ.get("CRAP_PROFILE_OUTPUT")
    if not path or not _STATS:
        return
    data = _load(path)
    for key, s in _STATS.items():
        _merge_entry(data, key, s)
    _atomic_write(path, data)


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _merge_entry(data, key, s):
    ex = data.setdefault(key, {
        "calls": 0, "totalMicros": 0.0, "minMicros": None, "maxMicros": 0.0,
    })
    ex["calls"] += s["calls"]
    ex["totalMicros"] += s["totalMicros"]
    if s["minMicros"] is not None:
        ex["minMicros"] = s["minMicros"] if ex["minMicros"] is None else min(
            ex["minMicros"], s["minMicros"])
    ex["maxMicros"] = max(ex["maxMicros"], s["maxMicros"])


def _atomic_write(path, data):
    tmp = f"{path}.tmp{os.getpid()}.{time.time_ns()}"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except OSError:
        pass  # best effort


atexit.register(flush)
'''
