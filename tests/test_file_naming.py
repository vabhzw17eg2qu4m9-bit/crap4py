"""Unit + CLI tests for the `file-naming` subcommand."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from crap4py.file_naming import (
    check_files,
    run,
    summary,
    violation_for,
)


class ViolationForTest(unittest.TestCase):
    def test_generic_stem(self):
        msg = violation_for("utils.py")
        self.assertIsNotNone(msg)
        self.assertIn('generic name "utils.py"', msg)
        self.assertIn("split by domain", msg)

    def test_generic_stem_matched_case_insensitively(self):
        msg = violation_for("Helpers.py")
        self.assertIsNotNone(msg)
        self.assertIn('generic name "Helpers.py"', msg)

    def test_numeric_suffix(self):
        msg = violation_for("jira_batch1.py")
        self.assertIsNotNone(msg)
        self.assertIn('numeric suffix in "jira_batch1.py"', msg)
        self.assertIn("batch1, part2, v2", msg)

    def test_allowed_stem_passes(self):
        self.assertIsNone(violation_for("base64.py"))
        self.assertIsNone(violation_for("sha256.py"))
        self.assertIsNone(violation_for("Utf8.py"))

    def test_clean_name_passes(self):
        self.assertIsNone(violation_for("analyzer.py"))
        self.assertIsNone(violation_for("crap4py/profile.py"))

    def test_digit_only_prefix_not_flagged(self):
        self.assertIsNone(violation_for("md5.py"))


class CheckFilesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _write(self, rel):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x = 1\n", encoding="utf-8")
        return p

    def test_violations_and_counts(self):
        files = [
            self._write("util.py"),
            self._write("base64.py"),
            self._write("report2.py"),
            self._write("good_name.py"),
            self._write("tests/helper.py"),  # under a tests/ dir — skipped
            self._write("test_tmp_mod.py"),  # test file — skipped
        ]
        result = check_files(files, self.root)
        self.assertEqual(result.checked, 4)
        self.assertEqual([v.file for v in result.violations], ["util.py", "report2.py"])
        self.assertEqual(summary(result), "2/4 files with mechanical names")

    def test_all_clean_summary(self):
        result = check_files([self._write("analyzer.py")], self.root)
        self.assertEqual(result.violations, [])
        self.assertEqual(summary(result), "1 files have domain-meaningful names")

    def test_path_outside_root_not_test_dir(self):
        outside = Path(self.root / ".." / "tmp2.py").resolve()
        outside.parent.mkdir(exist_ok=True)
        outside.write_text("x = 1\n", encoding="utf-8")
        self.addCleanup(outside.unlink)
        result = check_files([outside], self.root)
        self.assertEqual(result.checked, 1)
        self.assertEqual(result.violations[0].file, str(outside))


class RunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_no_files_exits_0(self):
        self.assertEqual(run([], self.root), 0)

    def test_violations_exit_2(self):
        (self.root / "utils.py").write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(run([], self.root), 2)

    def test_explicit_path_arg(self):
        (self.root / "core.py").write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(run(["core.py"], self.root), 2)


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

    def test_clean_project_exits_0(self):
        (self.root / "analyzer.py").write_text("x = 1\n", encoding="utf-8")
        r = self._run("file-naming")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 files have domain-meaningful names", r.stdout)

    def test_mechanical_names_exit_2(self):
        (self.root / "util.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / "batch1.py").write_text("x = 1\n", encoding="utf-8")
        r = self._run("file-naming")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn('util.py: generic name "util.py"', r.stdout)
        self.assertIn("batch1.py: numeric suffix", r.stdout)
        self.assertIn("2/2 files with mechanical names", r.stdout)

    def test_bad_flag_exits_1(self):
        r = self._run("file-naming", "--bogus")
        self.assertEqual(r.returncode, 1, r.stderr)


class ParserErrorTest(unittest.TestCase):
    def test_usage_error_raises_system_exit_1(self):
        from crap4py.file_naming import _build_parser

        with self.assertRaises(SystemExit) as ctx:
            _build_parser().parse_args(["--bogus"])
        self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
