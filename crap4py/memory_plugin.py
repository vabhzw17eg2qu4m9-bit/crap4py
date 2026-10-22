"""pytest plugin behind ``crap4py memory``: per-test tracemalloc windows.

Injected by the memory command as ``-p crap4py.memory_plugin`` with
``CRAP4PY_MEMORY_LOG`` pointing at a JSONL file. Loaded any other way the
plugin is inert. Port of the crap4dart per-test GC/alloc attribution
(upstream 3bae178bc0) adapted to Python tooling.

For every test the plugin wraps ``pytest_runtest_protocol`` — setup, call
and teardown, the analog of the upstream testStart..testDone span that
brackets all fixture work — and records one JSONL line:

- ``churn_bytes``: peak live traced memory above the window-start baseline
  (``tracemalloc.reset_peak``); deterministic per code path;
- ``retained_bytes``: signed net traced-memory delta across the window —
  the old-gen-growth analog (positive = the test left memory behind);
- ``gcs``/``gc_collected``: GC events during the window via the
  ``gc.callbacks`` tap — machine-dependent, diagnostics only.

tracemalloc is the primary mechanism because Python's GC tap exposes
collection events and object counts but no byte sizes, and raw-block or
non-container allocations never raise GC events at all — a GC-event-driven
byte metric would be blind. Sampling happens inside the plugin hooks, so
window bounds share one clock: upstream needs dill-index suite mapping,
global clock-offset voting and straggler reattribution only because GC
rows arrive on a separate VM clock, which a same-process tap cannot have.
Collection/import allocations fall between windows and land in a separate
UNATTRIBUTED record (``event: "unattributed"``: GC counts plus the largest
out-of-window peak rise and net delta) — reported by the memory command,
never polluting test/file totals (the upstream phase-2 bucket rule).

Requires pytest >= 7 (``wrapper`` hookimpl).
"""

from __future__ import annotations

import gc
import json
import os
import tracemalloc

import pytest

from .memory import LOG_ENV

_gc_events = 0
_gc_collected = 0
_session_start_bytes = 0
_test_count = 0
# Unattributed bucket: activity OUTSIDE the per-test windows (collection/
# import, session start/finish). churn_bytes = the largest peak rise over
# any out-of-window gap; the rest are run totals minus per-test sums.
_bucket_churn = 0
_gap_baseline = 0
_attr_gcs = 0
_attr_collected = 0
_attr_retained = 0


def _on_gc(phase: str, info: dict) -> None:  # pragma: no cover - gc tap
    global _gc_events, _gc_collected
    if phase == "stop":
        _gc_events += 1
        _gc_collected += info.get("collected", 0) + info.get("uncollectable", 0)


def _record(log_path: str, record: dict) -> None:
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(json.dumps(record) + "\n")


def _log_path(config: pytest.Config) -> str | None:
    return getattr(config, "_crap4py_memory_log", None)


def pytest_configure(config: pytest.Config) -> None:
    global _session_start_bytes
    log_path = os.environ.get(LOG_ENV)
    if not log_path:
        return  # loaded without the memory command: inert
    config._crap4py_memory_log = log_path  # type: ignore[attr-defined]
    tracemalloc.start()
    gc.callbacks.append(_on_gc)
    _session_start_bytes = tracemalloc.get_traced_memory()[0]
    # The first out-of-window gap (collection/import) starts here: reset
    # the peak so it only measures post-configure allocations.
    _gap_baseline = _session_start_bytes
    _bucket_churn = 0
    tracemalloc.reset_peak()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None):
    global _test_count, _bucket_churn, _gap_baseline
    global _attr_gcs, _attr_collected, _attr_retained
    log_path = _log_path(item.config)
    if log_path is None:
        return (yield)
    # Close the preceding out-of-window gap: peak has tracked allocations
    # since the last window's end (or session start), baseline is that
    # same moment's footprint.
    _, gap_peak = tracemalloc.get_traced_memory()
    _bucket_churn = max(_bucket_churn, gap_peak - _gap_baseline)
    baseline = tracemalloc.get_traced_memory()[0]
    gc_before = (_gc_events, _gc_collected)
    tracemalloc.reset_peak()
    try:
        return (yield)
    finally:
        current, peak = tracemalloc.get_traced_memory()
        _test_count += 1
        retained = current - baseline
        _record(
            log_path,
            {
                "event": "test",
                "nodeid": item.nodeid,
                "file": item.nodeid.split("::", 1)[0],
                "churn_bytes": peak - baseline,
                "retained_bytes": retained,
                "gcs": _gc_events - gc_before[0],
                "gc_collected": _gc_collected - gc_before[1],
            },
        )
        _attr_gcs += _gc_events - gc_before[0]
        _attr_collected += _gc_collected - gc_before[1]
        _attr_retained += retained
        # Reopen the out-of-window gap for the stretch after this test.
        _gap_baseline = current
        tracemalloc.reset_peak()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    global _bucket_churn
    log_path = _log_path(session.config)
    if log_path is None:
        return
    final, gap_peak = tracemalloc.get_traced_memory()
    # Close the last gap: whatever happened after the final test.
    _bucket_churn = max(_bucket_churn, gap_peak - _gap_baseline)
    _record(
        log_path,
        {
            "event": "totals",
            "tests": _test_count,
            "gcs": _gc_events,
            "gc_collected": _gc_collected,
            "retained_bytes": final - _session_start_bytes,
        },
    )
    _record(
        log_path,
        {
            "event": "unattributed",
            "gcs": _gc_events - _attr_gcs,
            "gc_collected": _gc_collected - _attr_collected,
            "churn_bytes": _bucket_churn,
            "retained_bytes": (final - _session_start_bytes) - _attr_retained,
        },
    )
    gc.callbacks.remove(_on_gc)
    tracemalloc.stop()
