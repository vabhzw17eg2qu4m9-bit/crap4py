"""Unit + CLI tests for the `string-hazards` subcommand.

Each flagged-shape test kills its check: the fixtures genuinely trigger the
scanned AST shapes (e.g. the escaped-interpolation fixtures always carry the
sibling real interpolation the evidence gate requires), and the assertions
pin messages/counts, so removing or mutating one check fails its test.
"""

import ast
import contextlib
import io
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from crap4py.string_hazards import check_files, file_violations, run, summary


def _violations(source: str) -> list[str]:
    source = textwrap.dedent(source)
    return [v.message for v in file_violations(ast.parse(source), source, "x.py")]


class EscapedInterpolationTest(unittest.TestCase):
    def test_escaped_braces_flagged_with_sibling(self):
        msgs = _violations(
            """
            def greet(name):
                print(f"hello {{name}}!")
                print(f"{name} real")
            """
        )
        self.assertEqual(len(msgs), 1)
        self.assertIn("escaped interpolation '{{name}}' renders literal '{name}'", msgs[0])
        self.assertIn("stray doubled braces", msgs[0])

    def test_no_flag_without_sibling_evidence(self):
        msgs = _violations(
            """
            def shexport(home):
                return f"export HOME={{home}} && echo ok"
            """
        )
        self.assertEqual(msgs, [])

    def test_plain_string_braces_never_flagged(self):
        msgs = _violations(
            """
            template = "hello {{name}}"
            real = f"{name}"
            """
        )
        self.assertEqual(msgs, [])

    def test_once_per_line_and_name(self):
        source = textwrap.dedent(
            """
            def f(name, title):
                print(f"{{name}} and {{name}}")
                print(f"{{name}}")
                print(f"{name}{title}")
            """
        )
        found = file_violations(ast.parse(source), source, "x.py")
        self.assertEqual([v.line for v in found], [3, 4])
        self.assertEqual(found[0].message, found[1].message)

    def test_raw_fstring_scanned_like_plain(self):
        # {{ }} unescaping is a brace rule, identical in raw f-strings.
        msgs = _violations(
            """
            a = rf"{{name}} raw"
            b = f"{name}"
            """
        )
        self.assertEqual(len(msgs), 1)

    def test_brace_escape_around_real_field_not_flagged(self):
        # f'{{{name}}}' is '{' + interpolation + '}': no escaped interpolation.
        msgs = _violations(
            """
            row = f"{{{name}}}"
            other = f"{name}"
            """
        )
        self.assertEqual(msgs, [])


class RegexAnchorTest(unittest.TestCase):
    def test_escaped_dollar_end_anchor_flagged(self):
        msgs = _violations('r1 = re.compile(r"[0-9a-f]{12}\\$")\n')
        self.assertEqual(len(msgs), 1)
        self.assertIn("literal '$' match, not the end anchor", msgs[0])

    def test_escaped_caret_start_anchor_flagged(self):
        msgs = _violations('r2 = re.match(r"\\^abc", text)\n')
        self.assertEqual(len(msgs), 1)
        self.assertIn("literal '^' match, not the start anchor", msgs[0])

    def test_non_raw_survived_escape_flagged_too(self):
        # Unlike Dart, a non-raw '\\$' also keeps the backslash into the regex.
        msgs = _violations('r3 = re.compile("[0-9a-f]{12}\\\\$")\n')
        self.assertEqual(len(msgs), 1)

    def test_plain_anchors_spared(self):
        msgs = _violations(
            """
            r1 = re.compile(r"[0-9a-f]{12}$")
            r2 = re.compile(r"^abc")
            r3 = re.findall("[0-9a-f]{12}$", text)
            """
        )
        self.assertEqual(msgs, [])

    def test_escaped_dollar_outside_re_spared(self):
        msgs = _violations('PATTERN = r"costs \\$"\n')
        self.assertEqual(msgs, [])

    def test_non_literal_pattern_spared(self):
        msgs = _violations(
            """
            pattern = r"x\\$"
            r1 = re.compile(pattern)
            r2 = re.compile(r"[a-z]$", flags=re.MULTILINE)
            """
        )
        self.assertEqual(msgs, [])


