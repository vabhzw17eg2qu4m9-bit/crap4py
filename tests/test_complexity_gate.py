"""Unit + CLI tests for the `complexity` subcommand (complexity gate)."""

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from crap4py.complexity_gate import check_files, run, summary

_ElifChain = (
    "def classify(x):\n"
    "    if x < 0:\n"
    "        return 'neg'\n"
    "    elif x == 0:\n"
    "        return 'zero'\n"
    "    elif x < 10:\n"
    "        return 'small'\n"
    "    elif x < 100:\n"
    "        return 'medium'\n"
    "    return 'large'\n"
)
# four flat ifs -> cognitive 4, cyclomatic 5


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

    def test_cognitive_scores_flat_chains_cheaply(self):
        self._write("chain.py", _ElifChain)
        result = check_files([self.root / "chain.py"], self.root, "cognitive", 2)
        self.assertEqual(len(result.violations), 1)
        self.assertEqual(result.violations[0].function, "classify")
        self.assertEqual(result.violations[0].metric_name, "cognitive")
        self.assertEqual(result.violations[0].score, 4)
        self.assertEqual(result.violations[0].max_complexity, 2)
        self.assertEqual(result.violations[0].line, 1)
        self.assertEqual(summary(result, "cognitive"), "1/1 methods over their cognitive limit")

    def test_cyclomatic_counts_the_same_chain_higher(self):
        self._write("chain.py", _ElifChain)
        result = check_files([self.root / "chain.py"], self.root, "cyclomatic", 4)
        self.assertEqual(result.violations[0].metric_name, "CC")
        self.assertEqual(result.violations[0].score, 5)

    def test_cyclomatic_is_the_default_metric(self):
        self._write("t.py", "def f(x):\n    return 1 if x else 0\n")
        result = check_files([self.root / "t.py"], self.root)
        self.assertEqual(result.violations, [])
        self.assertEqual(summary(result), "1 methods within their cyclomatic limit")

    def test_violation_reports_file_and_message_parts(self):
        self._write("t.py", "def f(a, b, c):\n    return a and b or c\n")
        result = check_files([self.root / "t.py"], self.root, "cyclomatic", 2)
        self.assertEqual((result.violations[0].file, result.violations[0].line), ("t.py", 1))

    def test_test_files_skipped(self):
        self._write(
            "test_t.py",
            "def f(a):\n    if a:\n        if a:\n            if a:\n                pass\n",
        )
        result = check_files([self.root / "test_t.py"], self.root, "cognitive", 1)
        self.assertEqual((result.checked, result.violations), (0, []))

    def test_broken_file_skipped(self):
        self._write("broken.py", "def f(:\n")
        result = check_files([self.root / "broken.py"], self.root)
        self.assertEqual((result.checked, result.violations), (0, []))

    def test_nested_defs_reported_separately(self):
        src = (
            "def outer(x):\n"
            "    def inner(y):\n"
            "        if y:\n"
            "            if y:\n"
            "                return 1\n"
            "    return x\n"
        )
        self._write("t.py", src)
        result = check_files([self.root / "t.py"], self.root, "cognitive", 2)
        self.assertEqual({v.function for v in result.violations}, {"outer.inner"})


class RunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_no_files_exits_0(self):
        self.assertEqual(run([], self.root), 0)

    def test_violations_exit_2(self):
        (self.root / "t.py").write_text(_ElifChain, encoding="utf-8")
        self.assertEqual(run(["--metric", "cognitive", "--max", "3"], self.root), 2)

    def test_default_limit_passes_clean_tree(self):
        (self.root / "t.py").write_text(_ElifChain, encoding="utf-8")
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
        r = self._run("complexity", "--bogus")
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_bad_metric_exits_1(self):
        r = self._run("complexity", "--metric", "sonar")
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_cognitive_violation_message(self):
        (self.root / "t.py").write_text(_ElifChain, encoding="utf-8")
        r = self._run("complexity", "--metric", "cognitive", "--max", "2")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("cognitive=4 > max 2", r.stdout)
        self.assertIn("over their cognitive limit", r.stdout)

    def test_cyclomatic_default_reports_cc(self):
        (self.root / "t.py").write_text("def f(x):\n    return 1 if x else 0\n", encoding="utf-8")
        r = self._run("complexity", "--max", "1")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("CC=2 > max 1", r.stdout)
        self.assertIn("over their cyclomatic limit", r.stdout)


if __name__ == "__main__":
    unittest.main()
