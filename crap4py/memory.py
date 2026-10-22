"""``memory`` subcommand: per-test allocation churn / GC attribution.

Port of the crap4dart ``memory`` command (upstream 3bae178bc0, phase 1)
adapted to Python tooling. Runs ``python -m pytest`` with the
``crap4py.memory_plugin`` tap (``PYTHONPATH``-injected, one JSONL log),
parses the log and prints leaderboards plus an optional JSON report.

Mechanism, chosen and documented: tracemalloc windows are the primary
per-test metric; ``gc.callbacks`` counts GC events (the ``--verbose_gc``
analog) for diagnostics only. Python's GC tap has no byte sizes and never
fires for raw-block allocations, so GC-event-driven bytes would be blind.
Semantics differ from upstream honestly: upstream churn is reclaimed
new-gen MB — GROSS volume that flowed through — while Python has no cheap
gross-allocation counter, so churn here is the PEAK LIVE traced footprint
of the test window (deterministic per code; alloc-and-free cycles net out)
and ``retained`` is the signed net delta — the old-gen-growth / leak
suspect analog. Like upstream: GC counts are machine-dependent and never a
metric; churn is a deterministic function of the code.

Attribution is exact where upstream had to infer: pytest nodeids carry the
file, and windows open/close inside the plugin (one clock), so there is no
dill-index suite mapping, no global clock-offset voting, no straggler
reattribution — those solve two-stream problems a same-process tap cannot
have. Windows cover setup+call+teardown; collection/import allocations
count towards run totals only.

Informational only: always exits 0 after a run (even with failing tests);
usage errors — no Python test suite found, no tests executed — exit 1.
unittest-style suites run through pytest; the current interpreter must
have pytest installed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .args import UsageErrorParser

# Shared with the pytest plugin (crap4py.memory_plugin reads this via
# ``from .memory import LOG_ENV``); memory.py must not import the plugin —
# the plugin imports pytest, which the command environment may lack.
LOG_ENV = "CRAP4PY_MEMORY_LOG"

DEFAULT_TOP = 20
FORMAT_CHOICES = ("console", "json")
DEFAULT_FORMAT = "console"
_MB = 1000.0 * 1000.0

# pytest exit codes that still produced per-test records worth reporting
# (0 ok, 1 test failures, 2 interrupted); anything else is flagged.
_REPORTABLE_PYTEST_EXIT = (0, 1, 2)


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py memory [options] [pytest args...]``."""
    parser = _build_parser()
    args, forwarded = parser.parse_known_args(argv)
    if args.top < 1:
        parser.error(f"--top must be >= 1, got {args.top}")
    if not _is_python_project(project_root):
        print(_no_suite_message(project_root), file=sys.stderr)
        return 1
    print("Running pytest under tracemalloc (this may take a while)", file=sys.stderr)
    records, warning = _profile(project_root, [*args.rest, *forwarded])
    report = build_report(records)
    if report["test_count"] == 0:
        message = "memory: no tests executed"
        if warning:
            message += f" ({warning})"
        print(message, file=sys.stderr)
        return 1
    if args.format == "json":
        _print_json(report, args.top)
    else:
        _print_console(report, args.top)
    return 0


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py memory",
        description="Run the test suite under tracemalloc and report per-test "
        "allocation churn (peak live MB) and retained memory.",
    )
    parser.add_argument(
        "--top", type=int, default=DEFAULT_TOP, help=f"leaderboard rows (default {DEFAULT_TOP})"
    )
    parser.add_argument(
        "--format",
        choices=FORMAT_CHOICES,
        default=DEFAULT_FORMAT,
        help="output format (default console)",
    )
    parser.add_argument("rest", nargs="*", help="paths and flags forwarded to pytest")
    return parser


def _profile(project_root: Path, forwarded: list[str]) -> tuple[list[dict], str | None]:
    """Run pytest with the tap; return (records, warning-or-None)."""
    with tempfile.TemporaryDirectory(prefix="crap4py-memory-") as tmp:
        log_path = os.path.join(tmp, "memory.jsonl")
        package_root = str(Path(__file__).resolve().parent.parent)
        env = dict(os.environ, PYTHONHASHSEED="0", CRAP4PY_MEMORY_LOG=log_path)
        env["PYTHONPATH"] = (
            f"{package_root}{os.pathsep}{env['PYTHONPATH']}"
            if env.get("PYTHONPATH")
            else package_root
        )
        cmd = [sys.executable, "-m", "pytest", "-p", "crap4py.memory_plugin", "-q", *forwarded]
        proc = subprocess.run(
            cmd, cwd=project_root, env=env, capture_output=True, text=True, check=False
        )
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        return _load_log(Path(log_path)), _pytest_warning(proc.returncode, proc.stderr)


def _pytest_warning(returncode: int, stderr: str) -> str | None:
    if "No module named pytest" in (stderr or ""):
        return "pytest is not importable by the current interpreter"
    if returncode not in _REPORTABLE_PYTEST_EXIT:
        return f"pytest exited with code {returncode}"
    return None


