# crap4py — Specification

A Python port of `crap4java` / `crap4dart`. Computes the **CRAP
(Change Risk Anti-Patterns) metric** for Python source by combining
cyclomatic complexity (stdlib `ast`) with line coverage (coverage.py JSON).

## 1. Purpose

Flag methods that are both complex and poorly tested — the highest-risk code
for changes. CRAP rises sharply when complexity is high *and* coverage is low.

## 2. Scope

**In scope:** `.py` files parsed by the standard library `ast` module;
coverage from a `coverage.py` JSON report; a CLI producing a tabular report
and a pass/fail exit code.

**Out of scope (see §11):** TypeScript/JSX, notebook (`.ipynb`) analysis,
branch coverage, non-coverage.py formats, IDE integration.

## 3. CLI

```
crap4py                      Analyze all .py under src/ (else .).
crap4py --changed            Analyze git-changed .py files only.
crap4py <path>...            Analyze explicit files/dirs (dirs expand to .py).
crap4py --help               Print usage; exit 0.
crap4py --coverage <path>    Override the coverage file (default: coverage.json).
crap4py --threshold <num>    Override the CRAP threshold (default: 8.0).
crap4py --run-tests          Run tests under coverage, then analyze.
```

`--changed` is mutually exclusive with explicit paths (usage error → exit 1).
Unknown flags → usage error (exit 1).

## 4. File selection

- **Default:** walk `src/` if it exists, else `.`; collect `*.py`.
- **Excluded:** `test_*.py`, `*_test.py`, `conftest.py`, and anything under
  `__pycache__/`, `.venv/`, `venv/`, `*/site-packages/*`, `build/`, `dist/`.
- **`--changed`:** `git -C <root> status --porcelain`; keep `.py` paths.
- **Explicit paths:** files kept as-is; directories expanded to `.py` files
  (same exclusions). Result de-duplicated and sorted for determinism.

If no analyzable files are found, print `No Python files to analyze.` and
exit 0.

## 5. Coverage

Input: a `coverage.py` JSON report. Relevant structure:

```json
{
  "files": {
    "rel/path.py": {
      "executed_lines": [1, 2, 3],
      "missing_lines": [4, 5]
    }
  }
}
```

- Absolute coverage paths are relativized against the project root; paths
  outside the root are ignored.
- If the coverage file is missing, a warning is printed to stderr and all
  methods get `N/A` coverage and CRAP.
- **Per-method attribution:** within `[start_line, end_line]`,
  `coverage = |executed ∩ range| / |(executed ∪ missing) ∩ range|`.
  If the range contains no executed or missing lines, coverage is `N/A`.

## 6. Python parsing

- The standard library `ast` module parses each file.
- All `ast.FunctionDef` and `ast.AsyncFunctionDef` nodes (at any nesting
  depth) are collected as methods.
- A method's qualified name:
  - `Class.method` when immediately inside a class,
  - `parent.inner` when immediately inside another function,
  - bare `name` at top level.
- `__init__` is a normal method (Python convention — included).
- Line range: `node.lineno` … `node.end_lineno` (1-based, inclusive).

## 7. Cyclomatic complexity

Base `1`, then `+1` per decision point:

| Construct                              | `ast` node                                |
|----------------------------------------|-------------------------------------------|
| `if`                                   | `ast.If`                                  |
| `for` / `async for`                    | `ast.For` / `ast.AsyncFor`                |
| `while`                                | `ast.While`                               |
| `except`                               | `ast.ExceptHandler`                       |
| ternary `x if c else y`                | `ast.IfExp`                               |
| `match`/`case`                         | `ast.match_case` (3.10+)                  |
| `and` / `or`                           | `ast.BoolOp` → `len(values) − 1`          |
| comprehension `for` clauses            | `len(generators)` on `ListComp`/`SetComp`/`DictComp`/`GeneratorExp` |

Lambda bodies count toward the enclosing method (consistent with the
Java/Dart default). Nested **named** function defs are skipped when computing
the parent's complexity — they are reported as their own methods.

## 8. Formula

```
CRAP = CC² × (1 − coverage)³ + CC
```

- `CC` is the integer cyclomatic complexity (≥ 1).
- `coverage` is the fraction in `[0.0, 1.0]`.
- If coverage is `None`, CRAP is `None` (shown as `N/A`).

Verified edge cases:

| CC | coverage | CRAP     |
|----|----------|----------|
| 5  | 1.0      | 5.0      |
| 5  | 0.0      | 30.0     |
| 8  | 0.45     | 18.648   |
| 3  | null     | null     |

## 9. Report

Printed to stdout:

```
CRAP Report
===========
Method                         File                                  CC    Cov%     CRAP
----------------------------------------------------------------------------------------
risky                          sample.py                              5    0.0%     30.0
...
empty                          mod.py                                 2      N/A      N/A

Max CRAP: 30.0 (threshold 8.0) — FAILED
```

- Sorted by CRAP descending; `N/A` (null) entries last.
- Ties broken by file (asc), then method name (asc) for deterministic output.
- `Cov%` rendered as a percentage with one decimal (e.g. `45.0%`) or `N/A`.
- `CRAP` rendered with one decimal or `N/A`.
- When all CRAP values are `N/A`, max is treated as `0.0` (verdict `passed`).

## 10. Threshold & exit codes

Exit `2` when `max(numeric CRAP) > threshold`, with
`CRAP threshold exceeded: <max> > <threshold>` on stderr.

| Code | Meaning                                                          |
|------|------------------------------------------------------------------|
| `0`  | Success (max CRAP ≤ threshold, or empty selection).             |
| `1`  | Usage error: bad flags, `--changed` + paths, unreadable source. |
| `2`  | CRAP threshold exceeded.                                         |

## 11. `--run-tests`

Runs `coverage run -m pytest`; on failure (including pytest not being
installed) falls back to `coverage run -m unittest discover`. Then runs
`coverage json -o coverage.json`. On total failure, prints to stderr and
exits 1.

## 12. Non-goals

- No runtime dependencies (stdlib only).
- No branch coverage — line coverage only (matches the cross-port contract).
- No mutation testing, no badges, no IDE plugins.
- No support for non-`.py` files or non-`coverage.py` formats.
