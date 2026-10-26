"""Unit + CLI tests for the `magic-constants` subcommand."""

import ast
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from crap4py.magic_constants import check_files, file_violations, run, summary


def _violations(source: str) -> list[str]:
    source = textwrap.dedent(source)
    return [v.message for v in file_violations(ast.parse(source), source, "x.py")]


class HexColorTest(unittest.TestCase):
    def test_hex_outside_constant_flagged(self):
        msgs = _violations(
            """
            color = 0xFF00FF
            paint(0x80AA00BB)
            """
        )
        self.assertEqual(msgs.count("hex color outside a constant declaration"), 2)

    def test_uppercase_assignment_value_exempt(self):
        msgs = _violations(
            """
            PRIMARY = 0xFF00FF
            _ACCENT: int = 0x80AA00BB
            OK = build(0x112233)
            """
        )
        self.assertNotIn("hex color outside a constant declaration", msgs)

    def test_lowercase_assignment_not_exempt(self):
        msgs = _violations("primary = 0xFF00FF\n")
        self.assertIn("hex color outside a constant declaration", msgs)

    def test_short_hex_not_flagged(self):
        msgs = _violations("x = 0xFFF\ny = 0x12345\n")
        self.assertNotIn("hex color outside a constant declaration", msgs)

    def test_decimal_int_not_hex_flag(self):
        msgs = _violations("x = 16711935\n")
        self.assertNotIn("hex color outside a constant declaration", msgs)

    def test_class_level_constant_exempt(self):
        msgs = _violations(
            """
            class Theme:
                PRIMARY = 0xFF00FF
                def paint(self):
                    return 0x00AA00
            """
        )
        self.assertEqual(msgs.count("hex color outside a constant declaration"), 1)


class RepeatedLiteralTest(unittest.TestCase):
    def test_repeated_literal_each_occurrence(self):
        msgs = _violations(
            """
            a = "hello"
            b = "hello"
            c = "hello"
            """
        )
        self.assertEqual(msgs.count("literal hello repeats 3 times — extract a named constant"), 3)

    def test_two_occurrences_below_threshold_ignored(self):
        self.assertEqual(_violations('a = "hello"\nb = "hello"\n'), [])

    def test_short_strings_ignored(self):
        self.assertEqual(_violations('a = "ab"\nb = "ab"\nc = "ab"\n'), [])

    def test_repeated_number_by_raw_lexeme(self):
        msgs = _violations("a = 3.14\nb = 3.14\nc = 3.14\n")
        self.assertEqual(len(msgs), 3)
        self.assertIn("literal 3.14 repeats 3 times — extract a named constant", msgs)

    def test_same_value_different_lexemes_not_merged(self):
        self.assertEqual(_violations("a = 10\nb = 0xA\nc = 0o12\n"), [])

    def test_adjacent_strings_merge(self):
        msgs = _violations('a = "he" "llo"\nb = "hello"\nc = "hello"\n')
        self.assertEqual(msgs.count("literal hello repeats 3 times — extract a named constant"), 3)

    def test_fstring_skipped(self):
        # Killing test: f-string static parts are not literal facts — the
        # part 'brand ' is >= min-length, so collecting JoinedStr children
        # (as the walk does) flags all three lines.
        self.assertEqual(_violations('a = f"brand {a}"\nb = f"brand {b}"\nc = f"brand {c}"\n'), [])

    def test_fstring_long_part_not_repeated(self):
        # Killing test (reviewer repro): the static part 'Failed to load '
        # repeats 3 times in one file and must not count.
        self.assertEqual(
            _violations(
                'a = f"Failed to load {u}"\nb = f"Failed to load {v}"\nc = f"Failed to load {w}"\n'
            ),
            [],
        )

    def test_interpolation_expression_literals_still_count(self):
        # Upstream parity: a literal inside the interpolation expression is
        # an ordinary literal, only the static parts are skipped.
        msgs = _violations("a = f'{86400}'\nb = f'{86400}'\nc = f'{86400}'\n")
        self.assertEqual(len(msgs), 3)
        self.assertIn("literal 86400 repeats 3 times — extract a named constant", msgs)

    def test_bool_none_never_counted(self):
        self.assertEqual(_violations("x = True\ny = True\nz = True\n"), [])
        self.assertEqual(_violations("x = None\ny = None\nz = None\n"), [])

    def test_const_line_occurrences_exempt_from_duplicates_too(self):
        msgs = _violations(
            """
            THEME = "brand"
            a = "brand"
            b = "brand"
            """
        )
        self.assertEqual(msgs, [])

    def test_const_initializer_subtree_fully_exempt(self):
        msgs = _violations(
            """
            PALETTE = mix(0xFFFFFF, {"edge": 0x000000})
            """
        )
        self.assertEqual(msgs, [])


