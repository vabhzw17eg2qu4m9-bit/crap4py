"""Unit + CLI tests for the `loc` subcommand."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from crap4py.loc import MAX_LINES, check_files, run, summary


def _file_of(lines: int) -> str:
    return "\n".join(["x = 1"] * lines) + "\n"


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

    def test_over_limit_flagged(self):
        self._write("big.py", _file_of(9))
        result = check_files([self.root / "big.py"], self.root, max_lines=8)
        self.assertEqual(len(result.violations), 1)
        self.assertEqual(result.violations[0].file, "big.py")
        self.assertEqual(result.violations[0].lines, 9)
        self.assertEqual(result.violations[0].max_lines, 8)

    def test_exactly_at_limit_passes(self):
        self._write("ok.py", _file_of(8))
        result = check_files([self.root / "ok.py"], self.root, max_lines=8)
        self.assertEqual(result.violations, [])
        self.assertEqual(result.checked, 1)
        self.assertEqual(summary(result, 8), "1 files within 8 lines")

    def test_default_max_lines_is_800(self):
        self.assertEqual(MAX_LINES, 800)

    def test_exclude_drops_file_from_check(self):
        self._write("big.py", _file_of(9))
        self._write("keep.py", _file_of(2))
        result = check_files(
            [self.root / "big.py", self.root / "keep.py"], self.root, 4, ["big.py"]
        )
        self.assertEqual(result.violations, [])
        self.assertEqual(result.checked, 1)  # keep.py only — big.py never counted

    def test_test_files_skipped(self):
        self._write("tests/big.py", _file_of(9))
        result = check_files([self.root / "tests" / "big.py"], self.root, 4)
        self.assertEqual(result.checked, 0)

    def test_summary_counts_violations(self):
        self._write("a.py", _file_of(9))
        self._write("b.py", _file_of(9))
        result = check_files([self.root / "a.py", self.root / "b.py"], self.root, 8)
        self.assertEqual(summary(result, 8), "2/2 files over their line limit")


class RunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_no_files_exits_0(self):
        self.assertEqual(run([], self.root), 0)

    def test_violations_exit_2(self):
        (self.root / "big.py").write_text(_file_of(9), encoding="utf-8")
        self.assertEqual(run(["--max-lines", "8"], self.root), 2)

    def test_boundary_exits_0(self):
        (self.root / "big.py").write_text(_file_of(5), encoding="utf-8")
        self.assertEqual(run(["--max-lines", "5"], self.root), 0)

    def test_exclude_exits_0(self):
        (self.root / "big.py").write_text(_file_of(9), encoding="utf-8")
        self.assertEqual(run(["--max-lines", "4", "--exclude", "big.py"], self.root), 0)

    def test_file_marker_suppresses(self):
        (self.root / "big.py").write_text("# ignore-file:loc\n" + _file_of(9), encoding="utf-8")
        self.assertEqual(run(["--max-lines", "4"], self.root), 0)


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
        r = self._run("loc", "--bogus")
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_violation_exits_2_with_message(self):
        (self.root / "big.py").write_text(_file_of(5), encoding="utf-8")
        r = self._run("loc", "--max-lines", "4")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("big.py: 5 lines > max 4", r.stdout)


if __name__ == "__main__":
    unittest.main()
