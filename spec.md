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
crap4py profile [opts] [p..] Run tests against instrumented source; report timings.
crap4py file-naming [path]   Check source file names for mechanical names.
crap4py nesting [path]       Check functions for nesting deeper than 5 levels.
crap4py class-size [path]    Check classes for >25 methods or WMC >80.
crap4py weight-of-class [p.] Check classes for public data weight >0.33.
crap4py unused-code [path]   Check for unused private module declarations.
crap4py unused-files [path]  Check for source files never imported.
crap4py banned-imports [..]  Enforce --from/--forbid import boundaries.
crap4py magic-constants [..] Flag hex colors outside constants and repeated literals.
crap4py test-assertions [..] Flag test bodies without assertion calls.
crap4py folder-structure [..] Flag package dirs with loose .py files directly.
crap4py skill                Print the crap4py profiling skill.
crap4py --help               Print usage; exit 0.
crap4py --coverage <path>    Override the coverage file (default: coverage.json).
crap4py --threshold <num>    Override the CRAP threshold (default: 8.0).
crap4py --run-tests          Run tests under coverage, then analyze.
```

`--changed` is mutually exclusive with explicit paths (usage error → exit 1).
Unknown flags → usage error (exit 1).

Subcommands (`profile`, `skill`, `file-naming`, `nesting`, `class-size`,
`weight-of-class`, `unused-code`, `unused-files`, `banned-imports`,
`magic-constants`, `test-assertions`, `folder-structure`) are
recognized only as the **first** argument; anything else (flags, paths) is
analyzed as above. Each subcommand parses its own options; unknown subcommand
options → usage error (exit 1).

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
- If the coverage file is missing, a warning is printed to stderr (with a
  hint naming the generation commands — `coverage run -m pytest &&
  coverage json` or `crap4py --run-tests`; crap4dart 0.8.7) and all
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
- Ties broken by file (asc), then method name (asc) for deterministic
  output — N/A rows never shuffle between runs (Python's sort is stable
  and this key is a total order, satisfying the crap4dart 0.8.7
  stable-N/A-ordering fix without a line tie-break).
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
| `2`  | CRAP threshold exceeded; profile `--threshold` exceeded; gate-subcommand violations (§12–§19). |

## 11. `--run-tests`

Runs `coverage run -m pytest`; on failure (including pytest not being
installed) falls back to `coverage run -m unittest discover`. Then runs
`coverage json -o coverage.json`. On total failure, prints to stderr and
exits 1.

## 12. `file-naming`

```
crap4py file-naming [paths...]
```

Flags mechanical source file names — the residue of splitting code without a
domain boundary. File selection defaults to the normal rules (§4); explicit
paths expand the same way. Test files (`test_*.py`, `*_test.py`,
`conftest.py`) and anything under a `test/`/`tests/` directory are skipped.

Two rules, both matched on the lowercased stem (basename without `.py`):

- **Generic stems** — exact match against `common`, `core`, `general`,
  `helper`, `helpers`, `misc`, `shared`, `stuff`, `temp`, `tmp`, `types`,
  `util`, `utils`, `utilities`, `utility`, `various` →
  `generic name "X.py" — split by domain instead of accumulating unrelated declarations`
- **Numeric suffix** — regex `[a-z_][0-9]+$` →
  `numeric suffix in "X.py" — split by domain instead of numbered parts (batch1, part2, v2 ...)`
  unless the stem is in the allowed list of technical terms where digits
  carry meaning (`aes256`, `base64`, `crc32`, `sha256`, `utf8`, ... — the
  upstream `defaultAllowedStems` list, hardcoded).

Output: one line per violation (`<relpath>: <message>`), then a summary —
`N/M files with mechanical names` or `M files have domain-meaningful names`.
Exit `2` iff any violations, else `0`.

## 13. `nesting`

```
crap4py nesting [paths...]
```

Fails functions whose deepest control-flow nesting exceeds 5 levels —
port of the crap4dart `nesting` gate. The function body block is level 1;
`if`/`elif`, `for`, `while`, `with`, `match`/`case` blocks and `try`
bodies (with `else`/`finally`) add one level, and each `except` handler
adds one more (its block sits inside the `try`). Nested named function
defs are their own methods and do not add to the parent. Test files are
skipped (§4 rules).

Output: `<file>:<line>: <function> nesting depth <n> > 5` per violation,
then a summary line. Exit `2` iff any violations, else `0`.

## 14. `class-size`

```
crap4py class-size [paths...]
```

Fails classes with more than 25 concrete methods (direct `def`/`async def`
in the class body) or a weighted-methods sum — total cyclomatic complexity
over all methods, counted per §7 — above 80. Port of the crap4dart
`class_size` gate. Output: `<file>:<line>: class <Name>: 26 methods
(max 25), weighted methods 85 (max 80)` plus a summary. Exit `2` iff
violations.

## 15. `weight-of-class`

```
crap4py weight-of-class [paths...]
```

Fails classes whose share of public data among public instance members
exceeds 0.33 — port of the crap4dart `weight_of_class` gate, adapted to
Python's lack of field declarations:

- **public fields** — distinct public `self.<attr>` assignment targets
  across all instance methods (no leading underscore; usually set in
  `__init__`)
- **public members** — public fields + public instance methods (public
  name, not `static`/`classmethod`)

Private classes (leading `_`) and classes without public fields are never
flagged. Output: `<file>:<line>: class <Name> data weight 0.67 (2 public
fields of 3 public members) > 0.33` plus a summary. Exit `2` iff
violations.

## 16. `unused-code`

```
crap4py unused-code [paths...]
```

Flags module-level private declarations — `_functions`, `_classes`,
`_x = ...` / `_x: T = ...` assignments; dunder names like `__version__`
are conventionally public and never flagged — whose identifier never
appears anywhere else in the module (references counted lexically on the
`ast`). Test files are skipped entirely (declarations and references).
Port of the crap4dart `unused_code` gate, scoped per module.

Output: `<file>:<line>: '<name>' is never referenced` plus a summary.
Exit `2` iff findings.

The crap4dart 0.7.1 fix (declaring a private declaration must not strip
its name from the reference set — cross-class private access within one
library was flagged) does not apply as a bug here: the port never removes
declared names from the lexical reference set, so same-module cross-class
access counts; a regression test pins this.

**Partial selection:** an explicit path list makes the check skip
(`unused-code: not meaningful for a partial selection`) with exit `0` —
a partial file set cannot know whether a name is used elsewhere
(crap4dart 0.5.1 behavior).

## 17. `unused-files`

```
crap4py unused-files [paths...]
```

Flags non-test source files never imported by any other analyzed
non-test file. Imports resolve to project files: relative imports
against the importing file's directory, absolute/dotted imports against
the package root (`src/` when present, else the project root); stdlib
and external imports never resolve. `__init__.py` and `__main__.py` are
exempt. Test files do not count as importers. Python's re-exports —
`from .impl import thing` in a package's `__init__.py` — are ordinary
`from` imports and already count toward the graph (the crap4dart 0.7.1
`export` fix; verified by a regression test).

Output: `<file>: never imported by any analyzed source file` plus a
summary. Exit `2` iff findings. **Partial selection skips** as in §16
(reachability over a partial set yields false positives).

## 18. `banned-imports`

```
crap4py banned-imports [--from GLOB --forbid GLOB [--message MSG]]... [paths...]
```

Enforces architectural import boundaries — port of the crap4dart
`banned_imports` gate. `--from`/`--forbid` pairs are zipped in CLI order
(`argparse` append; unequal counts → usage error, exit `1`), each with an
optional `--message`. For every file whose project-relative path matches
a rule's `from` glob (fnmatch over `/`-separated paths), each import
whose raw dotted name — or, for imports resolving into the project, its
project-relative path — matches the rule's `forbid` glob is a violation;
the optional message is appended. The first matching rule per import is
reported. With no rules the command passes and says so.

Output: `<file>:<line>: import '<target>' is banned — <message>` plus a
summary. Exit `2` iff violations.

The crap4dart 0.8.6 perf fix (compile globs to regexes once per pattern
instead of per file) needs no port: `fnmatch.fnmatch` already caches
compiled patterns per pattern in the standard library.

**Adaptation note (§12–§19):** crap4dart 0.5.0's gate-framework features —
`severity` (`error`/`warning`), `ignorable`/`crap:ignore` suppression,
per-path `entries` threshold overrides, yaml config and baselines — are
not ported: ports have no config system, so thresholds are the upstream
defaults (5 / 25+80 / 0.33 / 3+4) and each gate is a standalone subcommand.

## 19. `magic-constants`

```
crap4py magic-constants [paths...]
```

Flags magic literals — port of the crap4dart 0.6.0 `magic_constants`
gate, including the 0.7.2–0.8.4 precision fixes. Two checks: (a) hex
color integer literals (`0xRRGGBB` / `0xAARRGGBB`, matched on the raw
source segment) outside named-constant declarations — Python's const
convention is a module- or class-level assignment to ALL_CAPS name(s)
(leading underscore allowed), and the lines spanned by its value are
exempt (the full initializer subtree — nested calls, containers,
expressions); (b) numeric (`int`/`float`, raw lexeme) and string
literals (value; adjacent strings are already merged into one
`Constant`) whose value is ≥4 characters and repeats ≥3 times in one
file — every occurrence is reported, and occurrences on constant lines
are exempt here too (0.8.4). `bool`/`None` are never counted, and
f-strings (`JoinedStr`) are skipped (interpolated values are not
constants).

Strings in identifier positions are skipped (protocol identifiers, not
magic constants): dict keys (`{"theme": 1}`), index expressions
(`obj["key"]`), and match-case literal patterns (`case "ready":`).
Call-argument strings still count.

Output: `<file>:<line>: hex color outside a constant declaration` and
`<file>:<line>: literal <value> repeats N times — extract a named
constant`, plus a summary. Exit `2` iff violations, `1` on usage errors.

Also not ported from crap4dart 0.5.2–0.6.1: the 0.5.2 profile part-of
fix (Dart-only), the 0.6.0 baseline/severity/config knobs (no gate
framework — adaptation note above) and the 0.6.1 internal constants
refactor (no behavior change).

## 20. `profile`

```
crap4py profile [--name <pattern>] [--threshold <ms>] [--top <N>] [paths...]
```

Source-instrumentation profiler (port of the crap4dart profiler):

1. Copies the project to `.crap_profile_temp/` (venv, `.git`, caches and
   `profile-reports/` skipped).
2. Rewrites every function/method body of the selected sources (default:
   normal §4 selection) via the stdlib `ast` module as
   `t0 = perf_counter(); try: <body> finally: record(key, (perf_counter() - t0) * 1e6)`.
   The key is the **module-qualified** method name — `<module path>.` +
   the §6 qualified name (e.g. `pkg.mod.run`, nested `pkg.mod.run.inner`)
   — so same-named methods in different modules (every gate module
   defines `run`) never merge into one timing row or mis-attribute
   (0.9.2-era attribution fix).
3. Injects `_crap_collector.py`, aggregating `(calls, totalMicros, minMicros,
   maxMicros)` per key and merging into `.crap_profile.json` via an
   atomic rename so several test processes combine. Temp file names carry
   the worker pid; the flush reader retries once around a concurrent
   rename; records flush every 5 calls (a crashed worker keeps everything
   it flushed; successfully flushed records are cleared, so nothing is
   double-counted) and once more at process exit (0.9.2).
4. Runs `python -m pytest [-k <pattern>]` in the copy (fallback:
   `python -m unittest discover [-s tests] [-k <pattern>]`); a failing run
   warns on stderr but any flushed timings are still reported. The test
   commands always run inside the temp copy (cwd), never against the
   original sources.
5. Attributes timings to the method inventory of the instrumented set;
   unmatched keys are ignored. Temp dir removed afterwards.

Console table sorted by TOTAL desc, limited to `--top` rows (default 20):

```
TOTAL(ms) | % | CALLS | MEAN(µs) | MAX(µs) | @60fps(ms) | METHOD | FILE:LINE
```

(`%` = share of total time; `@60fps` = mean × 60 in ms; a `~` prefix on
`MEAN` marks sub-30µs means, where instrumentation overhead dominates —
read CALLS/TOTAL deltas there instead.) The full report is also written
to `profile-reports/profile-<timestamp>.txt` and `.json`.

Exit `2` when any method's total exceeds `--threshold` ms (default: off).

Not ported from 0.9.2's `9f9688e`: remapping positional **test** paths
into the temp copy (the port's positional paths select sources to
instrument, and the test runner already executes from the temp copy), and
parsing the full source set for attribution (the port instruments exactly
the selected set; module-qualified keys above make its inventory
unambiguous). Upstream's flutter_test pending-timer workaround (no 1s
flush timer) has no Python analog — flushing rides `atexit`.

Skipped from upstream: `--tags`/`--exclude-tags` (no tag concept in
pytest/unittest) and config-file options (ports have no config system).

## 21. `test-assertions`

```
crap4py test-assertions [--min N] [paths...]
```

Flags `test_*` functions and methods — unittest `TestCase` methods and
pytest-style functions, which share the naming convention — whose bodies
contain fewer than `--min` (default 1) assertion signals. Port of the
crap4dart 0.7.x `test_assertions` gate: a test without assertions
compiles, runs green and verifies nothing.

Counted signals (Python language map):

- bare `assert` statements (the main signal),
- `self.assert*` / `self.fail` calls (`assertEqual`, `assertRaises`,
  `fail`, ... — includes `with self.assertRaises(...)`),
- `raises` calls (`pytest.raises`, `from pytest import raises`) and bare
  `fail`.

File selection: default walks `src/` (else `.`) for test files
(`test_*.py`, `*_test.py`, `conftest.py`, anything under `test`/`tests`);
explicit paths keep only the test files among them (directories are
walked for test files).

Output: `<file>:<line>: '<Class.test_name>' has 0 assertion(s) — a test
without assertions verifies nothing` plus a summary (`N/M tests without
assertions` / `M tests assert their expectations`). Exit `2` iff
violations, `1` on usage errors.

## 22. `folder-structure`

```
crap4py folder-structure [--dir DIR]... [--max N]
```

Flags directories containing more than `--max` (default 0) `.py` files
**directly** (non-recursive) — a flat-file sprawl that should be
organized into feature subpackages. Port of the crap4dart 0.7.x
`folder_structure` gate.

Default dirs are the Python analog of Dart's `lib` — the importable
package roots: direct children of the package root (`src/` when present,
else the project root) that contain an `__init__.py`. `--dir` (repeatable,
project-relative) overrides. `__init__.py` and `__main__.py` are package
plumbing and never counted as loose files. Non-existent configured
directories are skipped.

Output: `<dir>: N loose .py files directly in <dir> — group them into
feature packages (max M)` plus a summary (`K directories organized into
packages` / `V directory(ies) with loose-file sprawl`). Exit `2` iff
violations, `1` on usage errors.

## 23. `skill`

`crap4py skill` prints a Python-adapted version of the crap4dart profiling
skill (when to profile, how the instrumentation works, how to read the
report) plus one line on installing it as an agent skill
(`.agents/skills/crap4py-profiling/SKILL.md`). Exit `0`, under ~80 lines.

## 24. Non-goals

- No runtime dependencies (stdlib only).
- No branch coverage — line coverage only (matches the cross-port contract).
- No mutation testing, no badges, no IDE plugins.
- No support for non-`.py` files or non-coverage.py formats.
- No config file and no gate framework (crap4py is a flag-based single
  surface, unlike crap4dart); `--tags`/`--exclude-tags` profiling filters
  are not ported. Gate severity/ignorable/entries/baseline features of
  crap4dart 0.5.0 are likewise not ported (§18 adaptation note).

Not ported from crap4dart 0.7–0.9 (upstream-specific):

- `broken_goldens` / tofu detection and the goldens-guard command —
  Flutter PNG golden files have no Python analog.
- the `external` gate (Checkstyle-XML import) — tied to the Dart/Java
  gate-framework config system.
- `run_tests: true` running the full suite on every analyze by default —
  breaking for a CLI; the port keeps `--run-tests` opt-in (§11).
- pixel-detector tuning commits — depend on `broken_goldens` (above).
