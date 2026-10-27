"""Unit + CLI tests for the `scattered-constants` subcommand."""

import ast
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from crap4py.scattered_constants import (
    MIN_FILES,
    MIN_VALUE,
    _violations,
    check_files,
    run,
    summary,
)


def _scan(*sources: str) -> tuple[dict, dict]:
    """Scan the given sources as files f0.py, f1.py, ... and return (sites, constants)."""
    from crap4py.scattered_constants import _scan_file

    sites: dict = {}
    constants: dict = {}
    for i, source in enumerate(sources):
        _scan_file(
            ast.parse(textwrap.dedent(source)), f"f{i}.py", 4, MIN_VALUE, sites, constants, []
        )
    return sites, constants


def _aliases(*sources: str) -> list[tuple]:
    """Scan the given sources and return the collected alias records."""
    from crap4py.scattered_constants import _scan_file

    aliases: list[tuple] = []
    for i, source in enumerate(sources):
        _scan_file(ast.parse(textwrap.dedent(source)), f"f{i}.py", 4, MIN_VALUE, {}, {}, aliases)
    return aliases


def _messages(*sources: str) -> list[str]:
    sites, constants = _scan(*sources)
    return [v.message for v in _violations(sites, constants, MIN_FILES)]


class RegressionTest(unittest.TestCase):
    """The constant-regression rule: an existing constant flags re-uses at once."""

    def test_single_file_single_occurrence_flags(self):
        # Killing test: the registry rule does not wait for min_files — a bare
        # re-introduction next to the declaration flags immediately.
        msgs = _messages("TIMEOUT = 86400\nx = 86400\n")
        self.assertEqual(len(msgs), 1)
        self.assertIn(
            "hardcoded 86400 duplicates constant `TIMEOUT` (f0.py:1) — reference the "
            "constant instead of repeating the literal",
            msgs,
        )

    def test_declaring_site_never_self_flags(self):
        # Killing test: the winning declaration is the reference point, not a finding.
        self.assertEqual(_messages("TIMEOUT = 86400\n"), [])

    def test_cross_file_single_occurrence_flags(self):
        sites, constants = _scan("TIMEOUT = 86400\n", "y = 86400\n")
        vs = _violations(sites, constants, MIN_FILES)
        self.assertEqual([(v.file, v.line) for v in vs], [("f1.py", 1)])
        self.assertIn("duplicates constant `TIMEOUT` (f0.py:1)", vs[0].message)

    def test_duplicate_local_const_flags(self):
        # Killing test: N files each declaring their own const with the same
        # value is precisely the scatter — the second declaration flags.
        sites, constants = _scan("TIMEOUT = 86400\n", "TIMEOUT = 86400\n")
        vs = _violations(sites, constants, MIN_FILES)
        self.assertEqual([(v.file, v.line) for v in vs], [("f1.py", 1)])
        self.assertIn("duplicates constant `TIMEOUT` (f0.py:1)", vs[0].message)

    def test_first_declaration_wins(self):
        msgs = _messages("A = 86400\n", "B = 86400\n", "C = 86400\n")
        self.assertEqual(len(msgs), 2)
        self.assertTrue(all("duplicates constant `A` (f0.py:1)" in m for m in msgs))

    def test_class_level_and_annotated_constants_registered(self):
        # Killing test: the annotated duplicate declaration (f1.py:1) and the
        # bare re-use (f1.py:2) both flag against the first declaration.
        msgs = _messages(
            """
            class Grid:
                LIMIT = 86400
            """,
            "CEILING: int = 86400\nz = 86400\n",
        )
        self.assertEqual(len(msgs), 2)
        self.assertTrue(all("duplicates constant `LIMIT` (f0.py:3)" in m for m in msgs))

    def test_string_and_float_constants_registered(self):
        msgs = _messages("BRAND = 'brand'\nRATIO = 86400.5\n", "x = 'brand'\ny = 86400.5\n")
        self.assertEqual(len(msgs), 2)
        self.assertIn("hardcoded 'brand' duplicates constant `BRAND`", msgs[0])
        self.assertIn("hardcoded 86400.5 duplicates constant `RATIO`", msgs[1])

    def test_bool_and_none_constants_not_registered(self):
        # bool/None are never literal facts; their sites are never collected either.
        self.assertEqual(_messages("FLAG = True\nx = True\ny = True\nz = True\n"), [])

    def test_complex_constants_not_registered(self):
        # Complex literals have no upstream analog and are never sites either.
        self.assertEqual(_messages("PHASE = 1000j\nx = 1000j\ny = 1000j\nz = 1000j\n"), [])

    def test_computed_initializer_literals_stay_ordinary(self):
        # Only a plain literal initializer is the named constant; literals
        # nested in a computed ALL_CAPS initializer are ordinary sites.
        msgs = _messages("PALETTE = mix(86400)\n", "x = 86400\n", "y = 86400\n")
        self.assertEqual(len(msgs), 3)
        self.assertNotIn("duplicates constant", "\n".join(msgs))
        self.assertIn("repeats in 3 files (3 occurrences)", msgs[0])

    def test_lowercase_assignment_not_registered(self):
        # Python's const convention is ALL_CAPS; lowercase names are variables.
        msgs = _messages("timeout = 86400\n", "x = 86400\n", "y = 86400\n")
        self.assertEqual(len(msgs), 3)
        self.assertNotIn("duplicates constant", "\n".join(msgs))

    def test_regression_takes_precedence_over_scatter(self):
        # With a constant present, even a value in 3 files is all regressions.
        msgs = _messages("TIMEOUT = 86400\n", "x = 86400\n", "y = 86400\nz = 86400\n")
        self.assertEqual(len(msgs), 3)
        self.assertEqual(sum("duplicates constant" in m for m in msgs), 3)


