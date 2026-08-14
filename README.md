# crap4py

[![Quality](https://github.com/vabhzw17eg2qu4m9-bit/crap4py/actions/workflows/quality.yml/badge.svg)](https://github.com/vabhzw17eg2qu4m9-bit/crap4py/actions/workflows/quality.yml)
[![version](https://img.shields.io/github/v/release/vabhzw17eg2qu4m9-bit/crap4py?label=version)](https://github.com/vabhzw17eg2qu4m9-bit/crap4py/releases)
![CRAP](badges/crap.svg)
![coverage](badges/coverage.svg)

**CRAP (Change Risk Anti-Patterns) metric for Python** — a port of
[`crap4java`](https://github.com/crap4java/crap4java) / `crap4dart`.

`crap4py` computes the CRAP score for every function/method in a Python
codebase by combining **cyclomatic complexity** (parsed with the stdlib `ast`
module) and **line coverage** (read from a `coverage.py` JSON report).

> **Zero runtime dependencies.** Pure Python standard library only
> (`ast`, `json`, `argparse`, `subprocess`, `pathlib`, `sys`, `tomllib`).
> Requires Python **>= 3.11** (for `tomllib`).

## The CRAP formula

```
CRAP = CC² × (1 − coverage)³ + CC
```

- `CC` — cyclomatic complexity of the method (integer ≥ 1)
- `coverage` — line-coverage fraction in `[0.0, 1.0]`
- When coverage is unknown, CRAP is reported as `N/A`.

High CRAP = high complexity **and** low coverage → high change risk. Cut CRAP
by adding tests or simplifying the code.

## Install

```bash
pip install crap4py
# or
pipx install crap4py
# or run directly without installing:
python -m crap4py
```

For development:

```bash
git clone <repo>
cd crap4py
python -m venv .venv && . .venv/bin/activate
pip install -e .
```

## Usage

```bash
crap4py                      # analyze all .py under src/ (else .)
crap4py --changed            # analyze git-changed .py files only
crap4py path/to/file.py      # analyze explicit files/dirs
crap4py --threshold 5.0      # override the CRAP threshold (default 8.0)
crap4py --coverage cov.json  # override the coverage file path
crap4py --run-tests          # run tests under coverage, then analyze
crap4py profile              # run tests against instrumented source; report timings
crap4py file-naming          # check source file names for mechanical names
crap4py nesting              # check functions for nesting deeper than 5 levels
crap4py class-size           # check classes for >25 methods or WMC >80
crap4py weight-of-class      # check classes for public data weight >0.33
crap4py unused-code          # check for unused private module declarations
crap4py unused-files         # check for source files never imported
crap4py banned-imports       # enforce --from/--forbid import boundaries
crap4py skill                # print the crap4py profiling skill
crap4py --help               # print usage
```

| Flag / arg         | Description                                                          |
|--------------------|----------------------------------------------------------------------|
| *(none)*           | Analyze all `.py` files under `src/` (else the current directory).   |
| `--changed`        | Analyze only git-changed `.py` files (`git status --porcelain`).     |
| `<path>...`        | Explicit files or directories; directories expand to `.py` files.    |
| `--coverage <p>`   | Coverage file path (default: `coverage.json`).                       |
| `--threshold <n>`  | CRAP threshold (default: `8.0`).                                     |
| `--run-tests`      | Run `coverage run -m pytest` (fallback `unittest`) then `coverage json`. |
| `--help`           | Print usage and exit.                                                |

`--changed` is mutually exclusive with explicit paths.

## `crap4py profile`

A source-instrumentation profiler: copies the project to a temp dir, wraps
every function body in `time.perf_counter()` + `try/finally` (stdlib `ast`),
runs the test suite (pytest, unittest fallback) against the instrumented
copy, and reports exact per-method timing.

```bash
crap4py profile                      # profile the whole suite
crap4py profile --name "parser"      # only run tests matching (pytest -k)
crap4py profile --top 10             # limit console rows (default 20)
crap4py profile --threshold 10.0     # exit 2 when any total exceeds 10ms
```

Console columns: `TOTAL(ms) | % | CALLS | MEAN(µs) | MAX(µs) | @60fps(ms) | METHOD | FILE:LINE`.
Full reports are written to `profile-reports/profile-<timestamp>.txt` and
`.json` regardless of `--top`.

## `crap4py file-naming`

Flags mechanical source file names — generic dumping-ground stems
(`utils.py`, `helpers.py`) and numeric suffixes (`batch1.py`, `report2.py`) —
which usually mean code was split without a domain boundary. Technical stems
where digits carry meaning (`base64.py`, `sha256.py`, `utf8.py`, ...) are
allowed. Prints one line per violation plus a summary; exits `2` iff any
violations.

## Gate subcommands (ported from crap4dart 0.5.x)

Six quality-gate checks, each a standalone subcommand taking optional
explicit paths (default: the normal §4-style selection — `src/` else `.`,
test files skipped). All exit `2` iff violations, `1` on usage errors.

| Subcommand                | Fails when …                                                        |
|---------------------------|---------------------------------------------------------------------|
| `nesting [paths...]`      | a function's control-flow nesting exceeds 5 levels (body = 1).      |
| `class-size [paths...]`   | a class has >25 concrete methods or a complexity sum (WMC) >80.     |
| `weight-of-class [paths…]`| a class's public data share (`self.<attr>` fields ÷ public members) >0.33. |
| `unused-code [paths...]`  | a module-level private name (`_func`, `_x = …`) is never referenced in its module. |
| `unused-files [paths...]` | a non-test file is never imported by any analyzed non-test file (`__init__.py`/`__main__.py` exempt). |
| `banned-imports [--from GLOB --forbid GLOB [--message MSG]]... [paths...]` | a file matching `from` imports something matching `forbid` (raw dotted name or resolved project path). |

`unused-code` and `unused-files` are whole-project checks: given explicit
paths they skip with `not meaningful for a partial selection` (exit `0`),
since a partial file set yields false positives. `banned-imports` pairs
`--from`/`--forbid` in CLI order (unequal counts → exit `1`); with no rules
it passes and says so.

```bash
crap4py banned-imports --from 'ui/**' --forbid 'db/*' --message 'UI must not touch DB'
```

crap4dart's gate-framework features (severity, `ignorable`/ignore comments,
per-path threshold entries, yaml config, baselines) are not ported — ports
are flag-based with upstream default thresholds.

## `crap4py skill`

Prints the profiling skill instructions (when to profile, how the
instrumentation works, how to read the report) and how to install them as an
agent skill.

## Generating coverage data

`crap4py` consumes a standard `coverage.py` JSON report. Generate one with:

```bash
coverage run -m pytest          # or: coverage run -m unittest discover
coverage json -o coverage.json
```

Or let `crap4py` do it: `crap4py --run-tests`.

The JSON shape (from `coverage.py`):

```json
{
  "files": {
    "rel/path.py": {
      "executed_lines": [1, 2, 3],
      "missing_lines": [4, 5],
      "summary": { "covered_lines": 3, "missing_lines": 2, ... }
    }
  }
}
```

Coverage attribution per method: within the method's `[start_line, end_line]`,
`coverage = |executed ∩ range| / |(executed ∪ missing) ∩ range|`. If the
coverage file is missing, or a method has no coverage records in its range,
its coverage and CRAP are `N/A`.

## Report format

```
CRAP Report
===========
Method                         File                                  CC    Cov%     CRAP
----------------------------------------------------------------------------------------
risky                          sample.py                              5    0.0%     30.0
branchy                        sample.py                              3   66.7%      3.3
simple                         sample.py                              1  100.0%      1.0

Max CRAP: 30.0 (threshold 8.0) — FAILED
```

Sorted by CRAP descending; `N/A` entries last.

## Exit codes

| Code | Meaning                                                                |
|------|------------------------------------------------------------------------|
| `0`  | Success (max CRAP ≤ threshold, or no files to analyze).                |
| `1`  | Usage error (bad flags, `--changed` + paths, unreadable source).       |
| `2`  | CRAP threshold exceeded (`CRAP threshold exceeded: <max> > <n>` on stderr); also `profile --threshold` and gate-subcommand (`file-naming`, `nesting`, `class-size`, `weight-of-class`, `unused-code`, `unused-files`, `banned-imports`) violations. |

## Cyclomatic complexity rules

Base `1`, then `+1` for each:

- `if`, `for`, `async for`, `while`, `except` handler, ternary (`x if c else y`)
- `match`/`case` clause (Python 3.10+)
- Each `for` clause in a comprehension (`ListComp`/`SetComp`/`DictComp`/`GeneratorExp`)
- `and`/`or` short-circuits: `len(values) − 1` per `BoolOp`

Lambda bodies count toward the enclosing method. Nested named function defs
are reported as their own methods and do **not** inflate the parent's
complexity. `__init__` is treated as a normal method (Python convention).

## Project layout

```
crap4py/
  pyproject.toml          build + metadata + ruff config (no runtime deps)
  README.md
  LICENSE                 MIT
  spec.md                 Python-adapted specification
  crap4py/
    __init__.py
    __main__.py           python -m crap4py entry
    cli.py                subcommand dispatch + argparse + exit codes
    crap.py               formula + dataclasses
    complexity.py         ast-based cyclomatic complexity
    coverage.py           coverage.py JSON parser + attribution
    analyzer.py           orchestration
    report.py             tabular formatter
    files.py              source finder + git changed + path expansion
    runtests.py           --run-tests driver
    args.py               shared argparse parser (usage errors exit 1)
    profile.py            `profile` subcommand: instrumentation + timing report
    file_naming.py        `file-naming` subcommand
    nesting.py            `nesting` gate subcommand (max depth 5)
    class_size.py         `class-size` gate subcommand (25 methods / WMC 80)
    weight_of_class.py    `weight-of-class` gate subcommand (data weight 0.33)
    unused_code.py        `unused-code` gate subcommand (dead private names)
    unused_files.py       `unused-files` gate subcommand (never imported)
    banned_imports.py     `banned-imports` gate subcommand (import boundaries)
    imports.py            import → project-file resolution (shared by the gates)
    skill.py              `skill` subcommand
  tests/                  stdlib unittest; fixtures/sample.py + coverage.json
```

## Development

A checked-in pre-commit hook runs `crap4py` with its 8.0 threshold on staged
`.py` files and blocks the commit if any exceeds it. Enable it once after
cloning:

```bash
git config core.hooksPath githooks
```

## Running the tests

```bash
python -m unittest discover -s tests -v
```

## License

MIT.