def _load_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _is_python_project(root: Path) -> bool:
    manifest = any((root / name).is_file() for name in ("pyproject.toml", "setup.py", "setup.cfg"))
    suite = any((root / name).is_dir() for name in ("tests", "test"))
    return manifest and suite


def _no_suite_message(root: Path) -> str:
    return (
        f"memory: no Python test suite found in {root} "
        "(the memory profiler runs `python -m pytest`; unittest-only suites "
        "are not supported yet)"
    )


# --- report ---------------------------------------------------------------------


def build_report(records: list[dict]) -> dict:
    """Aggregate JSONL records into the report shape (files in first-seen order)."""
    tests = [r for r in records if r.get("event") == "test"]
    totals = next((r for r in records if r.get("event") == "totals"), {})
    files: dict[str, dict] = {}
    for record in tests:
        entry = files.setdefault(
            record["file"],
            {
                "file": record["file"],
                "churn_bytes": 0,
                "retained_bytes": 0,
                "gcs": 0,
                "gc_collected": 0,
                "tests": 0,
            },
        )
        for key in ("churn_bytes", "retained_bytes", "gcs", "gc_collected"):
            entry[key] += record.get(key, 0)
        entry["tests"] += 1
    return {
        "test_count": len(tests),
        "gc_count": totals.get("gcs", sum(t.get("gcs", 0) for t in tests)),
        "gc_collected": totals.get("gc_collected", sum(t.get("gc_collected", 0) for t in tests)),
        # Retained totals come from the session record: they include the
        # collection/import allocations the per-test windows never see.
        "total_retained_bytes": totals.get(
            "retained_bytes", sum(t.get("retained_bytes", 0) for t in tests)
        ),
        "files": list(files.values()),
        "tests": tests,
    }


def _active(entry: dict) -> bool:
    return bool(entry.get("gcs") or entry.get("churn_bytes") or entry.get("retained_bytes"))


def _top(entries: list[dict], key: str, label_key: str, top: int) -> list[dict]:
    rows = [
        {
            "label": entry[label_key],
            "churn_bytes": entry.get("churn_bytes", 0),
            "retained_bytes": entry.get("retained_bytes", 0),
            "gcs": entry.get("gcs", 0),
        }
        for entry in entries
        if _active(entry)
    ]
    rows.sort(key=lambda row: (-row[key], row["label"]))
    return rows[:top]


def _mb(num_bytes: float) -> str:
    return f"{num_bytes / _MB:.1f}"


def _signed_mb(num_bytes: float) -> str:
    return f"{round(num_bytes / _MB, 1):+.1f}"


def _print_console(report: dict, top: int) -> None:
    churn_total = sum(f["churn_bytes"] for f in report["files"])
    print(
        f"memory: {report['test_count']} tests, {report['gc_count']} GCs, "
        f"churn {_mb(churn_total)} MB peak live, "
        f"retained {_signed_mb(report['total_retained_bytes'])} MB"
    )
    print()
    _print_table(
        "files by allocation churn (peak live traced MB)",
        _top(report["files"], "churn_bytes", "file", top),
    )
    print()
    _print_table(
        "tests by allocation churn (peak live traced MB)",
        _top(report["tests"], "churn_bytes", "nodeid", top),
    )
    print()
    _print_table(
        "tests by retained growth (leak suspects first)",
        _top(report["tests"], "retained_bytes", "nodeid", top),
    )


def _print_table(title: str, rows: list[dict]) -> None:
    print(f"top {len(rows)} {title}:")
    if not rows:
        print("  (no traced activity)")
        return
    print(f"  {'churn MB':>9}  {'retained MB':>12}  {'GCs':>5}  label")
    for row in rows:
        print(
            f"  {_mb(row['churn_bytes']):>9}  {_signed_mb(row['retained_bytes']):>12}  "
            f"{row['gcs']:>5}  {row['label']}"
        )


def _print_json(report: dict, top: int) -> None:
    churn_total = sum(f["churn_bytes"] for f in report["files"])

    def rounded(row: dict) -> dict:
        return {
            "churn_mb": round(row["churn_bytes"] / _MB, 1),
            "retained_mb": round(row["retained_bytes"] / _MB, 1),
            "gcs": row["gcs"],
        }

    print(
        json.dumps(
            {
                "test_count": report["test_count"],
                "gc_count": report["gc_count"],
                "total_churn_mb": round(churn_total / _MB, 1),
                "total_retained_mb": round(report["total_retained_bytes"] / _MB, 1),
                "files": [
                    {"file": f["file"], "tests": f["tests"], **rounded(f)} for f in report["files"]
                ],
                "tests": [
                    {"nodeid": t["nodeid"], "file": t["file"], **rounded(t)}
                    for t in report["tests"]
                ],
            },
            indent=2,
        )
    )
