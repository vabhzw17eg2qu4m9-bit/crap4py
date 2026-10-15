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
crap4py test-assertions [..] Flag test bodies without assertion calls.
crap4py duplicates [opts] [p..] Flag duplicated lines (percentage) or clone groups (clones).
crap4py mutation [opts] [p..]  Mutation testing: re-run tests per mutated operator.
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
`banned-imports`, `magic-constants`, `test-assertions`, `folder-structure`,
`duplicates`, `mutation`) are
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
| `2`  | CRAP threshold exceeded; profile `--threshold` exceeded; gate-subcommand violations (§12–§19, §26). |

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
N files`; fail summary: `K clone groups (>= T tokens, >= M lines)`. Files
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

## 27. Non-goals

- No runtime dependencies (stdlib only).
- No branch coverage — line coverage only (matches the cross-port contract).
- No badges, no IDE plugins.
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