class DistinctFileCountTest(unittest.TestCase):
    """The scatter path (no constant yet): distinct FILES per value, not occurrences."""

    def test_in_file_repeats_never_flag(self):
        # Killing test: an occurrence counter (the magic-constants rule)
        # flags these three lines; distinct-file counting must not.
        self.assertEqual(_messages('a = "brand"\nb = "brand"\nc = "brand"\n'), [])

    def test_three_files_flag_every_occurrence(self):
        msgs = _messages('x = "brand"\n', 'y = "brand"\n', 'z = "brand"\n')
        self.assertEqual(len(msgs), 3)
        self.assertIn(
            "literal 'brand' repeats in 3 files (3 occurrences) — extract a shared "
            "constant and reference it from every site; verify all occurrences mean "
            "the same thing first",
            msgs[0],
        )

    def test_two_files_below_threshold_clean(self):
        # Killing test: the min_files boundary — 2 files = clean, 3 = flagged.
        self.assertEqual(_messages('x = "brand"\n', 'y = "brand"\n'), [])
        self.assertEqual(len(_messages('x = "brand"\n', 'y = "brand"\n', 'z = "brand"\n')), 3)

    def test_multiple_occurrences_per_file_all_reported(self):
        msgs = _messages('x = "brand"\ny = "brand"\n', 'y = "brand"\n', 'z = "brand"\n')
        self.assertEqual(len(msgs), 4)

    def test_occurrences_sorted_by_file_and_line(self):
        from crap4py.scattered_constants import _scan_file

        sites, constants = _scan('b = "brand"\na = "brand"\n')
        _scan_file(ast.parse('c = "brand"\n'), "a.py", 4, MIN_VALUE, sites, constants, [])
        vs = _violations(sites, constants, 2)
        self.assertEqual([(v.file, v.line) for v in vs], [("a.py", 1), ("f0.py", 1), ("f0.py", 2)])

    def test_min_files_knob_moves_the_boundary(self):
        sites, constants = _scan('x = "brand"\n', 'y = "brand"\n')
        self.assertEqual(_violations(sites, constants, 3), [])
        self.assertEqual(len(_violations(sites, constants, 2)), 2)