class IdentifierPositionTest(unittest.TestCase):
    """Strings in identifier positions name protocol fields, not constants."""

    def test_dict_key_strings_skipped(self):
        self.assertEqual(
            _violations(
                """
                a = {"theme": 1}
                b = {"theme": 2}
                c = {"theme": 3}
                """
            ),
            [],
        )

    def test_dict_value_strings_still_counted(self):
        msgs = _violations(
            """
            a = {"k": "brand"}
            b = {"k": "brand"}
            c = {"k": "brand"}
            """
        )
        self.assertEqual(len(msgs), 3)

    def test_index_expression_strings_skipped(self):
        self.assertEqual(
            _violations(
                """
                a = m["status"]
                b = m["status"]
                c = m["status"]
                """
            ),
            [],
        )

    def test_match_case_pattern_strings_skipped(self):
        self.assertEqual(
            _violations(
                """
                def f(x):
                    match x:
                        case "ready":
                            return 1
                        case "ready":
                            return 2
                        case "ready":
                            return 3
                """
            ),
            [],
        )

    def test_call_argument_strings_still_counted(self):
        msgs = _violations('a = f("brand")\nb = f("brand")\nc = f("brand")\n')
        self.assertEqual(len(msgs), 3)


class CheckFilesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_fstring_part_clean_across_files(self):
        # Three files each using the f-string once: clean everywhere (the
        # reviewer's CLI repro, killed pre-fix only in the in-file form).
        for name in ("a.py", "b.py", "c.py"):
            (self.root / name).write_text(
                'def g(u):\n    return f"Failed to load {u}"\n', encoding="utf-8"
            )
        result = check_files([self.root / n for n in ("a.py", "b.py", "c.py")], self.root)
        self.assertEqual((result.violations, result.checked), ([], 3))

    def test_broken_file_skipped(self):
        p = self.root / "broken.py"
        p.write_text("def f(:\n", encoding="utf-8")
        result = check_files([p], self.root)
        self.assertEqual((result.violations, result.checked), ([], 0))

    def test_summary_and_exit_zero_clean_file(self):
        (self.root / "clean.py").write_text("X = 1\n", encoding="utf-8")
        result = check_files([self.root / "clean.py"], self.root)
        self.assertEqual(result.violations, [])
        self.assertEqual(summary(result), "1 files free of magic constants")

    def test_test_files_skipped(self):
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_x.py").write_text("a = 0xFF00FF\n", encoding="utf-8")
        result = check_files([self.root / "tests" / "test_x.py"], self.root)
        self.assertEqual(result.checked, 0)


class RunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_no_files_exits_0(self):
        self.assertEqual(run([], self.root), 0)

    def test_violations_exit_2(self):
        (self.root / "app.py").write_text("color = 0xFF00FF\n", encoding="utf-8")
        self.assertEqual(run([], self.root), 2)

    def test_clean_file_exits_0(self):
        (self.root / "app.py").write_text(
            'RED = 0xFF0000\ndef greet(name):\n    return f"hi {name}"\n', encoding="utf-8"
        )
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
        r = self._run("magic-constants", "--bogus")
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_violation_exits_2_with_output(self):
        (self.root / "app.py").write_text("color = 0xFF00FF\n", encoding="utf-8")
        r = self._run("magic-constants")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("app.py:1: hex color outside a constant declaration", r.stdout)


if __name__ == "__main__":
    unittest.main()
