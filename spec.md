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
crap4py complexity [opts] [p..] Flag methods over the complexity limit.
crap4py nesting [path]       Check functions for nesting deeper than 5 levels.
crap4py class-size [path]    Check classes for >25 methods or WMC >80.
crap4py weight-of-class [p.] Check classes for public data weight >0.33.
crap4py unused-code [path]   Check for unused private module declarations.
crap4py unused-files [path]  Check for source files never imported.
crap4py banned-imports [..]  Enforce --from/--forbid import boundaries.
crap4py magic-constants [..] Flag hex colors outside constants and repeated literals.
crap4py scattered-constants  Flag literals repeated across 3+ distinct files.
crap4py test-assertions [..] Flag test bodies without assertion calls.
crap4py duplicates [opts] [p..] Flag duplicated lines (percentage) or clone groups (clones).
crap4py mutation [opts] [p..]  Mutation testing: re-run tests per mutated operator.
crap4py similarity [opts] [d..] Scan for near-duplicate files (console/json); exits 0.
crap4py memory [opts] [p..]   Run tests under tracemalloc; per-test churn report, exits 0.
crap4py tuple-signatures [p.] Flag inline tuple annotations in function signatures.
crap4py skill                Print the crap4py profiling skill.
crap4py --help               Print usage; exit 0.
crap4py --coverage <path>    Override the coverage file (default: coverage.json).
crap4py --threshold <num>    Override the CRAP threshold (default: 8.0).
crap4py --run-tests          Run tests under coverage, then analyze.
```

`--changed` is mutually exclusive with explicit paths (usage error → exit 1).
Unknown flags → usage error (exit 1).

Subcommands (`profile`, `skill`, `file-naming`, `complexity`, `nesting`,
`class-size`, `weight-of-class`, `unused-code`, `unused-files`,
`banned-imports`, `magic-constants`, `scattered-constants`, `test-assertions`, `folder-structure`,
`duplicates`, `mutation`, `similarity`, `memory`, `tuple-signatures`) are
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
| `case ... if guard:`                   | +1 more when `match_case.guard` is set    |
| `case a \| b` or-patterns              | `ast.MatchOr` → `len(patterns) − 1`       |
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
| `2`  | CRAP threshold exceeded; profile `--threshold` exceeded; gate-subcommand violations (§12–§19, §21–§22, §24–§26, §29, §30); `reorder --dry-run` when any file would change (§32). |

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
adds one more (its block sits inside the `try`). `elif` chains shall stay
flat: Python parses `elif` as a single `ast.If` in `orelse`, which keeps
the originating `if`'s level, while an `if` after other statements in an
`else` suite keeps adding a level. Nested named function
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

**Adaptation note (§12–§19, §31):** crap4dart 0.5.0's gate-framework
features — `severity` (`error`/`warning`), per-path `entries` threshold
overrides, yaml config and baselines — are not ported: ports have no
config system, so thresholds are the upstream defaults (5 / 25+80 / 0.33
/ 3+4) and each gate is a standalone subcommand. The `ignorable`/
`crap:ignore` suppression IS ported as the hardcoded ignorable registry
(§31).

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
   `enter(key); try: <body> finally: exit(key)` — the collector owns the
   per-call timer on its call stack, so `exit` records inclusive time and
   self time (inclusive minus nested instrumented calls that completed
   while the frame was open — flamegraph semantics). The stack is a
   `threading.local()`: concurrent test threads never interleave frames
   (upstream Dart keeps one global stack — its runs are single-threaded).
   The key is the **module-qualified** method name — `<module path>.` +
   the §6 qualified name (e.g. `pkg.mod.run`, nested `pkg.mod.run.inner`)
   — so same-named methods in different modules (every gate module
   defines `run`) never merge into one timing row or mis-attribute
   (0.9.2-era attribution fix).
3. Injects `_crap_collector.py`, aggregating `(calls, totalMicros,
   totalSelfMicros, minMicros, maxMicros)` per key and merging into
   `.crap_profile.json` via an atomic rename so several test processes
   combine. Temp file names carry the worker pid; the flush reader
   retries once around a concurrent rename; records flush every 5 calls
   and once more at process exit (0.9.2). A flush merges only the
   records accumulated since the last one — `_STATS` is cleared only
   after the atomic write succeeds, so a crashed worker keeps everything
   it flushed and nothing is ever re-added (the 0.9.5 upstream
   delta-flush fix, already correct here since 0.9.2 — `flush()` /
   `_atomic_write()` inside `COLLECTOR_SOURCE`, `crap4py/profile.py`).
   A `threading.Lock` spans the shared-stats update and the flush's
   read-merge-write: Python tests run real threads (upstream's Dart
   collector is single-threaded and needs no lock).
4. Runs `python -m pytest [-k <pattern>]` in the copy (fallback:
   `python -m unittest discover [-s tests] [-k <pattern>]`); a failing run
   warns on stderr but any flushed timings are still reported. The test
   commands always run inside the temp copy (cwd), never against the
   original sources.
5. Attributes timings to the method inventory of the instrumented set;
   unmatched keys are ignored. Temp dir removed afterwards.

Console table sorted by TOTAL desc, limited to `--top` rows (default 20):

```
TOTAL | SELF | % | CALLS | MEAN(µs) | MAX(µs) | @60fps(ms) | METHOD | FILE:LINE
```

(TOTAL = inclusive time across all calls; SELF = total minus nested
instrumented calls that completed while the frame was open — flamegraph
self-time, ranking hot code by actual CPU burn rather than by caller
fan-out. Interleaved `await`s on one thread interleave frames on that
thread's stack: the same single-event-loop limitation upstream accepts.
`%` = share of total time; `@60fps` = mean × 60 in ms; a `~` prefix on
`MEAN` marks sub-30µs means, where instrumentation overhead dominates —
read CALLS/TOTAL deltas there instead.) TOTAL, SELF and the summary
line render time with adaptive units — `82.50ms`, `13.89s`, `22.50m`,
`13.89h` — so extreme call counts keep the columns compact (0.9.5).
The full report is also written to `profile-reports/profile-<timestamp>.txt`
and `.json` (per-method records carry `totalSelfMicros`).

Exit `2` when any method's total exceeds `--threshold` ms (default: off).

Not ported from 0.9.2's `9f9688e`: remapping positional **test** paths
into the temp copy (the port's positional paths select sources to
instrument, and the test runner already executes from the temp copy), and
parsing the full source set for attribution (the port instruments exactly
the selected set; module-qualified keys above make its inventory
unambiguous). Upstream's flutter_test pending-timer workaround (no 1s
flush timer) has no Python analog — flushing rides `atexit`.

The 0.9.3 fix (explicit test paths must be the *only* selectors — the
default `test` directory selector, appended unconditionally upstream, made
an explicit-path run profile the whole suite too) needs no port: the
port's paths never enter the test-runner argv, which is always exactly
the pytest/unittest subcommand plus an optional `-k` filter — there is no
default selector to mix in, and the default §4 source selection applies
only when no paths are given. Regression-pinned: the runner argv is
byte-exact with and without `--name`, and an explicit-path run reports
only that file's methods.

Skipped from upstream: `--tags`/`--exclude-tags` (no tag concept in
pytest/unittest) and config-file options (ports have no config system).

Ported from 0.9.5's `e2ce5e9`: the collector flushes on the outermost
method exit as well as every 5 records — a completed top-level call is a
natural checkpoint, so short runs don't lose their tail (`flush()` is
public in the injected collector and also rides `atexit`). Upstream's
flutter_test pending-timer workaround (no 1s flush timer) has no Python
analog. Also skipped: commit `b789739`'s icon/PNG assets (upstream
branding only — the port's own icon landed separately), and the upstream
version/CHANGELOG bump (release-please owns versions here). The 0.9.5
delta-flush fix needs no port — this collector has merged only unflushed
deltas since 0.9.2 (`flush()` clears `_STATS` only after
`_atomic_write()` succeeds, `crap4py/profile.py` `COLLECTOR_SOURCE`).

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

The 0.9.4 fix (the body is the *second positional* argument of Dart's
`test(...)`, not the last — trailing `skip:`/`timeout:` named args were
mistaken for the body) has no Python analog: unittest/pytest tests are
`def` bodies, never call arguments, and skip metadata arrives as
decorators (`@unittest.skip`, `@pytest.mark.skip`) that sit outside the
body the gate counts. Regression-pinned: skip-decorated tests are still
counted for their real body — the empty variant is flagged, the asserting
variant passes.

## 22. `folder-structure`

```
crap4py folder-structure [--dir DIR]... [--max N]
                         [--cap PATTERN=N]... [--exclude GLOB]...