class SemanticValueTest(unittest.TestCase):
    """Values compare semantically: spellings of one number are one fact."""

    def test_digit_separators_and_radix_merge(self):
        msgs = _messages("x = 1_000\n", "y = 1000\n", "z = 0x3E8\n")
        self.assertEqual(len(msgs), 3)
        self.assertIn("literal 1000 repeats", msgs[0])

    def test_int_and_float_stay_distinct(self):
        self.assertEqual(_messages("x = 1000\n", "y = 1000.0\n"), [])

    def test_string_and_number_spellings_stay_distinct(self):
        # Killing test for the typed key namespaces: if the string '86400'
        # merged with the int 86400, one 6-occurrence bucket would form.
        msgs = _messages(
            "x = 86400\ns = '86400'\n",
            "x = 86400\ns = '86400'\n",
            "x = 86400\ns = '86400'\n",
        )
        self.assertEqual(len(msgs), 6)
        joined = "\n".join(msgs)
        self.assertIn("literal 86400 repeats in 3 files (3 occurrences)", joined)
        self.assertIn("literal '86400' repeats in 3 files (3 occurrences)", joined)


class CorpusGuardTest(unittest.TestCase):
    """The constant registry honors the site scan's coincidence doctrine."""

    def test_small_int_constants_never_seed_corpus(self):
        # Killing test: the corpus minValue guard — small-int constants
        # (exit codes 0/1/2) are coincidence and must not collide in the
        # regression rule. Mutate the guard and the registry fills.
        from crap4py.scattered_constants import _scan_file

        constants: dict = {}
        for i, value in enumerate((0, 1, 2)):
            _scan_file(
                ast.parse(f"EXIT_{i} = {value}\n"), f"f{i}.py", 4, MIN_VALUE, {}, constants, []
            )
        self.assertEqual(constants, {})

    def test_small_int_constants_in_three_files_do_not_warn(self):
        msgs = _messages("EXIT_OK = 0\n", "FAIL = 1\n", "RETRY = 2\n")
        self.assertEqual(msgs, [])

    def test_const_decl_initializer_under_min_value_not_flagged(self):
        # Killing test: const-decl initializer hits go through the same
        # minValue guard as use-site literals — 3 files declaring int 1
        # is coincidence, not scatter.
        self.assertEqual(_messages("A = 1\n", "B = 1\n", "C = 1\n"), [])

    def test_const_decl_initializer_over_min_value_flags(self):
        # Over the threshold a duplicate declaration is the scatter itself.
        msgs = _messages("A = 86400\n", "B = 86400\n")
        self.assertEqual(len(msgs), 1)
        self.assertIn("duplicates constant `A` (f0.py:1)", msgs[0])

    def test_small_string_constant_still_seeds_corpus(self):
        # The guard is numbers-only: strings key on value, min_length is
        # the site-side length doctrine and the registry keeps short ones
        # (a use-site can never collide with them, sites are length-guarded).
        msgs = _messages("AB = 'ab'\n", "x = 'ab'\n")
        self.assertEqual(msgs, [])


class AliasTest(unittest.TestCase):
    """The pure-alias rule: a private ALL_CAPS alias of another name flags."""

    def test_private_alias_flags(self):
        # Killing test: `_APP = BRAND` adds a name without information.
        # Collection keeps every ALL_CAPS alias; the private filter bites
        # at violation time.
        aliases = _aliases("BRAND = 'brand'\n_APP = BRAND\n")
        self.assertEqual(aliases, [("_APP", "BRAND", "f0.py", 2)])
        from crap4py.scattered_constants import _alias_violations

        vs = _alias_violations(aliases, True)
        self.assertEqual([(v.file, v.line) for v in vs], [("f0.py", 2)])
        self.assertEqual(
            vs[0].message, "alias `_APP` adds no information — reference `BRAND` directly"
        )

    def test_public_alias_exempt(self):
        # Re-export shims are a deliberate API shape: collected, never flagged.
        aliases = _aliases("BRAND = 'brand'\nAPP = BRAND\n")
        self.assertEqual(aliases, [("APP", "BRAND", "f0.py", 2)])
        from crap4py.scattered_constants import _alias_violations

        self.assertEqual(_alias_violations(aliases, True), [])

    def test_computed_initializer_not_alias(self):
        # A computed initializer adds information; only a bare Name is an alias.
        self.assertEqual(_aliases("BRAND = 'brand'\nAPP = BRAND + '!'\n"), [])
        self.assertEqual(_aliases("BRAND = 'brand'\nAPP = f(BRAND)\n"), [])

    def test_flag_aliases_false_disables_rule(self):
        aliases = _aliases("BRAND = 'brand'\n_APP = BRAND\n")
        from crap4py.scattered_constants import _alias_violations

        self.assertEqual(_alias_violations(aliases, False), [])

    def test_non_all_caps_assignment_not_collected(self):
        # The const convention is ALL_CAPS; lowercase aliases are variables.
        self.assertEqual(_aliases("brand = 'brand'\n_app = brand\n"), [])

    def test_check_files_default_flags_and_suppresses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text(
                "BRAND = 'brand'\n_APP = BRAND\nprint(_APP)\n", encoding="utf-8"
            )
            result = check_files([root / "a.py"], root)
            self.assertEqual(len(result.violations), 1)
            self.assertIn("alias `_APP` adds no information", result.violations[0].message)
            self.assertEqual(summary(result), "1 pure alias(es) in 1 files")
            self.assertEqual(check_files([root / "a.py"], root, flag_aliases=False).violations, [])


