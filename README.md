# crap4py

[![Quality](https://github.com/vabhzw17eg2qu4m9-bit/crap4py/actions/workflows/quality.yml/badge.svg)](https://github.com/vabhzw17eg2qu4m9-bit/crap4py/actions/workflows/quality.yml)
[![version](https://img.shields.io/github/v/release/vabhzw17eg2qu4m9-bit/crap4py?label=version)](https://github.com/vabhzw17eg2qu4m9-bit/crap4py/releases)
![CRAP](badges/crap.svg)
![coverage](badges/coverage.svg)
![memory churn](badges/memory.svg)

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
python3 -m crap4py
```

For development:

```bash
git clone <repo>
cd crap4py
python3 -m venv .venv && . .venv/bin/activate
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
crap4py complexity           # flag methods over the complexity limit (--metric cyclomatic|cognitive)
crap4py nesting              # check functions for nesting deeper than 5 levels
crap4py class-size           # check classes for >25 methods or WMC >80
crap4py weight-of-class      # check classes for public data weight >0.33
crap4py unused-code          # check for unused private module declarations
crap4py unused-files         # check for source files never imported
crap4py banned-imports       # enforce --from/--forbid import boundaries
crap4py magic-constants      # flag hex colors outside constants and repeated literals
crap4py scattered-constants  # flag literals repeated across 3+ distinct files
crap4py test-assertions      # flag test bodies without assertion calls
crap4py duplicates           # flag files over the duplicated-lines threshold
crap4py mutation             # mutation testing: re-run tests per mutated operator
crap4py similarity           # scan for near-duplicate files (console/json)
crap4py memory               # per-test alloc churn + GC attribution (informational)
crap4py tuple-signatures     # flag inline tuple annotations in function signatures
crap4py reorder              # rewrite class bodies into canonical member order
crap4py string-hazards       # flag escaped f-strings, regex anchors, literal-pattern anchors
crap4py identical-branches   # flag the copy-paste-edit family inside one construct
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

A source-instrumentation profiler: copies the project to a temp dir,
reports method entry/exit for every function body to an injected
collector (stdlib `ast`) that owns per-call timers on a thread-local
call stack, runs the test suite (pytest, unittest fallback) against the
instrumented copy, and reports exact per-method inclusive (TOTAL) and
self (SELF) timing.

```bash
crap4py profile                      # profile the whole suite
crap4py profile --name "parser"      # only run tests matching (pytest -k)
crap4py profile --top 10             # limit console rows (default 20)
crap4py profile --threshold 10.0     # exit 2 when any total exceeds 10ms
```

Example console output:

```
Profile Report (2 methods, total 73.90ms)
    TOTAL      SELF       %  CALLS  MEAN(µs)  MAX(µs) @60fps(ms)  METHOD                   FILE:LINE
  45.20ms   12.10ms   61.2%    142     318.3     2890      19.10  analyzer.analyze_method  pkg/analyzer.py:88
  28.70ms    8.30ms   38.8%     88     326.1     2100      19.57  parser.parse_file        pkg/parser.py:21

Threshold: 10.00ms — 2 methods exceed
```

Console columns: `TOTAL | SELF | % | CALLS | MEAN(µs) | MAX(µs) | @60fps(ms) | METHOD | FILE:LINE`.
**TOTAL** is inclusive time across all calls; **SELF** is total minus
nested instrumented calls that completed while the frame was open
(flamegraph self-time — ranks hot code by actual CPU burn, not by how
many callers fan out through it). TOTAL, SELF and the summary line
render with adaptive units (`82.50ms`, `13.89s`, `22.50m`, `13.89h`) so
extreme call counts keep the columns compact. A `~` prefix on `MEAN`
marks sub-30µs means, where instrumentation overhead dominates — read
CALLS/TOTAL deltas there instead. Timing keys
are module-qualified (`pkg.mod.func`), so same-named functions in
different modules never merge into one row.
Full reports are written to `profile-reports/profile-<timestamp>.txt` and
`.json` regardless of `--top` (per-method records carry `totalSelfMicros`).

## `crap4py file-naming`

Flags mechanical source file names — generic dumping-ground stems
(`utils.py`, `helpers.py`) and numeric suffixes (`batch1.py`, `report2.py`) —
which usually mean code was split without a domain boundary. Technical stems
where digits carry meaning (`base64.py`, `sha256.py`, `utf8.py`, ...) are
allowed. Prints one line per violation plus a summary; exits `2` iff any
violations.

## `crap4py complexity`

Fails methods whose complexity exceeds the limit (`--max`, default 10).
`--metric` selects the metric: `cyclomatic` (default, path count — the value
CRAP uses) or `cognitive` (SonarSource-style, nesting-weighted — flat `elif`
chains stay cheap, nested ones pay extra; `and`/`or` sequences count once per
run). Prints one line per violation plus a summary; exits `2` iff any
violations.

## Gate subcommands (ported from crap4dart 0.5.x–0.9.x)

Eleven quality-gate checks, each a standalone subcommand taking optional
explicit paths (default: the normal §4-style selection — `src/` else `.`,
test files skipped; `test-assertions` inverts this and scans test files).
All exit `2` iff violations, `1` on usage errors.

| Subcommand                | Fails when …                                                        |
|---------------------------|---------------------------------------------------------------------|
| `complexity [--metric M] [--max N] [paths...]` | a method's complexity exceeds N (default 10) — `M` is `cyclomatic` (default) or `cognitive`. |
| `nesting [paths...]`      | a function's control-flow nesting exceeds 5 levels (body = 1; `elif` chains stay flat). |
| `class-size [paths...]`   | a class has >25 concrete methods or a complexity sum (WMC) >80.     |
| `weight-of-class [paths…]`| a class's public data share (`self.<attr>` fields ÷ public members) >0.33. |
| `unused-code [paths...]`  | a module-level private name (`_func`, `_x = …`) is never referenced in its module. |
| `unused-files [paths...]` | a non-test file is never imported by any analyzed non-test file (`__init__.py` re-exports count; `__init__.py`/`__main__.py` exempt). |
| `banned-imports [--from GLOB --forbid GLOB [--message MSG]]... [paths...]` | a file matching `from` imports something matching `forbid` (raw dotted name or resolved project path). |
| `magic-constants [paths...]` | a hex color literal (`0xRRGGBB`/`0xAARRGGBB`) appears outside an ALL_CAPS constant assignment, or a numeric/string literal (length ≥4) repeats ≥3 times in one file (dict keys, index strings and match-case patterns are skipped). |
| `scattered-constants [--min-files N] [--min-length N] [--min-value N] [--aliases/--no-aliases] [paths...]` | a value with an existing ALL_CAPS constant anywhere is re-introduced hardcoded — bare literal or duplicate declaration, any count, even once (`hardcoded <v> duplicates constant \`NAME\` (file:line)`); or a constant-less literal (string ≥N chars, default 4; number of magnitude >N, default 100) spans N distinct files (default 3) — one finding per occurrence (dict keys, index strings, match-case patterns skipped; `1_000` and `1000` are one value); or a private ALL_CAPS name is an outright alias of another name (`_X = Y` → `alias \`_X\` adds no information`; public aliases exempt, `--no-aliases` off). |
| `test-assertions [--min N] [paths...]` | a `test_*` function/method body has fewer than N (default 1) assertion signals — `assert` statements, `self.assert*`/`self.fail`, `pytest.raises`. |
| `folder-structure [--dir DIR]... [--max N] [--cap PATTERN=N]... [--exclude GLOB]...` | a directory holds more than its cap of logical `.py` units directly (`__init__.py`/`__main__.py` not counted; `foo_pb2.py`/`foo_pb2_grpc.py` collapse into `foo`'s stem; subdirectories never reach the parent's budget). `--dir` entries may be glob patterns (`pkg/*`, `packages/**`); `--cap` (repeatable) gives each pattern its own cap and takes precedence over `--dir`/`--max` (first pattern wins); `--exclude` globs exempt files. |
| `duplicates [--threshold N] [--min-tokens N] [--min-lines N] [--ignore-locals] [--ignore-literals] [--ignore-identifiers] [--ignore-imports] [--skip-duplicate-files] [--report percentage\|clones] [--exclude GLOB]... [--source PATH]... [paths...]` | a file's duplicated-line share (Rabin-Karp over token windows, `--min-tokens` 50 / `--min-lines` 5 defaults) exceeds N percent (default 1.0) — matches within or across files. `--report clones` switches to one violation per non-overlapping clone group (PMD CPD style). `--ignore-locals`/`--ignore-literals` opt into Type-2 normalization, `--ignore-identifiers`/`--ignore-imports`/`--skip-duplicate-files` are the CPD-parity knobs (below). |
| `mutation [--coverage PATH] [--min-score N] [--max-survivors N] [--timeout-seconds N] [--max-mutants N] [--operator CATEGORY]... [--exclude GLOB]... [--cache/--no-cache] [--test-args ARG]... [--scope-tests/--no-scope-tests] [--diff-base REF] [paths...]` | the mutation score (killed ÷ (killed + survived), uncovered sites excluded) is below N (default 0.8) or survived mutants exceed M (default 0). One violation per survived mutant; a timeout counts as survived, an import/compile error as killed. |
| `tuple-signatures [--max-fields N] [paths...]` | a function signature (parameter or return annotation) contains an inline tuple shape — `tuple[int, str]` / `typing.Tuple[...]` (bare `Tuple` counts; variadic `tuple[X, ...]` exempt; nested-in-type-args included, outermost reported); shapes with more than N (default 5) elements add an over-cap note. |
| `string-hazards [--severity warning\|error] [paths...]` | a string literal's escaping or pattern usage contradicts the author's almost-certain intent: `{{name}}` inside an f-string while `name` is genuinely interpolated elsewhere in the same file (escaped interpolation); a string passed to the `re.compile` family whose value ends `\$` or starts `\^` (escaped regex anchor — literal dollar/caret match where the anchor was intended); or a string first argument of `str.replace`/`split`/`rsplit`/`partition`/`rpartition` starting `^` or ending `$` (anchors in a literal pattern). Default `warning`: findings print `[WARN]` and exit 0; `--severity error` exits 2. |
| `identical-branches [--severity warning\|error] [paths...]` | the copy-paste-edit family — syntactically identical pieces inside one construct, compared by canonical form (whitespace/comment/parenthesis-independent, commutative operands sorted, same-operator nests flattened): duplicate conditions or branch bodies in one `if`/`elif` chain; duplicate operands in one `and`/`or` chain plus absorbed operands (`a or b and a`); identical ternary branches (`x if c else x`); duplicate positional arguments in one call (keyword arguments exempt, all-literal lists exempt, a literal never the repeat); duplicate except bodies in one `try` (exception types are semantically distinct and never compared). Default `warning`: findings print `[WARN]` and exit 0; `--severity error` exits 2. |

`unused-code` and `unused-files` are whole-project checks: given explicit
paths they skip with `not meaningful for a partial selection` (exit `0`),
since a partial file set yields false positives. `banned-imports` pairs
`--from`/`--forbid` in CLI order (unequal counts → exit `1`); with no rules
it passes and says so.

`tuple-signatures` is the port of crap4dart's record gates (4d844a4,
`inline_record_types` + `record_size`): an anonymous record shape maps to an
inline tuple annotation, and the named forms (`NamedTuple`, `dataclass`,
`TypedDict`) are the blessed typedefs. Upstream's unnamed-field kind
collapses into the inline-shape kind (Python tuples carry no field names),
and the `.$N` positional-access kind is skipped — `t[0]` is ordinary
sequence indexing, not a record fingerprint.

`scattered-constants` is the corpus-wide sibling of `magic-constants`
(port of crap4dart e766d68 + a314b485, `scattered_constants`), with three
finding kinds. **Constant regression**: once a named constant for a value
exists anywhere (module/class-level ALL_CAPS with a plain literal value,
first declaration wins), every hardcoded re-introduction — bare literal or
duplicate declaration in another file — flags immediately at any count,
naming the constant and its `file:line` to reference; the winning
declaration itself never flags. The registry honors the site scan's
coincidence doctrine: numbers of magnitude at or below `--min-value` (exit
codes, pixel codes, cache versions) never seed the corpus, so small facts
cannot collide in the regression rule. **Scattered literal**: with no
constant yet, the gate counts DISTINCT files per literal value and flags
every occurrence once the value spans `--min-files` files — the "change
`86400` in every service" hazard that a once-per-file literal hides from
every other gate. Values compare semantically (`1_000`, `1000` and `0x3E8`
are one fact; ints and floats stay distinct). **Pure value alias**: a
private ALL_CAPS name whose initializer is another name outright
(`_APP = BRAND`) adds a spelling without information and flags
(`alias \`_APP\` adds no information — reference \`BRAND\` directly`);
public aliases are exempt (re-export shims are a deliberate API shape),
computed initializers are not aliases, and `--no-aliases` switches the
rule off. Adaptations: import specifiers are structural references
(upstream skips import/export/part URIs) but are not string literals in
Python, so nothing to skip; Python docstrings are string-literal
statements, so doc-text repeats surface as findings (upstream's Dart doc
comments are invisible to its gate); upstream's `ignore` option is not
ported (flag-based surface, no config system — its `aliases` config key
maps to the `--aliases/--no-aliases` flag). Test files and
build/dist/venv trees are excluded by the default selection;
identifier-position strings are exempt, per upstream semantics.

`string-hazards` is the port of crap4dart's `string_hazards` gate
(62fec6833e): flags string literals whose escaping or pattern usage
contradicts what the author almost certainly meant, from the parsed AST
only. Three finding kinds, each mapped to Python's honest analog:

- **Escaped f-string interpolation** — upstream flags `\$name` inside a
  non-raw Dart string (renders the literal text `$name`); Python's analog
  is `{{name}}` inside an f-string (doubled braces render a literal
  `{name}`). Flagged only when the same identifier is genuinely
  interpolated elsewhere in the same file (sibling evidence — the root
  receiver of some `{a.b[i].c()}` field), which keeps deliberate literal
  braces (shell text, templates) quiet; one finding per (line, identifier).
  Raw-ness is deliberately not consulted: unlike Dart raw strings (where
  `\$` stays `\$`), `{{ }}` unescaping is a brace rule that behaves
  identically in raw f-strings. Plain (non-f) strings never interpolate in
  Python — nothing to escape, never scanned.
- **Escaped regex anchor** — a string passed to the `re.compile` family
  (`compile`, `match`, `search`, `fullmatch`, `findall`, `finditer`,
  `split`, `sub`, `subn` — first positional or `pattern=`) whose value ends
  `\$` or starts `\^`: the regex matches a literal dollar/caret where the
  end/start anchor was almost certainly intended. The check is value-based,
  not raw-based: in Python an unrecognized escape such as `'\$'` survives
  into the regex in BOTH raw and non-raw strings (unlike Dart, where a
  non-raw `'\$'` decodes to a bare `$` anchor), so both spellings carry the
  bug upstream's raw-string check catches.
- **Anchors in literal `str` patterns** — `str.replace`/`split`/`rsplit`/
  `partition`/`rpartition` treat their pattern argument as literal text, so
  a leading `^` or trailing `$` can only match that literal character; a
  regex was probably intended. Only a string-literal first positional
  argument is checked (a variable or call argument is spared, as upstream);
  a value ending `\$` is an explicitly escaped, deliberate dollar and is
  spared. Python has no adjacent-string literals (implicit concatenation
  already merges into one constant), so the merged value is checked whole;
  `re.split` is a regex call (previous kind), not this kind.

Severity: upstream defaults the gate to `warning` — findings are reported
(`[WARN] file:line: message`) but do not fail the run (exit `0`);
`--severity error` restores the fatal exit `2`. This was the port's first
warning-default gate (all others are error-default), standing in for
upstream's per-gate `severity` config key; ignore markers
(`# ignore:string_hazards`) are honored — the gate is ignorable.

`identical-branches` is the port of crap4dart's `identical_branches` gate
(c6b2425add): flags the copy-paste-edit family — syntactically identical
pieces inside one construct where a second, slightly different piece was
intended. Six checks, all comparing by canonical form: Python's AST
carries no parentheses or comments, so comparing AST structure is
whitespace-, comment- and parenthesis-insensitive for free; operands of
commutative operators sort (`a == b` matches `b == a`, `a and b` matches
`b and a`) and same-operator nests flatten (`a + b + c` compares equal
regardless of grouping; `a - (b - c)` does not equal `a - b - c`):

- duplicate conditions in one `if`/`elif` chain — every later branch is
  unreachable;
- duplicate branch bodies in one chain (`else` == then; a
  single-statement list equals the naked statement, so the violation
  points at the statement);
- duplicate operands in one `and`/`or` chain (`a or a`; only the
  outermost link of a same-operator chain reports) and absorbed operands
  (`a or b and a` collapses to `a` — the absorption law, both ways);
- identical ternary branches (`x if c else x` — the condition is dead);
- duplicate positional arguments in one call (`f(w, h, w)`) — keyword
  arguments are exempt (the name documents the slot), all-literal lists
  are exempt (uniform-value APIs like `Rectangle(8, 8, 8, 8)` stay
  quiet), and a literal is never reported as the repeat;
- duplicate except bodies in one `try` — duplicate exception TYPES are
  deliberately not flagged: Python dispatches handlers per type, so the
  types are semantically distinct in a way Dart `case` labels are not.

Canonical-form operators, per the cross-port contract: `==`, `!=` and
`in` sort their two operands (`in` is the Python-only member the
contract adds), `and`/`or`/`+`/`*`/`&`/`|`/`^` flatten and sort; `is`/
`is not`/`not in`, the directional comparisons (`< > <= >=`) and
non-associative arithmetic (`- / % // ** << >> @`) keep operand order.
Only pieces of the same construct are compared; identical conditions in
separate `if` statements are legal (the state may change between them),
and `match` is excluded like upstream's `switch`. Python specifics: an
`if` after an `else:` is by construction a chain link (no elif AST
node), and `except*` groups' bodies are compared like plain handlers.
Severity as above (`warning` default, `--severity error` exits 2);
ignore markers (`# ignore:identical_branches`) are honored — the gate is
ignorable.

```bash
crap4py banned-imports --from 'ui/**' --forbid 'db/*' --message 'UI must not touch DB'
```

`duplicates` scans the whole set at once (duplicates are cross-file by
definition): explicit paths (like every gate) or the default §4-style
selection, plus any `--source` file/dir
(dirs expand recursively to `.py`, missing paths skipped) — the hook for
cross-module duplicate checks. `--exclude` globs (fnmatch on
project-relative paths) drop files from the scan. In the default
`percentage` mode a file fails when its distinct duplicated-token lines
exceed `--threshold`% of its lines; the summary reports the aggregate
share, e.g. `12 files, 0.42% duplicated lines` / `1/12 files over 1.0%
duplication`. `--report clones` reports one violation per
non-overlapping clone group instead — longest first, every occurrence
location in the message: `clone: 52 tokens / 12 lines, 2 occurrences:
alpha.py:3-14; beta.py:3-14` (up to three locations, then `(+N more)`).

Windows are hash-bucket candidates only: each bucket is verified by
token equality (a Rabin-Karp collision no longer reports a false
duplicate), extended to the shared extent, and the groups compete in
one greedy longest-first non-overlapping selection (PMD CPD Match
semantics).

Two opt-in normalizations extend the gate to renamed (Type-2) clones,
both off by default. `--ignore-locals` renames function-local
identifiers to `$L1`-style placeholders in first-use order per outermost
`def`/`async def`/`lambda` scope (parameters except `self`/`cls`,
assigned/unpacking/walrus targets, loop and comprehension variables,
`except as` names, match captures, type params, nested defs; API names
keep their lexemes, and f-strings go opaque). `--ignore-literals` masks
string/numeric literals as `$STR`/`$NUM`. Detection unions the raw pass
with the masked one, so exact copies survive shifted placeholder
numbering. The CPD-parity knobs (upstream d6c3ab6), all off by default:
`--ignore-identifiers` masks every identifier to one `$ID` placeholder
before hashing — structure-only matching that finds clones renamed
anywhere (hard keywords keep their lexemes; soft keywords and builtins
mask too); `--ignore-imports` drops `import`/from-import statements
from tokenization; `--skip-duplicate-files` analyzes a token-identical
file only once, so a copied file is one clone group, not N×N pairs.

`mutation` is mutation testing adapted from the unclebob/mutator research
conclusions (port of crap4dart's 22nd gate): a declarative, category-tagged
operator table (`arithmetic` `+`↔`-`, `*`↔`/`, `/`→`//`; `boundary`
`>`↔`>=`, `<`↔`<=`, `>`↔`<`, `>=`↔`<=`; `equality` `==`↔`!=`; `logical`
`and`↔`or`; `boolean` `True`↔`False`; `constant` `0`↔`1`, `-1`→`0`;
`unary` delete `not`) is matched on the parsed AST — the Python AST gives
operator nodes no positions, so the exact operator token is located as the
single code token between the operand spans (stdlib `tokenize`, never
regex). Covered sites (per the coverage.py report) mutate one at a time and
the project's test suite re-runs per mutant: non-zero exit kills (import
errors included), a clean pass survives, a timeout survives. Uncovered
sites are excluded from the score. The engine clears `__pycache__` and runs
the suite with `PYTHONDONTWRITEBYTECODE=1` — a stale bytecode cache would
shadow mutants (pyc validation is mtime+size, and same-sized mutants land
in the same second). Expensive, and therefore never part of plain analysis:
`mutation` runs only when invoked. Requires `coverage.json` and a `tests/`
(or `test/`) directory; otherwise it skips with a reason (exit 0).

Digest-incremental cache (port of crap4dart 16d9c4c): `.crap-mutation-cache.json`
at the project root remembers each method's source-span digest (FNV-1a of the
exact function text) and the per-site outcomes of the last run that executed
it. Killed mutants of unchanged methods are replayed, not re-executed;
survivors always re-run (the suite may have improved) and coverage gating is
re-derived from the fresh report every run. Offsets are stored method-relative,
so an edit in an earlier method of the same file invalidates only that method;
a tool-version/operator/`--test-args` mismatch wipes the whole cache. The cache
file is disposable build output (gitignored). `--no-cache` opts out.
`--test-args ARG` (repeatable) appends arguments to the per-mutant test command
(e.g. `--test-args tests/test_analyzer.py`) to scope the suite on big repos —
scoping is your responsibility: a mutant is killed only if the scoped suite
would kill it.

Diff-scoped execution + per-mutant test scoping (port of crap4dart 8228585):
`--diff-base REF` intersects candidate sites with the changed lines BEFORE
executing — a site runs only when its containing method intersects the diff
(its own line when it sits outside every method), so a per-PR run pays only
for the mutants of the changed methods (`git diff --unified=0 REF -- '*.py'`
parsed into new-path → added-line numbers; a git failure is a usage error,
exit 1). `--scope-tests` narrows each mutant's run to the derived test file
of the mutated file (`tests/test_<module>.py`), falling back to the full
invocation when no candidate exists — a scoped kill is monotone and cached
as a regular kill, while a scoped survivor (timeouts included) is
re-confirmed once against the full suite and only the confirmed outcome is
reported and cached (`; test-scoped N (M re-confirmed killed)` in the
summary). `--scope-tests` is deliberately not part of the cache meta guard.

End-of-run tree integrity check (port of crap4dart 8b2e8e8): the engine
snapshots every file it will mutate before the loop and re-verifies the whole
tree in an outer `finally` — a per-site restore can be defeated out-of-process
(external write, killed interpreter), so any drift is re-restored AND surfaced
as `INTEGRITY: re-restored after run: <files>` in the summary plus a stderr
warning telling you to treat the run as suspect.

crap4dart's remaining gate-framework features (severity, per-path
threshold entries, yaml config, baselines) are not ported — ports are
flag-based with upstream default thresholds. Ignore comments ARE ported
(crap4dart 8abb64c): see [Ignore markers](#ignore-markers).

## Ignore markers

Gate violations can be suppressed by source comments (port of
crap4dart 8abb64c, adapted from `//` to Python's `#`; marker literals
stay ASCII):

```python
_v = 0xFF00FF00  # crap:ignore                     legacy blanket: every ignorable gate, this line
_v = 0xFF00FF00  # ignore:magic_constants          granular: only this gate, this line
# ignore:magic_constants                         a whole-line marker covers the line below
# ignore:magic_constants                         stacked whole-line markers all apply
# ignore:unused_code                             ...to the code line that ends the run
# ignore-file:unused_code                        file scope: first 5 lines of the file
# ignore-file:                                   empty id = blanket (every ignorable gate)
```

- **Line scope** covers the violation's line (trailing marker counts) or
  the run of whole-line markers stacked directly above it; the walk stops
  at the first non-marker line. Detection is textual, like upstream: a
  marker inside a string literal on a violating line suppresses too.
- **File scope** reads the first 5 lines of the violation's file; markers
  on line 6+ are inert. Violations without a line number (`class-size`,
  `file-naming`, `unused-files`) are silenced by file markers only.
- **One id per marker** (Dart lint style): several markers on one line
  union; an empty id (`# ignore:`) is a blanket; a comma id is ONE
  unknown id — comma lists are inert — and unknown ids match nothing.
- **Legacy parity**: `# crap:ignore` / `# crap:ignore-file` keep their
  blanket scope (substring match, so a `crap:ignore-file` line also
  carries line scope — an upstream quirk kept verbatim).
- **Opt-in per gate**: upstream's per-gate `ignorable: true` config flag
  has no config system to live in here, so the hardcoded
  `ignore_filter.IGNORABLE_GATES` registry IS the opt-in. Suppression
  runs once per gate at the shared violation path (after the scan,
  before printing/exit code), never per-gate copies.

| Subcommand          | Marker gate id       | Line scope | File scope |
|---------------------|----------------------|------------|------------|
| `complexity`        | `complexity`         | yes        | yes        |
| `nesting`           | `nesting`            | yes        | yes        |
| `class-size`        | `class_size`         | — (no line)| yes       |
| `weight-of-class`   | `weight_of_class`    | yes        | yes        |
| `unused-code`       | `unused_code`        | yes        | yes        |
| `unused-files`      | `unused_files`       | — (no line)| yes        |
| `banned-imports`    | `banned_imports`     | yes        | yes        |
| `magic-constants`   | `magic_constants`    | yes        | yes        |
| `scattered-constants`| `scattered_constants`| yes       | yes        |
| `test-assertions`   | `test_assertions`    | yes        | yes        |
| `duplicates`        | `duplication`        | yes        | yes        |
| `mutation`          | `mutation`           | yes        | yes        |
| `tuple-signatures`  | `inline_record_types`| yes        | yes        |
| `string-hazards`    | `string_hazards`     | yes        | yes        |
| `identical-branches`| `identical_branches` | yes        | yes        |
| `file-naming`       | `file_naming`        | — (no line)| yes        |

Not ignorable: `folder-structure` (directory-level findings — nothing
line- or file-scoped can address them), `similarity`/`memory`
(informational, always exit 0), `profile`/`skill`/`reorder` (not gates —
`reorder` is a deterministic source transform).

## `crap4py similarity`

Scans any text corpus (markdown docs, source files) for file-level
near-duplicates — port of the crap4dart `similarity` gate + command
(upstream 2b21966) and the exploration tool for tuning the thresholds
before relying on them. Two files are *similar* when their 8-token shingle
sets overlap enough: Jaccard (overall overlap, `--jaccard`, default 0.8)
or containment (the smaller file largely inside the bigger one,
`--containment`, default 0.95). Comments, imports, code fences and literal
values are normalized away, so only real content counts. Informational:
always exits 0.

```bash
crap4py similarity                          # project's markdown, console report
crap4py similarity docs/                    # scan one directory
crap4py similarity --jaccard 0.5            # threshold overrides
crap4py similarity --min-shingles 30        # smaller files are skipped
crap4py similarity --include '**/*.md' --include '**/*.py'
crap4py similarity --format json            # machine-readable clusters
```

Profiles by extension: `.md`/`.markdown`/`.mdx` drop fenced code blocks;
source files (a 25-extension family) strip comments and import-family
lines and map literals to `S`/`N` placeholders — `.py` via the stdlib
`tokenize`/`ast` path, the rest language-agnostic; anything else compares
as-is. Similar pairs merge transitively into clusters. Files below
`--min-shingles` (default 50) are skipped. `--include`/`--exclude` are
fnmatch globs on project-relative paths (default exclusions: `.git/`,
`.venv/`, `build/`, `coverage/`, `node_modules/`). There is no config file
— the kebab-case flags are the whole config surface, strictly validated.
Dogfood: measured clean on this repo's own md corpus at jaccard 0.6 /
containment 0.9.

## `crap4py memory`

Runs the test suite under `python -m pytest` with a tracemalloc tap
(injected as a pytest plugin, `-p crap4py.memory_plugin`) and attributes
memory to tests: which tests allocate the most (churn — peak live traced
MB during the test window, deterministic across runs) and which leave
memory behind (retained MB — the leak-suspect leaderboard). GC events are
counted via `gc.callbacks` for diagnostics only — counts are
machine-dependent and never a metric (upstream's conclusion, ported).

Semantics differ from the Dart VM's, stated honestly: upstream churn is
reclaimed new-gen MB (gross volume that flowed through); Python has no
cheap gross-allocation counter, so churn here is the peak live footprint
of the test window (alloc-and-free cycles net out) and retained is the
signed net delta — the old-gen-growth analog. Windows cover
setup + call + teardown (the analog of `testStart`..`testDone`), so
fixture allocations are attributed to the test; collection/import
allocations and out-of-window GCs land in a separate unattributed bucket
— reported in every report, never polluting file/test totals. Attribution
is exact where upstream had to infer: pytest nodeids carry the file and
the window taps share one clock, so there is no suite-index mapping, no
clock-offset voting and no straggler reattribution.

```bash
crap4py memory                  # run the suite, print the leaderboards
crap4py memory --top 5          # shorter leaderboards
crap4py memory --format json    # machine-readable report (JSON-only stdout)
crap4py memory --badge badges/memory.svg   # shields-style churn badge
crap4py memory tests/ --ignore=tests/test_x.py   # unknown args go to pytest
```

Informational only — always exits 0 after a run (failing tests included);
usage errors (no Python test suite found, no tests executed) exit 1.
`PYTHONHASHSEED=0` is pinned in the run for churn reproducibility. The
current interpreter must have pytest installed; unittest-style suites run
through it unchanged. Dogfood: 545 tests, 27.7 MB churn across 29 test
files (top: `tests/test_duplicates.py` 4.2 MB); file-level churn identical
across runs (29/29 files, 545/545 tests over two runs), the unattributed
collection/session bucket jitters (8.7–9.2 MB peak) and GC counts moved
110 → 101 — never a metric. `badges/memory.svg` tracks this suite
(green, `--badge-threshold` default 500 MB); the commit-badges workflow
refreshes only the CRAP/coverage badges — the memory badge is written by
`crap4py memory --badge` itself.

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

## `crap4py reorder`

```bash
crap4py reorder [paths...] [--dry-run/-n]
```

Rewrites class bodies so members follow the canonical order — a
deterministic source transform, not a gate (port of crap4dart 24a6f24f,
`reorder` command). The stable partition never sorts alphabetically:
same-kind members keep their source-relative order. The canonical table
(host-idiomatic, porting upstream's constants/static fields/fields/
constructors/static methods/accessors/public/private):

1. class constants — `UPPER_SNAKE_CASE` assignments (annotated or not)
2. class attributes — every other assignment/annotation in the body
3. constructors — `__init__`, `__new__`
4. other dunders — `__repr__`, `__eq__`, `__len__`, ...
5. static/class methods — `@staticmethod` / `@classmethod`
6. properties — `@property` / `@x.setter` / `@x.deleter`
7. public methods
8. private methods — `_name`

A leading docstring stays first. `#` comment lines directly above a member
and its decorators move with it; a comment block separated from its member
by ONE blank line still attaches, TWO blank lines detach — a detached block
rides with the preceding member, so comments are never dropped (upstream
drops them). Whole-line slices only: every member keeps its exact source
text, and the file's line-ending style (LF/CRLF) and trailing-newline state
are preserved.

Never touched: `enum.Enum`-family subclasses (value order is semantics),
bodies containing anything but members (`if`/`try`/nested class/bare
expressions — their order may be semantic), members sharing a source line
(`class A: x = 1` one-liners, semicolon lists — no safe slice points).

Deterministic: same input, same output; already-canonical bodies are left
byte-identical whatever their layout; `fix(fix(x)) == fix(x)`. With
`--dry-run` nothing is written, files that would change are listed, and the
command exits `2` (CI/pre-commit form); without it, files are written and it
exits `0`. Parse errors are skipped with a note, never fatal. The default
selection is the normal file selection (§4 of the spec); explicit paths may
be files or directories.

## Exit codes

| Code | Meaning                                                                |
|------|------------------------------------------------------------------------|
| `0`  | Success (max CRAP ≤ threshold, or no files to analyze); `similarity` always exits 0 (informational). |
| `1`  | Usage error (bad flags, `--changed` + paths, unreadable source).       |
| `2`  | CRAP threshold exceeded (`CRAP threshold exceeded: <max> > <n>` on stderr); also `profile --threshold` and gate-subcommand (`complexity`, `file-naming`, `nesting`, `class-size`, `weight-of-class`, `unused-code`, `unused-files`, `banned-imports`, `magic-constants`, `scattered-constants`, `test-assertions`, `folder-structure`, `duplicates`, `mutation`, `tuple-signatures`) violations, `string-hazards --severity error` and `identical-branches --severity error` violations, and `reorder --dry-run` when any file would change. |

## Cyclomatic complexity rules

Base `1`, then `+1` for each:

- `if`, `for`, `async for`, `while`, `except` handler, ternary (`x if c else y`)
- `match`/`case` clause (Python 3.10+); a `case ... if guard:` adds one more
- `|` or-patterns in a `case`: `len(patterns) − 1`
- Each `for` clause in a comprehension (`ListComp`/`SetComp`/`DictComp`/`GeneratorExp`)
- `and`/`or` short-circuits: `len(values) − 1` per `BoolOp`

Lambda bodies count toward the enclosing method. Nested named function defs
are reported as their own methods and do **not** inflate the parent's
complexity. `__init__` is treated as a normal method (Python convention).

## Cognitive complexity rules

The `complexity --metric cognitive` option scores methods with SonarSource-
style cognitive complexity instead (port of crap4dart's `CognitiveCalculator`;
CRAP always uses cyclomatic). The score starts at `0`, then:

- `+1` per flow break: `if`/`elif`/`else`, `for`/`async for`, `while`,
  `except` handler, every `match` case, ternary, and each *sequence* of
  identical `and`/`or` operators (`a and b and c` = +1, `a and b or c` = +2).
- Structural constructs (everything above except `and`/`or`) pay `1 + depth`
  when nested; logical sequences stay flat.
- `elif` chains are flat: each chained `if` increments once at the depth of
  its originating `if`.

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
    cognitive.py          SonarSource-style cognitive complexity
    complexity_gate.py    `complexity` subcommand: per-method metric gate
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
    magic_constants.py    `magic-constants` gate subcommand (magic literals)
    scattered_constants.py `scattered-constants` gate subcommand (cross-file literal repeats)
    constants.py          shared gate-output literal facts (scattered-constants dogfood)
    test_assertions.py    `test-assertions` gate subcommand (assertion-free tests)
    folder_structure.py   `folder-structure` gate subcommand (loose-file sprawl)
    tuple_signatures.py   `tuple-signatures` gate subcommand (inline tuple shapes)
    reorder.py            `reorder` subcommand (canonical member ordering transform)
    mutation.py           `mutation` gate subcommand (per-mutant test re-runs)
    imports.py            import → project-file resolution (shared by the gates)
    skill.py              `skill` subcommand
  tests/                  stdlib unittest; fixtures/sample.py + coverage.json
```

## Development

A checked-in pre-commit hook runs `crap4py` with its 8.0 threshold on staged
`.py` files, printing the report and blocking the commit when any function
exceeds the threshold — or when the check could not run at all. If crap4py is
not importable by either `python3` or `python`, it warns and lets the commit
through. Enable it once after cloning:

```bash
git config core.hooksPath githooks
```

## Running the tests

```bash
python3 -m unittest discover -s tests -v
```

## License

MIT.