```

Flags directories containing more than their cap of logical `.py` units
**directly** — a flat-file sprawl that should be organized into feature
subpackages. Port of the crap4dart 0.7.x `folder_structure` gate
(per-pattern caps, glob dirs and stem collapse adapted from upstream
753c3580). Two configuration forms:

- **Classic** (default package roots, `--max` 0): every `--dir` entry —
  a literal path or a glob pattern (`pkg/*`, `packages/**`, pathlib
  `Path.glob` semantics: `*` stays within one path segment, `**`
  recurses and includes the base directory itself) expanding to every
  matching directory — shares one cap.
- **`--cap PATTERN=N`** (repeatable): each pattern's directories get
  their own cap — `--cap 'pkg/core=3' --cap 'pkg/**=30'` — a registry
  folder may hold more than a hot core directory. When any `--cap` is
  present it takes precedence and `--dir`/`--max` are not consulted. A
  directory matched by several patterns uses the **first** pattern's
  cap (flag order).

Counting rules: only a directory's OWN contents count — files in
subdirectories are the organized form and never reach the parent's
budget. Generated siblings of one source file collapse into a single
logical unit — `foo.py`, `foo_pb2.py` and `foo_pb2_grpc.py` share the
stem `foo` and count once (host-generic py generated set: the
protobuf `_pb2`/`_pb2_grpc` suffixes; Dart's `.g.dart`/`.freezed.dart`
analog). `.pyi` stubs are package metadata and never counted as loose
files (so a `.pyi` beside its `.py` collapses by omission). Files
matching `--exclude` (fnmatch on the project-relative path, repeatable)
don't count. Glob expansion never enumerates hidden or build
directories: dot-prefixed names (`.git`, `.venv`, `.mypy_cache`, ...),
`__pycache__`, `build`, `dist`, `venv`, `node_modules` and `*.egg-info`
are skipped.

Default dirs are the Python analog of Dart's `lib` — the importable
package roots: direct children of the package root (`src/` when present,
else the project root) that contain an `__init__.py`. `--dir` (repeatable,
project-relative) overrides. `__init__.py` and `__main__.py` are package
plumbing and never counted as loose files. Non-existent configured
directories are skipped.

Output: `<dir>: N loose .py files directly in <dir> — group them into
feature packages (max M)` plus a summary (`K directories organized into
packages` / `V directory(ies) with loose-file sprawl`). Exit `2` iff
violations, `1` on usage errors (malformed `--cap` values included).

Dogfooding: the gate's own repo passes with `--cap 'crap4py=40'` —
`crap4py/` holds 33 logical units today (35 `.py` files minus the two
dunder plumbing files; no generated siblings yet).

## 23. `skill`

`crap4py skill` prints a Python-adapted version of the crap4dart profiling
skill (when to profile, how the instrumentation works, how to read the
report) plus one line on installing it as an agent skill
(`.agents/skills/crap4py-profiling/SKILL.md`). Exit `0`, under ~80 lines.

## 24. `duplicates`

crap4py duplicates [--threshold N] [--min-tokens N] [--min-lines N]
                   [--ignore-locals] [--ignore-literals]
                   [--ignore-identifiers] [--ignore-imports]
                   [--skip-duplicate-files]
                   [--report percentage|clones]
                   [--exclude GLOB]... [--source PATH]... [paths...]
```

Flags files whose share of duplicated lines exceeds `--threshold` percent
(default 1.0), or — with `--report clones` — reports one violation per
clone group. Port of the crap4dart `duplication` gate (upstream §11.11)
including the dc64e9c per-gate `sources` union and the d6c3ab6 CPD-parity
work (clones mode, `ignore_identifiers`/`ignore_imports`/
`skip_duplicate_files`, token-equality verification, shared-extent
anchoring).

Detection tokenizes every scanned file with the stdlib `tokenize` module,
keeping token lexemes with their source line. Dropped token types — the
Python map of upstream's "comments and synthetic tokens are ignored":
`COMMENT`, `NL`, `NEWLINE`, `INDENT`, `DEDENT`, `ENCODING`, `ENDMARKER`
and `ERRORTOKEN`. A duplicated block is a window of at least
`--min-tokens` (default 50) consecutive lexemes spanning at least
`--min-lines` (default 5) source lines whose token sequence appears at
least twice — within or across files, since every file is indexed
together. Windows are indexed by a Rabin-Karp rolling hash over the
lexeme hashes, mod 2**64 (upstream's jscpd-style scheme). Hash buckets
are only candidates: each bucket of at least two window positions is
verified by actual token equality against the bucket reference (a
Rabin-Karp fingerprint collision no longer reports a false duplicate),
extended to the maximal extent the occurrences share, and clipped to the
common token count; the resulting groups compete in one greedy
longest-first selection that accepts non-overlapping occurrences only
(PMD CPD Match semantics) and marks their tokens.

Three opt-in CPD-parity knobs, all default off. `--ignore-identifiers`
masks every identifier-shaped token to `$ID` before hashing —
structure-only matching that detects clones renamed anywhere; hard
keywords (`if`, `for`, `return`, `and`, `in`, ...) keep their lexeme,
while soft keywords (`match`, `case`, `type`, `_`) and builtins (`len`,
`range`, `print`, ...) occupy identifier slots and mask too. With it on,
per-scope local renaming is redundant. `--ignore-imports` excludes
`import` and from-import statements from tokenization (their AST
line/col ranges are skipped, multi-line parenthesized imports included).
`--skip-duplicate-files` analyzes a file whose masked token stream
equals an earlier file's only once, so a copied file yields one clone
group instead of N×N pairs.

Two opt-in normalizations extend detection to renamed (Type-2) clones,
both default off — with both off, behavior is identical to plain
copy-paste detection. `--ignore-locals` renames function-local
identifiers to `$L1`-style placeholders before hashing: one outermost
scope per `def`/`async def`/`lambda` (nested functions share the
enclosing scope), with each scope's declared names renamed in first-use
order — parameters (except `self`/`cls` receivers), assigned, deleted,
tuple-unpacking and walrus targets, loop and comprehension variables,
`except as` names, match captures, 3.12+ type parameters, and locally
declared defs/classes. Identifiers outside a scope's declared set keep
their lexeme, so the API surface (called methods, types, fields,
imports, globals — including `global`/`nonlocal` redeclarations) stays
visible and swapped locals keep distinct placeholders. Interpolated
(f-)strings turn opaque (`$STR`) because their raw lexeme embeds local
identifiers. `--ignore-literals` replaces string and numeric literals
with `$STR`/`$NUM` placeholders. Detection is a union of two passes: the
raw-lexeme pass always runs alongside the masked one, so exact copies
are never lost when enclosing scopes shift placeholder numbering.

Report modes. `percentage` (the default) keeps per-file semantics: a
file's duplicated-line share is its distinct duplicated-token lines
divided by its total line count; the file violates when the share is
strictly greater than `--threshold`, reported at the first duplicated
line: `<file>:<line>: X.XX% duplicated lines > T%`. Pass summary:
`N files, X.XX% duplicated lines` (aggregate share over the scanned
set); fail summary: `M/N files over T% duplication`. `--report clones`
reports one violation per accepted clone group instead (PMD CPD style):
`<first file>:<first line>: clone: T tokens / L lines, K occurrences:
`f1:l1-l2; f2:l1-l2; f3:l1-l2 (+N more)` — groups sorted longest first,
up to three locations shown. Pass summary: `no clones >= M lines across
N files, X.XX% duplicated lines`; fail summary: `K clone groups (>= T
tokens, >= M lines), X.XX% duplicated lines` — the project-wide
duplicated-line percentage (marked lines over the scanned set's total
lines), the number percentage mode already computes. Files
with fewer than `--min-tokens` tokens never enter the scan (the summary
counts tokenized files only, and with `--skip-duplicate-files` the copy
is skipped the same way). With nothing tokenizable the gate passes with
`no files with enough tokens`. Exit `2` iff violations, `1` on usage
errors.

File selection: like every gate, explicit `paths` (files/dirs, expanded
to `.py`) replace the default scan set — the §4 selection (`src/` else
`.`, test files and generated dirs already excluded — the port's
stand-in for upstream's default `exclude` of generated files and
`test/**`). `--source PATH` (repeatable) unions extra paths into the
scan, resolved against the project root: directories are walked
recursively for `.py` files, `.py` files are taken directly (even test
files), missing paths are skipped silently — the intended mechanism for
cross-module duplication checks while the CRAP analysis stays scoped.
`--exclude GLOB` (repeatable) skips scanned files whose project-relative
path matches the fnmatch glob (`*` crosses `/`). Duplicate detection is
whole-set — every selected file is indexed together, so a block copied
between files is marked in both; use `--source` to widen beyond the
selection. Exit `2` iff violations, `1` on usage errors.

Regression-pinned by `tests/test_duplicates.py`: within-file and
cross-file detection, the `--min-tokens`/`--min-lines` boundaries,
threshold exclusivity (`>` not `>=`), flag round-trips through `run()`
(`--min-tokens`, `--threshold`, `--report`, the CPD-parity booleans),
positional paths selecting the scan set, `--source` union with missing
paths skipped, `--exclude` globs, clones mode (one violation per group,
all locations, three-location truncation, `percentage` as default),
`--ignore-identifiers` structure-only matching, `--ignore-imports`
boilerplate exclusion, and `--skip-duplicate-files` single analysis.
The anchored-group share semantics (token-equality verification,
shared-extent anchoring, non-overlapping acceptance) kill the old
raw-window engine: that test fails against the pre-d6c3ab6 marking.

## 25. `mutation`

```
crap4py mutation [--coverage PATH] [--min-score N] [--max-survivors N]
                 [--timeout-seconds N] [--max-mutants N]
                 [--operator CATEGORY]... [--exclude GLOB]...
                 [--cache/--no-cache] [--test-args ARG]...
                 [--scope-tests/--no-scope-tests] [--diff-base REF] [paths...]
```

Mutation testing, port of the crap4dart `mutation` gate (upstream c865ec3,
adapted from the unclebob/mutator research conclusions). A declarative,
category-tagged operator table is matched on the parsed stdlib `ast` tree;
replacement spans come from AST node positions plus stdlib `tokenize` token
offsets — never regex:

- `arithmetic`: `+`↔`-`, `*`↔`/`, `/`→`//`
- `boundary`: `>`↔`>=`, `<`↔`<=`, `>`↔`<`, `>=`↔`<=`
- `equality`: `==`↔`!=`
- `logical`: `and`↔`or` (one site per adjacent value pair)
- `boolean`: `True`↔`False`
- `constant`: integer `0`↔`1` (by value, any lexeme), `-1`→`0` (whole span)
- `unary`: delete a `not` prefix (the operand stays)

Binary rows match `ast.BinOp`/`ast.Compare` operators; the Python AST gives
operator nodes no positions, so the exact operator token is located as the
single code token (comments, newlines and indentation excluded) between the
operand spans — the tokenize-backed equivalent of upstream's analyzer token
offsets. Asymmetric like upstream: `/` gains `//` as a mutant, but a `//`
expression has no row (Dart `~/`). Each match is a mutation site
`{file, line, method, category, original, mutant, start, end}`.

A site whose containing function has no executed line — or whose line is
recorded as missing when no function contains it (module-level code) — is
`uncovered`: excluded from the score denominator and never executed. Every
covered site is then mutated one at a time (one span replacement at its
exact offsets) and the project's test command re-runs against it with a
per-mutant timeout. A non-zero exit kills the mutant; an import/compile
error is killed the same way, because the test command refuses to run. A
timeout counts as survived: the suite did not kill the mutant
deterministically. The file is restored after every mutant, also on
timeouts and errors. Because CPython validates `.pyc` caches by source
mtime (1-second granularity) and size — table mutants have the same size
and run within the same second — the engine clears regenerable
`__pycache__` directories once per run and executes the suite with
`PYTHONDONTWRITEBYTECODE=1`, so a stale cache can never shadow one mutant
with the previous mutant's bytecode.

The test command is `[sys.executable, -m, pytest]` when pytest is
importable, else `[sys.executable, -m, unittest, discover, -s tests]`
(`-s test` for a `test/` directory) — the port's `dart test`/`flutter test`
switch. The mutation score is `killed / (killed + survived)`; uncovered
sites are excluded. The gate fails when the score is below `--min-score`
(default 0.8) or when more than `--max-survivors` (default 0) mutants
survive, and reports one violation per survived mutant:
`<file>:<line>: survived <category> mutant `<original>` -> `<mutant>`[ in
<method>]`. Without coverage data, without a `tests/` (or `test/`)
directory, or when the test command cannot be spawned, the gate skips
itself (exit 0 with a reason). `--timeout-seconds` (default 60) bounds each
mutant, `--max-mutants` (default 500) caps the executed covered sites
deterministically (file order, then offset), `--operator CATEGORY`
(repeatable, one of `arithmetic boundary equality logical boolean constant
unary`) selects an enabled subset, and `--exclude GLOB` (repeatable,
fnmatch on project-relative paths) skips files. Pass summary:
`score P% (K killed, S survived, U uncovered, C cached)`; the fail summary
appends `survivors by category: <category=N, ...>`. The gate is disabled by
default in the upstream sense: mutation testing never runs during plain
analysis — it executes only when this subcommand is invoked. Exit `2` iff
violations, `1` on usage errors.

Digest-incremental cache (port of upstream 16d9c4c): when `--cache` is on
(default), the gate loads `.crap-mutation-cache.json` from the project root
and binds every covered site inside a function to that function's cache
identity — the key `<rel>#<qualified name>` plus the FNV-1a (64-bit state,
output-masked to 63 bits, 16 hex chars — zero dependencies, stable across
platforms) digest of the function's exact source text. Killed mutants of
digest-unchanged methods are replayed as killed without executing
(`C cached` in the summary); survivors always re-execute, and coverage
gating is re-derived from the fresh report every run. Site offsets are
stored method-relative (relative to the function's start), so an edit in an
earlier method of the same file invalidates only that method's sites. The
envelope carries a meta guard — tool version, sorted enabled operators,
comma-joined `--test-args` — and any mismatch, a version mismatch, a corrupt
file, or a missing file starts from an empty cache (full re-execution).
Methods absent from the scanned scope (deleted or excluded files) are
pruned; sites outside every function are never cached. The merged cache is
persisted after the run; the file is disposable build output and
gitignored. `--no-cache` disables load/merge entirely. `--test-args ARG`
(repeatable) appends arguments to the per-mutant test command — scoping the
suite is the caller's responsibility: a mutant is killed only if the scoped
suite would kill it. Empty `--test-args` values are usage errors (exit 1),
matching upstream's strict validation.

Diff-scoped execution (port of upstream 8228585): when `--diff-base REF`
is given, the gate intersects the candidate sites with the changed lines
BEFORE executing, not merely filters reported violations afterwards: a
site runs only when its containing method intersects the diff (falling
back to the site's own line when no method contains it — module-level
code). The changed lines come from a fixed-argv `git diff --unified=0
REF -- '*.py'` run at the project root (new-side paths → 1-based added
line numbers, the Python equivalent of upstream's `DiffLineMap`; a file
with only deletions maps to an empty set, deleted files are absent).
Sites outside the diff are neither executed nor counted, so a per-PR run
pays only for the mutants of the changed methods. A git failure (unknown
ref, not a repository) is a usage error: `git diff failed: …` on stderr,
exit 1.

Per-mutant test scoping: `--scope-tests` (default off) narrows each
mutant's invocation to the derived test file of the mutated file —
`tests/test_<module>.py` by module stem (`crap4py/mutation.py` →
`tests/test_mutation.py`) — falling back to the full invocation
(`--test-args`) when no candidate test file exists. Under the unittest
runner, scoped file arguments run as dotted modules
(`python -m unittest tests.test_a`: discovery cannot take file paths);
pytest takes the path directly. A scoped invocation is a SUBSET of the
full suite, so a scoped kill is monotone (the full suite kills that
mutant too) and is cached as a regular kill; a scoped survivor — a
timeout included — is NOT monotone, so it is re-confirmed once against
the full invocation, and only the confirmed outcome is reported and
cached. `--scope-tests` is deliberately not part of the cache meta
guard. The summary counts the scoped sites owing confirmation and the
re-confirmed kills: `; test-scoped N (M re-confirmed killed)`.

End-of-run tree integrity check (port of upstream 8b2e8e8): the engine
snapshots every file it is about to mutate before the mutant loop and, in
an outer `finally`, re-verifies the whole tree against those snapshots
even when the loop dies mid-mutant. A per-site restore can be defeated
out-of-process (external write, killed interpreter), so any drift is
re-restored AND surfaced: `INTEGRITY: re-restored after run: <files>` in
the summary plus a stderr warning (`working tree did not restore cleanly …
Treat this run as suspect and re-run`). An empty violation list is the
healthy case and stays silent.

File selection: like every gate, explicit `paths` (files/dirs, expanded to
`.py`) replace the default §4 selection; `--coverage` (default
`coverage.json`) resolves relative to the project root and must be a
coverage.py JSON report (§5).

Regression-pinned by `tests/test_mutation.py`: operator-table rows and the
`//` asymmetry, category filtering, site metadata (file/line/method),
engine mutate-restore (including on runner errors), real-subprocess
survived/killed/timeout/import-error verdicts, uncovered exclusion,
skip conditions, `--max-mutants` cap, `--min-score`/`--max-survivors`
interplay, violation file+line output, and plain-analysis never mutating.
It also pins the 8228585 port: diff-hunk parsing (added lines per new
path, deletions absent), diff-scoped execution (only diff-intersecting
methods execute; sites outside the diff neither executed nor counted;
module-level sites fall back to their own line), `--diff-base` E2E
against a real git repo, the `tests/test_<module>.py` derivation,
fallback to `--test-args` (fallback survivors re-confirmed against the
same invocation), scoped-kill monotonicity, scoped-survivor
re-confirmation (survived and timed-out) with only the confirmed outcome
reported, and the scoped invocation reaching a real subprocess.
`tests/test_mutation_cache.py` pins the digest cache and the integrity
check: cold/warm equivalence (zero runner calls on replay), survivors
always re-executing, method-relative invalidation, operator and
`--test-args` meta-guard wipes, version/corruption resets, pruning of
vanished methods, cross-file keying, module-level sites never cached,
out-of-band mutation self-heal, byte-identical trees on success and
mid-loop exceptions, and `--test-args` scoping/validation — plus the
scope_tests cache interplay: scoped kills cached as regular kills and
replayed with `--scope-tests` off, confirmed scoped kills cached, and a
meta guard identical with and without scoping.

## 26. `complexity`

```
crap4py complexity [--metric M] [--max N] [paths...]
```

Fails methods whose complexity exceeds `--max` (default 10) — port of the
crap4dart `complexity` gate. `--metric` selects the metric: `cyclomatic`
(default; counted per §7) or `cognitive` (SonarSource-style, counted per
§26.1; nesting-weighted — flat `elif` chains stay cheap, nested ones pay
extra). Output: `<file>:<line>: <method> CC=<n> > max <n>` (cognitive
violations read `cognitive=<n>`), then a summary line
(`<m>/<n> methods over their <metric> limit` /
`<n> methods within their <metric> limit`). Exit `2` iff violations.
The CRAP score of the `analyze` command always uses cyclomatic complexity
regardless of this setting.

### 26.1 Cognitive complexity

The score starts at `0` (not a path count), then:

- `+1` per flow break: `if`/`elif`/`else`, `for`/`async for`, `while`,
  `except` handler, every `match` case, ternary `x if c else y`, and each
  *sequence* of identical `and`/`or` operators (`a and b and c` = +1,
  `a and b or c` = +2).
- Structural constructs (everything above except `and`/`or`) pay `1 + depth`
  when they appear inside another structural construct at depth `depth`;
  logical sequences stay flat.
- `elif` chains are flat: each chained `if` increments once at the depth of
  its originating `if`, so `if/elif/elif` scores 3 regardless of chain
  length. Python parses `elif` and `else` + `if` into the same AST, so both
  stay flat; an `if` after other statements in an `else` suite keeps paying.
  Loop `else` suites (no upstream analog) nest like the loop body without an
  extra increment.
- Lambda bodies and comprehension bodies increase nesting depth but do not
  increment themselves. Nested named function defs are skipped entirely,
  mirroring cyclomatic complexity.

## 27. `similarity`

```
crap4py similarity [--format console|json] [--jaccard N] [--containment N]
                   [--min-shingles N] [--include GLOB]... [--exclude GLOB]...
                   [dirs...]
```

File-level near-duplicate detection over any text corpus (markdown, source
code of any language, raw text) — port of the crap4dart `similarity` gate +
command (upstream 2b21966, §11.23). Each scanned file is normalized per its
profile — markdown (`.md`/`.markdown`/`.mdx`) drops fenced code blocks,
code (a 25-extension family) drops comments and import-family lines and
maps literals to placeholders, raw is compared as-is — tokenized
(markdown/raw: 2+ letter words lowercased; code: identifiers lowercased
and literal placeholders), and represented as the set of its 8-token
shingles.

Two files are *similar* when Jaccard(A,B) >= `--jaccard` (default 0.8) OR
containment(A,B) >= `--containment` (default 0.95; intersection over the
smaller set — catches a small file largely copied into a big one). Similar
pairs merge transitively into clusters (union-find with path compression),
and each cluster reports the pairs that built it. Files with fewer than
`--min-shingles` shingles (default 50) are skipped — tiny files carry no
reliable signal and never enter comparison.

Profiles are extension-mapped. The code profile is language-agnostic: the
generic engine strips block/line comments (`//`, `#`, `--`, `;`) and
import-family lines (`import`/`export`/`part`/`part of`/`library`,
`from`/`require`/`require_once`/`using`/`package`/`include`, shebangs),
then tokenizes identifiers (lowercased), quoted strings (`S`) and numbers
(`N`). `.py` files instead go through the stdlib `tokenize`/`ast` path
with the same token alphabet: comments dropped natively,
`import`/from-import statements dropped (multi-line parenthesized
included), strings/numbers → `S`/`N`, names lowercased, f-strings opaque;
a file the tokenizer cannot read falls back to the generic engine.

Selection: positional `dirs` (default `.`) scanned recursively; reported
paths stay project-relative (unlike upstream there is no scan-root switch)
and missing directories are skipped silently. `--include` globs (default
`**/*.md`; a leading `**/` also matches at the root, fnmatch semantics
otherwise) and `--exclude` globs (fnmatch on project-relative paths,
defaults `.git/**`, `.venv/**`, `build/**`, `coverage/**`,
`node_modules/**` — upstream's list with `.dart_tool/**` traded for
`.venv/**`) drop files.

The command is informational and ALWAYS exits 0 — the exploration tool for
tuning thresholds; usage/flag errors exit 1. There is no config file and
no `check` pipeline (§32), so upstream's gate config maps to the
strictly validated kebab-case flags: `--jaccard`/`--containment` outside
0.0–1.0, `--min-shingles` < 1, an unknown `--format` and any unknown flag
are usage errors. Opt-in by nature: nothing in the default analyze path
reads this module, so defaults cannot change existing behavior.

Output. Console: a summary line `similarity: N files scanned (M skipped
below K shingles), thresholds jaccard >= X / containment >= Y`, then
`no similar files`, or one block per cluster (largest first) listing each
member with its strongest pair metrics (`jaccard 0.87, containment
0.92`), then `P files in C clusters`. `--format json` prints `scanned`,
`skipped`, `thresholds` (`jaccard`/`containment`/`min_shingles`), `pairs`
(`a`, `b`, `jaccard`, `containment` rounded to 4 decimals, strongest
Jaccard first) and `clusters` (sorted files) — stdout stays JSON-only.

Dogfood: measured clean on this repo's own md corpus (README/spec/
CHANGELOG — 3 files scanned, 1 below the 50-shingle floor) at upstream's
thresholds jaccard 0.6 / containment 0.9, and still clean at jaccard 0.5.

Regression-pinned by `tests/test_similarity.py` (fixture shingle counts
are asserted before every cross-file expectation, so a below-floor
fixture can never pass vacuously). Killing fixtures: breaking union-find
transitivity fails `test_pairs_merge_transitively`; swapping the
jaccard/containment metrics or dropping the containment criterion fails
`test_containment_only_pair_crosses`; dropping markdown fence
normalization fails `test_fence_only_differences_still_cluster`; dropping
literal placeholders fails the `.py` and generic literal fixtures.

## 28. `memory`

```
crap4py memory [--top N] [--format console|json] [--badge PATH]
               [--badge-threshold MB] [pytest args...]
```

Per-test allocation churn / GC attribution — port of the crap4dart
`memory` command (upstream 3bae178bc0, phase 1; attribution hardening and
badge from 11551739c8, phase 2). The command runs
`python -m pytest -p crap4py.memory_plugin` (the tap plugin is
`PYTHONPATH`-injected; `PYTHONHASHSEED=0` is pinned for churn
reproducibility) and parses the plugin's JSONL log:

- churn is PRIMARY via tracemalloc windows: peak live traced memory above
  the window-start baseline (`tracemalloc.reset_peak`), deterministic per
  code path; `gc.callbacks` counting (the `--verbose_gc` analog) is
  diagnostics only — Python's GC tap exposes no byte sizes and never fires
  for raw-block allocations, so GC-event-driven bytes would be blind;
- semantics vs upstream, honest: upstream churn = reclaimed new-gen MB
  (GROSS volume); Python has no cheap gross-allocation counter, so churn
  here is the peak live footprint of the window (alloc-and-free cycles
  net out) and `retained` = signed net traced-memory delta — the
  old-gen-growth / leak-suspect analog. GC counts are machine-dependent
  and never a metric (upstream's conclusion, ported);
- each test window covers pytest's `runtest_protocol` — setup + call +
  teardown, the analog of upstream's `testStart`..`testDone` — so fixture
  allocations are attributed to the test; everything OUTSIDE the windows
  (collection/import, session start/finish) goes to a separate
  UNATTRIBUTED bucket: GC counts and collected objects (run totals minus
  window sums), the largest out-of-window peak rise (churn) and the
  out-of-window net delta (retained). The bucket is REPORTED — a console
  line and a `json` `unattributed` object — and never pollutes file/test
  totals (the upstream 11551739c8 window-only attribution rule);
- attribution is EXACT where upstream inferred: nodeids carry the file,
  and window taps share one clock, so there is no dill-index suite
  mapping, no global clock-offset voting and no straggler reattribution —
  those solve two-stream problems a same-process tap cannot have.

Options:

- `[pytest args...]`
  Unknown flags and paths are forwarded to pytest verbatim
  (`parse_known_args`), e.g. `--ignore=crap4py/test_assertions.py`.
- `--top <n>` (default 20)
  How many entries each leaderboard prints; `< 1` is a usage error.
- `--format console|json` (default console)
  Output format; `json` keeps stdout JSON-only (pytest progress goes to
  stderr).
- `--badge <path>`
  Write a shields-style SVG badge (`memory churn | <N> MB`) with the
  suite churn to this path; green at or below, yellow at most twice, red
  above `--badge-threshold` MB. Missing parent directories are created;
  a `Badge written to <path>` note goes to stderr.
- `--badge-threshold <mb>` (default 500)
  Churn threshold (MB) for the badge color; `< 0` is a usage error.

The badge is written by the command itself (self-analyze:
`crap4py memory --ignore=crap4py/test_assertions.py --badge
badges/memory.svg`); the CI commit-badges job refreshes ONLY the
CRAP/coverage badges (`git add badges/*.svg` finds `memory.svg` unchanged
and skips it) — it never regenerates the memory badge.

Console: a summary line `memory: N tests, M GCs, churn X MB peak live,
retained ±Y MB` followed by `, allocated Z MB` when the allocation
volume is positive, an unattributed line `unattributed (collection/session,
outside test windows): X MB peak, ±Y MB retained, M GCs`, then
leaderboards — files by allocation volume (peak live traced MB), tests
by allocation volume (peak live traced MB), tests by retained growth
(leak suspects first). JSON: `test_count`, `gc_count`, `total_churn_mb`,
`total_allocated_mb`, `total_retained_mb`, `unattributed` (`churn_mb`,
`retained_mb`, `gcs`),
`files` (`file`, `tests`, `churn_mb`, `retained_mb`, `gcs`) and `tests`
(`nodeid`, `file`, `churn_mb`, `retained_mb`, `gcs`), MB rounded to 1
decimal; `total_allocated_mb` equals `total_churn_mb` — tracemalloc
exposes no cumulative gross-allocation counter, so the peak live traced
footprint is the documented honest allocation-volume proxy (upstream
b7c227d's `totalAllocatedMb`; the dart engine makes the same move, its
allocated volume being the reclaimed-new-gen churn). `files` and
`tests` are untruncated, the console leaderboards
are `--top`-limited.

The command is informational: it exits 0 after a run — even with failing
or erroring tests (they still get records) — and 1 on usage errors (no
Python test suite found: manifest `pyproject.toml`/`setup.py`/`setup.cfg`
plus a `tests/`/`test/` directory required; no tests executed). The
current interpreter must have pytest installed; unittest-style suites run
through it unchanged.

Dogfood (this repo, `--ignore=crap4py/test_assertions.py` — that source
module's `test_candidates(tree)` helper is miscollected by pytest, a
pre-existing main quirk; the re-exported name still errors at setup,
harmless): two runs, 545 tests each, 27.7 = 27.7 MB churn over 29 test
files, top file `tests/test_duplicates.py` 4.2 MB. Determinism:
file-level churn identical across runs (29/29 files, 545/545 tests, 0.1
MB rounding); GC counts moved 110 → 101 (never a metric); the
unattributed collection/session bucket jitters (8.7–9.2 MB peak, ~8 MB
retained — pytest's own import machinery), which is why it is reported
separately instead of polluting file totals. `--badge badges/memory.svg`
tracked the suite at 28 MB (green).

## 29. `tuple-signatures`

```
crap4py tuple-signatures [paths...] [--max-fields N]
```

Flags inline tuple type annotations in function signatures — port of the
crap4dart record gates (4d844a4 `inline_record_types` + `record_size`),
adapted to Python. A finding is any `tuple[...]` / `typing.Tuple[...]`
subscript with an actual tuple slice — the bare `Tuple` name from
`from typing import Tuple` counts too — in any signature: module functions,
methods, async functions and nested functions; parameter annotations
(positional, keyword-only, `*args`, `**kw`) and return annotations; and
shapes nested inside generic type arguments (`dict[str, tuple[int, int]]`,
`Optional[tuple[int, int]]`, `X | None` unions, `Callable` argument lists).
Only the outermost shape of a nested tuple group is reported: an inner
tuple inside an already-flagged shape is fixed by the same shape
extraction (upstream semantics). Dart's two annotation kinds collapse
here: every Python tuple annotation is unnamed-by-nature (Python has no
named-field tuple syntax), so upstream's "unnamed fields" kind and "inline
shape" kind are one check.

Adaptation notes:

- **Variadic tuples** — `tuple[X, ...]` is a homogeneous sequence, not a
  record shape, and is never flagged (pinned by test).
- **Blessed forms** — `NamedTuple`/`dataclass`/`TypedDict` declarations
  referenced by name are the port of Dart's record typedefs; their
  class-body annotations are declarations, not signatures, and fall
  outside the scan (pinned by test).
- **Positional access (`.$N`) skipped** — upstream's third kind has no
  honest Python analog: `t[0]` is ordinary sequence indexing and would
  fire on every list.
- **`--max-fields` (default 5)** — upstream's `record_size` cap folds into
  this gate: a shape with more than N elements carries an additional
  `N fields > max M` note instead of a separate violation.
- Bare `tuple` and single-element `tuple[int]` (plain-name slice) are not
  flagged; string/forward-reference annotations are not parsed.

Test files are skipped; the default selection is the normal §4 selection.
Output: `<file>:<line>: inline tuple type 'tuple[int, int]' in signature —
declare the shape as a NamedTuple/dataclass and reference it by name` plus
a summary. Exit `2` iff findings.

## 30. `scattered-constants`

```
crap4py scattered-constants [paths...] [--min-files N] [--min-length N] [--min-value N]
                            [--aliases/--no-aliases]
```

Flags literals repeated across files — port of the crap4dart
`scattered_constants` gate (e766d68 + a314b485), the corpus-wide sibling of
`magic-constants` (§19). Three finding kinds:

- **Constant regression** — a named constant for the value already exists
  somewhere in the corpus: a module- or class-level ALL_CAPS assignment
  (§19's const convention, `Assign` or `AnnAssign`) whose initializer is a
  plain string/int/float literal; first declaration wins on shared values.
  Every hardcoded re-introduction — a bare literal anywhere, or a duplicate
  ALL_CAPS declaration in another file — flags IMMEDIATELY, at any
  occurrence count, even a single one: `hardcoded <value> duplicates
  constant \`NAME\` (<file>:<line>) — reference the constant instead of
  repeating the literal`. You do not get to regrow the scatter while the
  gate is silent. The winning declaring site itself never flags (its site
  carries the const-declaration mark and is exempt only at the winning
  file:line); duplicate declarations do flag. The registry honors the site
  scan's coincidence doctrine (a314b485 gate fix 1): numbers of magnitude
  at or below `--min-value` (exit codes 0/1/2, pixel codes, cache
  versions) never seed the corpus — an ignored value cannot be the
  canonical twin that flags someone else — and const-declaration
  initializer hits go through the same `--min-value`/`--min-length` guards
  as use-site literals (gate fix 2; this port's single-collection design
  already routed every site through one guard, pinned by tests).
- **Scattered literal** — no constant exists yet and the value reached
  `--min-files` (default 3) distinct files. The in-file gate counts repeats
  inside one file; this path counts **distinct files per literal value**
  and flags ONE finding per occurrence (file + line) — the "change `86400`
  in every service" hazard that a once-per-file literal hides from every
  other gate. Multiple occurrences in one file each add a finding but
  count as ONE file; the message carries the scope (`in N files
  (M occurrences)`) and the extract-and-verify fix shape.
- **Pure value alias** (a314b485) — a module- or class-level ALL_CAPS
  assignment whose initializer is another name outright (`_APP = BRAND`):
  a second spelling of a fact that already has a home. Private aliases
  flag: `alias \`_APP\` adds no information — reference \`BRAND\`
  directly`. Public aliases are exempt (re-export shims are a deliberate
  API shape); computed initializers (`_APP = BRAND + "!"`, `_APP = f(...)`)
  are not aliases; `--no-aliases` switches the rule off. Alias collection
  shares the const-statement pass with the registry — one pass, no second
  walk; aliases enter the combined finding list and sort with everything
  else by file and line.

Values compare semantically: numbers key on their typed value, so digit
separators and alternate radixes are one fact (`1_000` == `1000` ==
`0x3E8`), while ints and floats stay distinct (`1000` ≠ `1000.0`); strings
key on value. Strings render quoted and truncated at 40 chars in findings.
The summary splits the kinds (`N hardcoded re-use(s) of existing
constants, M scattered-literal occurrence(s), K pure alias(es) in F
files`). Exit `2` iff violations.

False-positive controls (upstream defaults; `magic-constants` shares the
identifier rules via the same helpers):

- strings shorter than `--min-length` (default 4) and numbers of magnitude
  at or below `--min-value` (default 100) are coincidence;
- dict keys, index subscripts and match-case literal patterns are protocol
  identifiers, not constants;
- the winning constant declaration is the blessed form and exempt; a
  computed ALL_CAPS initializer (`PALETTE = mix(...)`, container literals)
  registers no constant and its nested literals stay ordinary sites
  (upstream parity);
- `bool`/`None` and f-strings are never counted (also never registered);
  f-strings are `JoinedStr` nodes and their static text parts are skipped
  with the same id-set mechanism as the identifier positions;
- import specifiers are structural references (upstream skips
  import/export/part URIs) and are not string literals in Python — nothing
  to skip;
- test files are excluded by default (the normal §4-style selection);
  Python has no generated-code glob convention and build/dist/venv trees
  are already excluded by the source finder (upstream defaults:
  generated-code globs + `test/**`).

Adaptation notes:

- **Registry scope** — upstream collects Dart `const` VariableDeclarations
  (top-level/static/instance); Python's const convention is the ALL_CAPS
  assignment, so the registry collects module- and class-level ALL_CAPS
  `Assign`/`AnnAssign` with plain literal initializers — the same
  statement shapes `magic-constants` §19 exempts.
- **Docstrings are string literals** — Python docstrings are
  expression-statement constants; upstream's Dart doc comments are
  comments, invisible to its gate. Doc-text repeats (e.g. shared docstring
  phrasing across gate modules) therefore surface as findings; renaming
  docstrings per file is the accepted remainder, not extraction targets.
- **No `ignore` option** — upstream's value/path-glob ignore list needs
  the config system; the ports' flag surface has no config keys (§32).
  Generic vocabulary ('utf-8', test-framework names, argparse metavar
  words) is accepted remainder. Upstream's `aliases` config key (bool,
  default true) maps to the `--aliases/--no-aliases` flag (default on);
  upstream's strict unknown-key validation has no analog — a flag surface
  rejects unknown flags outright (argparse exit 1).
- **Dogfood** — the gate's first run on crap4py itself found 182
  findings. Extracted: the empty-selection message `'No Python files to
  check.'` (9 gate modules) to `crap4py/constants.py`
  (`NO_FILES_MESSAGE`); under the regression rule, `'coverage.json'`
  (`DEFAULT_COVERAGE`: cli, mutation, runtests), `'console'`
  (`DEFAULT_FORMAT`: memory, similarity) and `'percentage'`
  (`DEFAULT_REPORT`: duplicates) moved to the same shared module, with
  each module's `*_CHOICES` tuple composed from the constant. Zero
  regressions remain. The accepted remainder (scattered class) is the
  accepted classes above (argparse boilerplate, protocol words,
  docstrings, encoding/glob vocabulary).
- **Dogfood (a314b485 re-run)** — the upgraded gate on crap4py's own
  source: 0 regressions, 0 pure aliases, 200 scattered-literal
  occurrences in 35 files — identical to the 6f3cd94 baseline. The
  e766d68 dogfood above had already given the shared vocabulary single
  homes; the corpus minValue guard only removes inert small-int registry
  entries (`MIN_FILES = 3`-shaped module knobs) that could never collide
  (small-value sites are never collected), so no warning counts moved and
  no new vocabulary homes were needed. The alias rule found nothing to
  inline — crap4py has no `_X = Y` pure aliases (the e766d68 extraction
  referenced `constants.py` directly, creating no aliases, per upstream's
  own review lesson). No `magic-constants` interaction: the shared homes
  are the same ALL_CAPS declarations that gate already exempts.

## 31. Ignore markers (gate framework)

Port of crap4dart 8abb64c ("ignore comments: gate-specific granularity").
Gate violations may be suppressed by source comments, adapted from Dart's
`//` to Python's `#`; marker literals stay ASCII (`ignore:`,
`ignore-file:`, `crap:ignore`, `crap:ignore-file`).

Grammar and scopes:

- `# crap:ignore` — legacy blanket line marker: suppresses every ignorable
  gate on its line. Detection is a substring match, like upstream (a
  `crap:ignore-file` line therefore also carries line scope).
- `# ignore:<gate-id>` — granular line marker: suppresses only that gate
  on its line. Dart lint style — ONE id per marker, several markers union.
  Match rule `#\s*ignore:([^\s#]*)`: a comma id is ONE unknown id (comma
  lists are silently inert) and unknown ids match nothing; an empty id
  (`# ignore:`) is a blanket — every ignorable gate.
- Line scope: the violation's line (trailing marker counts — and, as
  upstream, a trailing marker on the line directly above), or the run of
  WHOLE-LINE markers stacked above the violation: every trimmed line
  starting with exactly `# ignore:` or `# ignore-file:` continues the
  run; the first non-marker line stops it. `#ignore:` without the space
  still matches the granular rule on its own line but does not continue
  a run.
- `# crap:ignore-file` / `# ignore-file:<gate-id>` — file scope: first
  5 lines of the violation's file, several markers union, empty id =
  blanket. File markers outside the first 5 lines are inert.
- Violations without a line number (`class-size` totals, `file-naming`,
  `unused-files` paths) are only suppressed by file-scoped markers;
  unreadable files are never suppressed.

Opt-in: suppression applies only to gates in
`ignore_filter.IGNORABLE_GATES`. Upstream gates carry a per-gate
`ignorable: true` config flag (default false); the ports' flag surface
has no config system (§32), so the hardcoded registry IS the opt-in.
The filter runs once per gate at the shared violation path — after the
scan, before printing and exit-code aggregation. Ignorable gate ids:

| Subcommand            | Gate id               | Line scope | File scope |
|-----------------------|-----------------------|------------|------------|
| `complexity`          | `complexity`          | yes        | yes        |
| `nesting`             | `nesting`             | yes        | yes        |
| `class-size`          | `class_size`          | — (no line)| yes        |
| `weight-of-class`     | `weight_of_class`     | yes        | yes        |
| `unused-code`         | `unused_code`         | yes        | yes        |
| `unused-files`        | `unused_files`        | — (no line)| yes        |
| `banned-imports`      | `banned_imports`      | yes        | yes        |
| `magic-constants`     | `magic_constants`     | yes        | yes        |
| `scattered-constants` | `scattered_constants` | yes        | yes        |
| `test-assertions`     | `test_assertions`     | yes        | yes        |
| `duplicates`          | `duplication`         | yes        | yes        |
| `mutation`            | `mutation`            | yes        | yes        |
| `tuple-signatures`    | `inline_record_types` | yes        | yes        |
| `string-hazards`      | `string_hazards`      | yes        | yes        |
| `identical-branches`  | `identical_branches`  | yes        | yes        |
| `enum-name-contract`  | `enum_name_contract`  | yes        | yes        |
| `collapsible-flow`    | `collapsible_flow`    | yes        | yes        |
| `file-naming`         | `file_naming`         | — (no line)| yes        |

Not ignorable: `folder-structure` (directory-level findings — nothing
line- or file-scoped can address them), `similarity`/`memory`
(informational, always exit 0), `profile`/`skill` (not gates). A gate
family a port lacks is simply not ignorable there.

Host adaptations vs upstream: `//` → `#` in the match rules and the
whole-line prefixes; marker matching stays textual on the raw source
line — a marker inside a string literal on a violating line suppresses
too (upstream has the same property); gate ids keep upstream's
snake_case, mapped from the kebab-case subcommands.

## 32. `reorder`

```
crap4py reorder [paths...] [--dry-run/-n]
```

The `reorder` command shall rewrite each listed Python file (default: the
normal §4 selection) so that class-body members appear in the canonical
order: class constants (`UPPER_SNAKE_CASE` assignments, annotated or not),
class attributes (every other assignment/annotation), constructors
(`__init__`, `__new__`), other dunder methods, static/class methods
(`@staticmethod` / `@classmethod`), properties (`@property` /
`@x.setter` / `@x.deleter`), public methods, private methods (`_name`).
Members within one category shall keep their original relative order (a
stable partition — the command never sorts alphabetically). A leading
docstring stays first. Whole-line `#` comment blocks above a member and its
decorators shall move with it — including a block separated from the member
by a single blank line; two or more blank lines detach the block, and a
detached block shall ride with the preceding member (no comment is ever
dropped). Enum-family subclasses (`Enum`, `IntEnum`, `StrEnum`, `Flag`,
`IntFlag`, dotted or bare) shall have their member assignments frozen —
value order is semantics (iteration order, `value` lookups and member
identity depend on it) — and the member section after the last member
assignment shall be reordered into the canonical order exactly like a
class body; an enum without a member section shall come back
byte-identical. Because Python allows interleaving, a member assignment
appearing after code shall freeze everything up to and including itself
(the host analog of the constants-terminator span rule). Order dependencies
inside a body are a documented constraint: a member that reads a bare name
bound by an earlier member of the same body (`cr = gi` aliases, method
defaults, decorators, annotations — the expressions Python evaluates while
the class body runs) shall never be reordered before its binder; it shall
be pinned to the antecedent's position (moving with its chain), and a body
whose dependencies the partition cannot respect shall come back
byte-identical. Reads that cannot resolve inside the body — attribute
access, imported/module/builtin names, and anything inside a method body
(evaluated at call time, not class-definition time) — never constrain the
reorder. Bodies containing anything but member statements
(`if`/`try`/nested class/bare expressions) shall never be reordered, nor
shall members sharing a source line
(`class A: x = 1` one-liners, semicolon lists). A file's line-ending style
(LF or CRLF) and its trailing-newline state shall be preserved.

The transform shall be deterministic and idempotent: the same input shall
produce the same output, already-canonical bodies shall be left
byte-identical (whatever their member layout), and reordering an
already-reordered file shall not change it. Nested classes: the innermost
reorderable class bodies are fixed first and the enclosing bodies on the
next fixpoint pass (a nested class in a body marks it not reorderable;
method-local classes are fixed inside the moving member text). With
`--dry-run` the command shall write nothing, list the files that would
change, and exit `2` when at least one would (0 otherwise); without it,
files are written and the command exits `0`. Files that fail to parse shall
be skipped with a note and shall not fail the run.

## 33. Non-goals

- No runtime dependencies (stdlib only).
- No branch coverage — line coverage only (matches the cross-port contract).
- No badge framework — the CI commit-badges job and the memory command's
  `--badge` write plain SVGs directly (via `crap4py.badge`); no IDE plugins.
- No support for non-`.py` files in CRAP analysis (the `similarity` scan
  reads any text per §27) or non-coverage.py formats.
- No config file and no gate framework (crap4py is a flag-based single
  surface, unlike crap4dart); `--tags`/`--exclude-tags` profiling filters
  are not ported. Gate severity/entries/baseline features of crap4dart
  0.5.0 are likewise not ported (§18 adaptation note) — except the
  warning-default gates' severity, which maps to a `--severity` flag
  (§34, §35); the `ignorable`/ignore-comment opt-in IS ported as a
  hardcoded registry (§31).

Not ported from crap4dart 0.7–0.9 (upstream-specific):

- `broken_goldens` / tofu detection and the goldens-guard command —
  Flutter PNG golden files have no Python analog.
- the `external` gate (Checkstyle-XML import) — tied to the Dart/Java
  gate-framework config system.
- `run_tests: true` running the full suite on every analyze by default —
  breaking for a CLI; the port keeps `--run-tests` opt-in (§11).
- pixel-detector tuning commits — depend on `broken_goldens` (above).

## 34. `string-hazards`

```
crap4py string-hazards [paths...] [--severity warning|error]
```

The `string_hazards` gate (62fec6833e — port of crap4dart's
`string_hazards` gate) shall flag string literals whose escaping or pattern
usage contradicts what the author almost certainly meant, from the parsed
AST only. Three finding kinds, each the Python analog of the upstream Dart
shape (§34 mapping is normative):

1. **Escaped f-string interpolation** — upstream: `\$name`/`\${name}`
   inside a non-raw string renders the literal text `$name`. Python maps
   the shape to `{{name}}` inside an f-string: the gate scans the f-string's
   decoded literal parts (Constant values of `JoinedStr`), where a literal
   brace can only come from a `{{` escape, so `{identifier` in decoded text
   is precisely the escaped form rendering a literal `{identifier}`. Such
   an occurrence shall be flagged ONLY when the same identifier is genuinely
   interpolated elsewhere in the same file — the root receiver of some
   replacement-field expression (`{a.b[i].c()}` evidences `a`) — one
   finding per (line, identifier). Without sibling evidence the escape is
   presumed deliberate (shell text, literal-brace templates). Raw-ness
   shall not be consulted: `{{ }}` unescaping is a brace rule identical in
   raw f-strings (unlike Dart raw strings, where `\$` stays `\$`). Plain
   (non-f) strings never interpolate in Python and shall never be scanned
   for this kind.
2. **Escaped regex anchor** — a string literal passed to the
   `re.compile` family (`compile`, `match`, `search`, `fullmatch`,
   `findall`, `finditer`, `split`, `sub`, `subn`; first positional argument
   or `pattern=` keyword) whose VALUE ends with `\$` or starts with `\^`.
   The regex then matches a literal dollar/caret where the end/start anchor
   was almost certainly intended. The check shall be value-based, not
   rawness-based: in Python an unrecognized escape such as `'\$'` survives
   into the regex in BOTH raw and non-raw strings (unlike Dart, where a
   non-raw `'\$'` decodes to a bare `$` anchor), so both spellings carry
   the bug the upstream raw-string check catches. A non-literal pattern
   argument (name, call) shall be spared, as shall escaped anchors in
   strings outside the `re` family.
3. **Anchors in literal `str` patterns** — a string literal passed as the
   first positional argument of `str.replace`, `split`, `rsplit`,
   `partition` or `rpartition` (the pattern is matched literally) whose
   value starts with `^` or ends with an unescaped `$` (a value ending
   `\$` is an explicitly escaped, deliberate dollar and shall be spared).
   Python has no adjacent-string literals — implicit concatenation already
   merges into one constant, so the merged value is checked whole.
   `re.split` is a regex call (kind 2), not this kind; a non-string or
   non-literal first argument shall be spared.

Every violation shall carry the file (project-relative) and line of the
offending literal. Severity: `warning` by default — violations are
reported (`[WARN] file:line: message`) and the exit code is `0`;
`--severity error` makes violations exit `2`. This flag stands in for
upstream's per-gate `severity` config key (§33) and is one of the port's
warning-default gates (§35 is the other); ignore markers
(`# ignore:string_hazards` / `# crap:ignore`) apply per §31 (the gate is
ignorable).

## 35. `identical-branches`

```
crap4py identical-branches [paths...] [--severity warning|error]
```

The `identical_branches` gate (c6b2425add — port of crap4dart's
`identical_branches` gate) shall flag the copy-paste-edit family —
syntactically identical pieces inside one construct where a second,
slightly different piece was intended. All checks compare by canonical
form over the parsed AST: Python's AST carries no parentheses and no
comments, so comparing AST structure is whitespace-, comment- and
parenthesis-insensitive by construction. Operands of commutative
operators sort (`a == b` matches `b == a`, `a and b` matches `b and a`)
and same-operator nests of associative operators flatten (`a and b and c`
compares equal regardless of grouping; `a - (b - c)` differs from
`a - b - c`). Canonical-form operators, per the cross-port contract:
`==` (`Eq`), `!=` (`NotEq`) and `in` (`In` — the Python-only member the
contract adds) sort their two operands; `and`/`or` and the bitwise/
arithmetic `+ * & | ^` flatten same-operator nests and sort; `is`/
`is not`/`not in`, the directional comparisons (`< > <= >=`) and
non-associative arithmetic (`- / % // ** << >> @`) keep operand order
(identity reads directionally — `is` is deliberately never sorted).
Unary operators render `op(operand)`; leaves render as structural AST
text. Six finding kinds:

1. **Duplicate conditions** — two links of one `if`/`elif` chain whose
   conditions compare equal; every later branch is unreachable. Python
   has no `elif` AST node (an elif is an `If` in the head's `orelse`),
   so an `if` after an `else:` is by construction a chain link of the
   same chain. `match` shall be excluded (upstream excludes `switch`).
2. **Duplicate branch bodies** — two bodies in one chain whose
   statements compare equal by structural text; a single-statement list
   shall compare equal to the naked statement (free in Python, where
   both spellings are a one-element list). The violation shall point at
   the statement's line.
3. **Duplicate and absorbed operands** — one boolean chain whose
   flattened operand list contains a repeated canonical operand (`a or a`
   — only the outermost link of a same-operator chain shall be checked),
   or an operand that also appears standalone as another top-level
   operand's conjunct (for `or`) or disjunct (for `and`) — the
   absorption law (`a or b and a` collapses to `a`), same law both ways.
   Only the first such finding per chain shall be reported.
4. **Identical ternary branches** — an `x if c else y` expression whose
   then and else expressions compare equal.
5. **Duplicate positional arguments** — one call whose positional
   argument list contains a repeated canonical argument. Keyword
   arguments shall be exempt (the name documents the slot); an
   all-literal argument list shall be exempt (uniform-value APIs like
   `Rectangle(8, 8, 8, 8)` stay quiet); a literal argument shall never
   be reported as the repeat (only name-like repeats report — a repeated
   `-8` reports, mirroring upstream, where `-8` is a unary expression
   rather than a literal). A `**` expansion is unnamed and shall count
   as a positional slot.
6. **Duplicate except bodies** — two handlers of one `try` (including
   `except*` groups) whose bodies compare equal. Duplicate exception
   TYPES are deliberately not flagged: Python dispatches handlers per
   type, so the types are semantically distinct in a way Dart `case`
   labels are not; only the bodies are compared. `else`/`finally` bodies
   are not compared.

Only pieces of the *same* construct shall be compared; identical
conditions in separate `if` statements are legal (the state may change
between them). Test files are excluded by the default selection,
matching the port's other AST gates. Every violation shall carry the
file (project-relative) and line of the repeated occurrence. Severity:
`warning` by default — violations are reported
(`[WARN] file:line: message`) and the exit code is `0`; `--severity
error` makes violations exit `2`. This flag stands in for upstream's
per-gate `severity` config key (§33); ignore markers
(`# ignore:identical_branches` / `# crap:ignore`) apply per §31 (the
gate is ignorable).

## 36. `enum-name-contract`

```
crap4py enum-name-contract [paths...] [--severity warning|error]
```

The `enum_name_contract` gate (418f1c58dc — port of crap4dart's
`enum_name_contract` gate) shall flag reads of the built-in `.name` member
inside enum class bodies, resolved from the parsed AST only, where the
contract value silently changes when an identifier is renamed
(`Color.RED.name` is `"RED"` until the constant is renamed — and every
persisted YAML/JSON/DB row written from it drifts). The audited scope
shall be the class bodies of `Enum`/`IntEnum`/`StrEnum`/`Flag`/`IntFlag`
subclasses, transitively through in-file enum subclasses (a class whose
base is a known enum-family name, or a class defined in the same file
that transitively subclasses one). A receiver is provably enum-typed when
it is:

1. `self` — `self.name` in an instance method body, including nested
   functions closing over `self`;
2. implicit `self` — a bare `name` identifier read in an instance method
   body with no local shadowing of that name (implicit `self`);
3. one of the enum's own constants, bare (`RED.name`) or qualified
   (`Color.RED.name`), valid in static members too;
4. a loop variable introduced by iterating the enum's values/members —
   `for c in Color`, `for k in Color.__members__`, `for c in list(Color)`,
   or the equivalent comprehension forms;
5. a parameter explicitly annotated with the enclosing enum's name
   (`c: Color`; string spellings `c: "Color"` and qualified `mod.Color`
   spellings included; `Optional[Color]` is deliberately not provable),
   including parameters of defs nested inside enum member bodies —
   registered flat over the member body, as upstream.

Only `.name` shall be policed: Python enums have no built-in `.index`
member (`list(Enum).index(x)` is a list method, not a built-in), so the
upstream `.index` half of the gate is omitted. `.value` IS the explicit
contract and shall never be flagged; every violation message shall carry
the fix hint to contract on an explicit value field (`.value`). The
upstream `values[i]` index-access receiver shape has no common Python
idiom and shall not be tracked (the `for x in` loop forms cover the
iteration idiom).

Guards: an enum declaring its own `name` attribute (an assignment or a
method/property `def`) shadows the built-in and shall not be flagged.
Assignment and invocation positions shall not be treated as reads
(`x.name()` is a member call, not a `.name` read — the exclusion applies
to every receiver class; `x.name`'s `name` is a string attribute in
Python's AST, structurally never an implicit-self candidate; `name()` is
a call, not a read; a Store/Del context is a binding, not a read). `staticmethod` and
`classmethod` bodies shall have no implicit self (bare `name` there is a
module global read, and `self` is out of scope). Usages outside enum
class bodies are Tier 2 — they need type matching — and are out of
scope; non-enum class bodies are likewise never audited.

Severity: `warning` by default — violations are reported
(`[WARN] file:line: message`) and the exit code is `0`; `--severity
error` makes violations exit `2`. This flag stands in for upstream's
per-gate `severity` config key (§33); ignore markers
(`# ignore:enum_name_contract` / `# crap:ignore`) apply per §31 (the
gate is ignorable).

## 37. `collapsible-flow`

```
crap4py collapsible-flow [paths...] [--severity warning|error]
```

The `collapsible_flow` gate (7bdfd64ae1 — port of crap4dart's
`collapsible_flow` gate) shall flag control-flow nesting that a
semantics-preserving mechanical rewrite can flatten, resolved from the
parsed AST only. Three checks:

1. **Collapsible if** — an `if` without `else` whose suite holds only
   one statement that is itself an `if` without `else`. The two
   conditions merge into a single `and` condition; Python's `and`
   short-circuits, so evaluation order is preserved and the merge is
   always equivalent (including `await`ed or side-effecting
   conditions). A Python `if` has no pattern condition to block a
   merge — `match` is a separate statement and is never merged.
2. **Redundant else** — an `if`/`else` whose then-suite always exits:
   ends with `return`, `raise` (bare `raise` = rethrow — the same AST
   node as `raise X`), `break`, `continue`, an `if`/`else` pair whose
   both branches exit, or a `try`/`try*` whose body exits and whose
   `finally` is absent, a lone `pass` (Python cannot write an empty
   suite), or exits. The `else` is redundant and its body dedents. An
   inner `if` without an `else` shall never count as exiting (control
   falls through when its condition is false); `match` bodies shall not
   be analyzed for exits (pattern fall-through semantics, as upstream's
   `switch`), and `except` handlers shall not be analyzed (as upstream,
   only the body and the `finally` decide). A redundant else on an
   `elif` link shall carry the nest-first hint (nest the chain under the
   enclosing `else`, then dedent within it): Python has no braces, so a
   bare dedent of the link's else body would escape the head's else
   scope and run after earlier falling-through branches.
3. **Chain after terminal then** — an `elif` (Python has no elif AST
   node: an `if` in the head's `orelse`) following an always-exiting
   then-suite, flagged as a flattening hint (guard sequence or dispatch
   table); the rewrite is not mechanical, so the message shall not
   promise a specific fix.

`match` bodies are still visited: a collapsible if inside a `case`
suite is flagged like any other.

Default severity: `warning` — violations are reported
(`[WARN] file:line: message`) and the exit code is `0`; `--severity
error` makes violations exit `2`. This flag stands in for upstream's
per-gate `severity` config key (§33); ignore markers
(`# ignore:collapsible_flow` / `# crap:ignore`) apply per §31 (the
gate is ignorable). Every violation shall carry the file and line of
the offending construct.