class FalsePositiveTest(unittest.TestCase):
    """Upstream false-positive controls pinned."""

    def test_short_strings_ignored(self):
        self.assertEqual(_messages("x = 'ab'\n", "y = 'ab'\n", "z = 'ab'\n"), [])

    def test_small_numbers_ignored(self):
        self.assertEqual(_messages("x = 100\n", "y = 100\n", "z = 100\n"), [])

    def test_number_magnitude_boundary(self):
        # Killing test: magnitude AT min_value is coincidence, above it is a fact.
        self.assertEqual(len(_messages("x = 101\n", "y = 101\n", "z = 101\n")), 3)

    def test_negative_numbers_count_by_magnitude(self):
        self.assertEqual(len(_messages("x = -86400\n", "y = -86400\n", "z = -86400\n")), 3)

    def test_dict_keys_not_flagged(self):
        self.assertEqual(
            _messages("x = {'theme': 1}\n", "y = {'theme': 2}\n", "z = {'theme': 3}\n"), []
        )

    def test_index_expressions_not_flagged(self):
        self.assertEqual(
            _messages("x = m['status']\n", "y = m['status']\n", "z = m['status']\n"), []
        )

    def test_match_case_patterns_not_flagged(self):
        msgs = _messages(
            """
            def f(x):
                match x:
                    case 'ready':
                        return 1
            """,
            """
            def f(x):
                match x:
                    case 'ready':
                        return 2
            """,
            """
            def f(x):
                match x:
                    case 'ready':
                        return 3
            """,
        )
        self.assertEqual(msgs, [])

    def test_fstrings_skipped(self):
        # Killing test: f-string static parts are not literal facts — the
        # part 'brand ' is >= min_length, so collecting JoinedStr children
        # (as ast.walk does) makes this fail.
        self.assertEqual(
            _messages("x = f'brand {a}'\n", "y = f'brand {b}'\n", "z = f'brand {c}'\n"), []
        )

    def test_fstring_part_not_a_regression(self):
        # The static part 'brand' matches the existing constant exactly;
        # treating it as a site would flag a regression.
        self.assertEqual(_messages("BRAND = 'brand'\n", "x = f'brand{a}'\n"), [])

    def test_bool_and_none_skipped(self):
        self.assertEqual(_messages("x = True\n", "y = True\n", "z = True\n"), [])
        self.assertEqual(_messages("x = None\n", "y = None\n", "z = None\n"), [])

    def test_complex_literals_skipped(self):
        # Python-specific literal kind with no upstream analog: not a
        # string/int/float fact.
        self.assertEqual(_messages("x = 1000j\n", "y = 1000j\n", "z = 1000j\n"), [])

    def test_import_specifiers_are_not_strings(self):
        self.assertEqual(_messages("import os\n", "import os\n", "from os import path\n"), [])


class CheckFilesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _write(self, rel, source):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(source), encoding="utf-8")
        return p

    def test_cross_file_scatter_and_summary(self):
        for name in ("a.py", "b.py", "c.py"):
            self._write(name, 'x = "brand"\n')
        result = check_files([self.root / n for n in ("a.py", "b.py", "c.py")], self.root)
        self.assertEqual(len(result.violations), 3)
        self.assertEqual(result.checked, 3)
        self.assertEqual(summary(result), "3 scattered-literal occurrence(s) in 3 files")

    def test_regression_via_check_files(self):
        self._write("a.py", "TIMEOUT = 86400\n")
        self._write("b.py", "x = 86400\n")
        result = check_files([self.root / "a.py", self.root / "b.py"], self.root)
        self.assertEqual(len(result.violations), 1)
        self.assertEqual((result.violations[0].file, result.violations[0].line), ("b.py", 1))
        self.assertEqual(summary(result), "1 hardcoded re-use(s) of existing constants in 2 files")

    def test_mixed_summary_counts_both_kinds(self):
        self._write("a.py", "TIMEOUT = 86400\n")
        self._write("b.py", "x = 86400\n")
        for name in ("c.py", "d.py", "e.py"):
            self._write(name, 'x = "brand"\n')
        result = check_files(
            [self.root / n for n in ("a.py", "b.py", "c.py", "d.py", "e.py")], self.root
        )
        self.assertEqual(
            summary(result),
            "1 hardcoded re-use(s) of existing constants, "
            "3 scattered-literal occurrence(s) in 5 files",
        )

    def test_test_files_skipped(self):
        self._write("tests/test_x.py", 'x = "brand"\n')
        self._write("a.py", 'x = "brand"\n')
        self._write("b.py", 'x = "brand"\n')
        result = check_files(
            [self.root / "tests/test_x.py", self.root / "a.py", self.root / "b.py"], self.root
        )
        self.assertEqual((result.violations, result.checked), ([], 2))

    def test_broken_file_skipped(self):
        p = self._write("broken.py", "def f(:\n")
        result = check_files([p], self.root)
        self.assertEqual((result.violations, result.checked), ([], 0))

    def test_clean_summary(self):
        result = check_files([], self.root)
        self.assertEqual(summary(result), "0 files scanned, no literals repeat across files")


class RunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_no_files_exits_0(self):
        self.assertEqual(run([], self.root), 0)

    def test_violations_exit_2(self):
        for name in ("a.py", "b.py", "c.py"):
            (self.root / name).write_text('x = "brand"\n', encoding="utf-8")
        self.assertEqual(run([], self.root), 2)

    def test_clean_exit_0(self):
        (self.root / "a.py").write_text('x = "brand"\n', encoding="utf-8")
        self.assertEqual(run([], self.root), 0)


class CliSubprocessTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "crap4py", *args],
            cwd=str(self.root),
            capture_output=True,
            text=True,
        )

    def test_bad_flag_exits_1(self):
        r = self._run("scattered-constants", "--bogus")
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_violation_exits_2_with_output(self):
        for name in ("a.py", "b.py", "c.py"):
            (self.root / name).write_text('x = "brand"\n', encoding="utf-8")
        r = self._run("scattered-constants")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("a.py:1: literal 'brand' repeats in 3 files", r.stdout)
        self.assertIn("3 scattered-literal occurrence(s) in 3 files", r.stdout)

    def test_regression_output_names_constant(self):
        (self.root / "a.py").write_text("TIMEOUT = 86400\n", encoding="utf-8")
        (self.root / "b.py").write_text("x = 86400\n", encoding="utf-8")
        r = self._run("scattered-constants")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("b.py:1: hardcoded 86400 duplicates constant `TIMEOUT` (a.py:1)", r.stdout)

    def test_min_files_flag_relaxes_threshold(self):
        for name in ("a.py", "b.py"):
            (self.root / name).write_text('x = "brand"\n', encoding="utf-8")
        self.assertEqual(self._run("scattered-constants").returncode, 0)
        r = self._run("scattered-constants", "--min-files", "2")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("repeats in 2 files (2 occurrences)", r.stdout)

    def test_private_alias_flags_and_no_aliases_disables(self):
        # Killing test: the default config flags the alias; --no-aliases
        # (the ports' form of upstream's aliases: false) switches it off.
        (self.root / "a.py").write_text(
            "BRAND = 'brand'\n_APP = BRAND\nprint(_APP)\n", encoding="utf-8"
        )
        r = self._run("scattered-constants")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("alias `_APP` adds no information — reference `BRAND` directly", r.stdout)
        self.assertIn("1 pure alias(es) in 1 files", r.stdout)
        r = self._run("scattered-constants", "--no-aliases")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no literals repeat across files", r.stdout)


if __name__ == "__main__":
    unittest.main()