class LiteralPatternTest(unittest.TestCase):
    def test_leading_caret_in_replace_flagged(self):
        msgs = _violations('out = path.replace("^~/", "/")\n')
        self.assertEqual(len(msgs), 1)
        self.assertIn("starts with '^'", msgs[0])
        self.assertIn("a regex was probably intended", msgs[0])

    def test_trailing_dollar_in_split_flagged(self):
        msgs = _violations('parts = user.split("abc$")\n')
        self.assertEqual(len(msgs), 1)
        self.assertIn("ends with '$'", msgs[0])

    def test_both_anchors_one_pattern(self):
        msgs = _violations('both = s.replace("^a$", "")\n')
        self.assertEqual(len(msgs), 2)

    def test_regex_call_and_non_literal_spared(self):
        msgs = _violations(
            """
            s.split(re.compile("^"))
            s.replace(var, "")
            re.sub(r"abc$", "", text)
            """
        )
        self.assertEqual(msgs, [])

    def test_re_split_routed_to_regex_kind_not_literal_kind(self):
        msgs = _violations('parts = re.split(r"abc\\$", text)\n')
        self.assertEqual(len(msgs), 1)
        self.assertIn("not the end anchor", msgs[0])

    def test_escaped_trailing_dollar_spared(self):
        msgs = _violations('out = price.replace("abc\\\\$", "")\n')
        self.assertEqual(msgs, [])


class CheckFilesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_broken_file_skipped_and_test_file_excluded(self):
        (self.root / "broken.py").write_text("def f(:\n", encoding="utf-8")
        (self.root / "test_x.py").write_text('x = s.split("^")\n', encoding="utf-8")
        (self.root / "app.py").write_text('x = s.split("^")\n', encoding="utf-8")
        result = check_files(
            [self.root / n for n in ("broken.py", "test_x.py", "app.py")], self.root
        )
        self.assertEqual(result.checked, 1)
        self.assertEqual([v.line for v in result.violations], [1])


class RunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_no_files_exits_0(self):
        self.assertEqual(run([], self.root), 0)

    def test_warning_severity_default_reports_without_failing(self):
        (self.root / "app.py").write_text('out = p.replace("^~/", "/")\n', encoding="utf-8")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = run([], self.root)
        self.assertEqual(code, 0)
        self.assertIn("[WARN]", buffer.getvalue())
        self.assertIn("1 string hazard(s) in 1 files", buffer.getvalue())

    def test_error_severity_exits_2(self):
        (self.root / "app.py").write_text('out = p.replace("^~/", "/")\n', encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            code = run(["--severity", "error"], self.root)
        self.assertEqual(code, 2)

    def test_ignore_marker_suppresses(self):
        (self.root / "app.py").write_text(
            'out = p.replace("^~/", "/")  # ignore:string_hazards\n', encoding="utf-8"
        )
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = run([], self.root)
        self.assertEqual(code, 0)
        self.assertIn("1 files free of string hazards", buffer.getvalue())

    def test_clean_file_exits_0(self):
        (self.root / "app.py").write_text(
            'import re\nr1 = re.compile(r"^[a-z]+$")\nout = s.replace("a", "b")\n',
            encoding="utf-8",
        )
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = run([], self.root)
        self.assertEqual(code, 0)
        self.assertIn("1 files free of string hazards", buffer.getvalue())


class SummaryTest(unittest.TestCase):
    def test_summary(self):
        from crap4py.string_hazards import StringHazard, StringHazardsResult

        self.assertEqual(summary(StringHazardsResult([], 3)), "3 files free of string hazards")
        found = StringHazardsResult([StringHazard("a.py", 1, "m")], 3)
        self.assertEqual(summary(found), "1 string hazard(s) in 3 files")


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
        r = self._run("string-hazards", "--bogus")
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_warning_output_and_exit_0(self):
        (self.root / "app.py").write_text('parts = user.split("abc$")\n', encoding="utf-8")
        r = self._run("string-hazards")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[WARN] app.py:1: literal pattern ends with '$'", r.stdout)

    def test_dispatched_from_main_cli(self):
        r = self._run("string-hazards", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("crap4py string-hazards", r.stdout)


if __name__ == "__main__":
    unittest.main()
