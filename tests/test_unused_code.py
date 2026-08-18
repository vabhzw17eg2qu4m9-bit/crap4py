"""Unit + CLI tests for the `unused-code` subcommand."""

import ast
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from crap4py.unused_code import check_files, run, summary, unused_names


def _names(source: str) -> set[str]:
    return set(unused_names(ast.parse(textwrap.dedent(source))))


class UnusedNamesTest(unittest.TestCase):
    def test_unused_private_function_flagged(self):
        names = _names(
            """
            def _helper():
                return 1
            """
        )
        self.assertEqual(names, {"_helper"})

    def test_referenced_private_function_not_flagged(self):
        names = _names(
            """
            def _helper():
                return 1
            def public():
                return _helper()
            """
        )
        self.assertEqual(names, set())

    def test_unused_private_assignment_flagged(self):
        names = _names("_unused = 1\n_used = 2\nprint(_used)\n")
        self.assertEqual(names, {"_unused"})

    def test_recursive_self_reference_still_counts_as_used(self):
        names = _names(
            """
            def _rec(n):
                return _rec(n - 1) if n else 0
            """
        )
        self.assertEqual(names, set())

    def test_unused_private_class_flagged(self):
        names = _names(
            """
            class _Internal:
                pass
            """
        )
        self.assertEqual(names, {"_Internal"})

    def test_dunder_names_never_flagged(self):
        names = _names('__version__ = "1.0"\n')
        self.assertEqual(names, set())

    def test_public_names_never_flagged(self):
        names = _names("unused_public = 1\n")
        self.assertEqual(names, set())

    def test_annassign_target(self):
        names = _names("_flag: bool = False\n")
        self.assertEqual(names, {"_flag"})

    def test_cross_class_private_access_not_flagged(self):
        """0.7.1 regression: declaring a private name must not strip the
        name from the reference set — cross-class access in one module counts."""
        names = _names(
            """
            def _helper():
                return 1

            class Consumer:
                def run(self):
                    return _helper()

            class Producer:
                def make(self):
                    return _helper()
            """
        )
        self.assertEqual(names, set())


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

    def test_violation_reported_with_line(self):
        self._write("dead.py", "def _gone():\n    return 1\n")
        result = check_files([self.root / "dead.py"], self.root)
        self.assertEqual(len(result.violations), 1)
        self.assertEqual(result.violations[0].file, "dead.py")
        self.assertEqual(result.violations[0].line, 1)
        self.assertEqual(result.violations[0].name, "_gone")
        self.assertEqual(summary(result), "1 unused private declarations in 1 files")

    def test_test_files_skipped_entirely(self):
        self._write("test_dead.py", "def _gone():\n    return 1\n")
        result = check_files([self.root / "test_dead.py"], self.root)
        self.assertEqual((result.checked, result.violations), (0, []))


class RunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_partial_selection_skips_with_exit_0(self):
        (self.root / "dead.py").write_text("def _gone():\n    return 1\n", encoding="utf-8")
        self.assertEqual(run(["dead.py"], self.root), 0)

    def test_violations_exit_2(self):
        (self.root / "dead.py").write_text("def _gone():\n    return 1\n", encoding="utf-8")
        self.assertEqual(run([], self.root), 2)

    def test_clean_module_exits_0(self):
        (self.root / "clean.py").write_text(
            "def _used_by_public():\n    return 1\n\n\ndef public():\n"
            "    return _used_by_public()\n",
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

    def test_partial_selection_skip_message(self):
        (self.root / "dead.py").write_text("def _gone():\n    return 1\n", encoding="utf-8")
        r = self._run("unused-code", "dead.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("not meaningful for a partial selection", r.stdout)

    def test_bad_flag_exits_1(self):
        r = self._run("unused-code", "--bogus")
        self.assertEqual(r.returncode, 1, r.stderr)


if __name__ == "__main__":
    unittest.main()
