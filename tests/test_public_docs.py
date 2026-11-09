"""Unit + CLI tests for the `public-docs` subcommand."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from crap4py.public_docs import check_files, run, summary


def _kinded(result):
    return [(v.message, v.line) for v in result.violations]


class CheckFilesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _write(self, rel, source):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(source, encoding="utf-8")
        return p

    def test_module_and_function_without_docs_flagged(self):
        p = self._write("bare.py", "def bare():\n    return 1\n")
        result = check_files([p], self.root)
        self.assertEqual(
            _kinded(result),
            [
                ('missing docstring on module "bare"', 1),
                ('missing docstring on function "bare"', 1),
            ],
        )

    def test_documented_declarations_pass(self):
        source = (
            '"""Module docs."""\n'
            "\n"
            "\n"
            "class Thing:\n"
            '    """Class docs."""\n'
            "\n"
            "    def method(self):\n"
            '        """Method docs."""\n'
            "\n"
            "def func():\n"
            '    """Function docs."""\n'
        )
        p = self._write("doced.py", source)
        result = check_files([p], self.root)
        self.assertEqual(result.violations, [])
        self.assertEqual(result.checked, 4)
        self.assertEqual(summary(result), "4 public declarations documented")

    def test_private_names_skipped(self):
        source = (
            '"""Module docs."""\n'
            "\n"
            "def _private():\n    return 1\n"
            "\n"
            "class _Private:\n"
            "    def also_private(self):\n        return 2\n"
            "\n"
            "class Public:\n"
            '    """Docs."""\n'
            "\n"
            "    def __init__(self):\n        self.x = 1\n"
        )
        p = self._write("mixed.py", source)
        result = check_files([p], self.root)
        self.assertEqual(result.violations, [])
        self.assertEqual(result.checked, 2)  # module + Public

    def test_members_of_private_class_exempt(self):
        source = (
            '"""Module docs."""\n'
            "\n"
            "\n"
            "class _Hidden:\n"
            "    def exposed_looking(self):\n"
            "        return 1\n"
        )
        p = self._write("hidden.py", source)
        result = check_files([p], self.root)
        self.assertEqual(result.violations, [])
        self.assertEqual(result.checked, 1)

    def test_nested_public_class_checked(self):
        source = (
            '"""Module docs."""\n'
            "\n"
            "class Outer:\n"
            '    """Docs."""\n'
            "\n"
            "    class Inner:\n"
            "        def deep(self):\n            return 1\n"
        )
        p = self._write("nested.py", source)
        result = check_files([p], self.root)
        self.assertEqual(
            _kinded(result),
            [('missing docstring on class "Inner"', 6), ('missing docstring on method "deep"', 7)],
        )

    def test_exclude_skips_file(self):
        p = self._write("bare.py", "def bare():\n    return 1\n")
        result = check_files([p], self.root, excludes=["bare.py"])
        self.assertEqual(result.violations, [])
        self.assertEqual(result.checked, 0)

    def test_unparseable_file_skipped(self):
        p = self._write("broken.py", "def broken(:\n    return 1\n")
        result = check_files([p], self.root)
        self.assertEqual(result.violations, [])
        self.assertEqual(result.checked, 0)

    def test_test_files_skipped(self):
        p = self._write("tests/bare.py", "def bare():\n    return 1\n")
        result = check_files([p], self.root)
        self.assertEqual(result.checked, 0)

    def test_summary_counts_violations(self):
        p = self._write("bare.py", "def bare():\n    return 1\n")
        result = check_files([p], self.root)
        self.assertEqual(summary(result), "2/2 public declarations missing docstrings")


class RunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_no_files_exits_0(self):
        self.assertEqual(run([], self.root), 0)

    def test_violations_exit_2(self):
        (self.root / "bare.py").write_text("def bare():\n    return 1\n", encoding="utf-8")
        self.assertEqual(run([], self.root), 2)

    def test_documented_exits_0(self):
        (self.root / "ok.py").write_text('"""Docs."""\n', encoding="utf-8")
        self.assertEqual(run([], self.root), 0)

    def test_line_marker_suppresses(self):
        (self.root / "bare.py").write_text(
            '"""Docs."""\n\n\ndef bare():  # ignore:public_docs\n    return 1\n',
            encoding="utf-8",
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
        r = self._run("public-docs", "--bogus")
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_violation_exits_2_with_message(self):
        (self.root / "bare.py").write_text("def bare():\n    return 1\n", encoding="utf-8")
        r = self._run("public-docs")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn('bare.py:1: missing docstring on function "bare"', r.stdout)


if __name__ == "__main__":
    unittest.main()
