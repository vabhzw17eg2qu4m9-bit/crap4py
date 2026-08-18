"""``skill`` subcommand: prints the language-adapted profiling skill text."""

from __future__ import annotations

from pathlib import Path

_SKILL = """\
# crap4py Profiling Skill

## When to Use

Use this skill when the user wants to:

- Find performance bottlenecks in Python code
- Measure per-function execution time (microsecond precision)
- Profile a test suite to see which functions are expensive
- Identify frequently-called functions that accumulate cost

## What is crap4py profile?

`crap4py profile` is a source-instrumentation profiler. It copies the
project to a temp dir, wraps every function/method body in
`time.perf_counter()` + `try/finally` (via the stdlib `ast` module), runs
the test suite against the instrumented copy, and reports exact
per-function timing.

Unlike sampling profilers (statistical), this records **exact**
microsecond timing for every single call — no missed fast functions.

## Basic Usage

```bash
crap4py profile                          # profile the whole suite
crap4py profile --name "parser"          # only run tests matching (pytest -k)
crap4py profile path/to/pkg              # instrument only these sources
crap4py profile --top 10                 # limit console rows (default 20)
crap4py profile --threshold 10.0         # exit 2 when a total exceeds 10ms
```

## Reading the Report

Console columns:
`TOTAL(ms) | % | CALLS | MEAN(µs) | MAX(µs) | @60fps(ms) | METHOD | FILE:LINE`

- `TOTAL` — time across all calls; `%` — share of total profiled time
- `MEAN` / `MAX` — average and worst single call; a `~` prefix marks means
  under 30µs where instrumentation overhead dominates — trust CALLS/TOTAL
  there (the method may have gotten cheaper)
- `@60fps` — cost if called 60×/sec (mean × 60); a hot-loop budget proxy

Full reports are saved to `profile-reports/profile-<timestamp>.txt` and
`.json` (the file report is never truncated by `--top`).

## What to Look For

1. **High TOTAL + high CALLS** — called too often; cache or debounce it.
2. **High MEAN** — a single call is expensive; algorithm/data-structure
   issue. MEAN of a cheap-but-optimized method can APPEAR to grow after you
   optimize everything around it: the fixed instrumentation cost weighs
   more. Check CALLS/TOTAL deltas before concluding a regression.
3. **High MAX >> MEAN** — occasional spikes; GC, I/O, or contention.

## How It Works

1. Copies the project to `.crap_profile_temp/` (venv, `.git`, caches skipped)
2. Wraps every analyzed function body in perf_counter + try/finally
3. Injects `_crap_collector.py`, aggregating calls/min/max/total per function
4. Runs `pytest` (fallback: `unittest discover`) inside the temp copy
5. The collector merges timings into `.crap_profile.json` on process exit
6. The temp dir is cleaned up; reports are written to `profile-reports/`

## Limitations

- Test files themselves are not instrumented (only analyzed sources)
- Nested functions are timed individually *and* inside their parent
- Instrumentation adds ~2-3x overhead to the profiled run
"""


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py skill``: print the skill text. Exit 0."""
    print(_SKILL, end="")
    print(
        "Install as an agent skill: save this text to "
        ".agents/skills/crap4py-profiling/SKILL.md in your repository."
    )
    return 0
