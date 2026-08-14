"""Unit + CLI tests for the `banned-imports` subcommand."""

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from crap4py.banned_imports import ImportRule, check_files, run, summary


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

    def test_raw_name_match_is_a_violation(self):
        self._write("app.py", "import os.path\n")
        rules = [ImportRule("*.py", "os.*", "no os here")]
        result = check_files([self.root / "app.py"], self.root, rules)
        self.assertEqual(len(result.violations), 1)
        v = result.violations[0]
        self.assertEqual((v.file, v.target), ("app.py", "os.path"))
        self.assertEqual(v.rule.message, "no os here")

    def test_resolved_project_path_match(self):
        self._write("pkg/a.py", "value = 1\n")
        self._write("pkg/b.py", "from . import a\n")
        rules = [ImportRule("pkg/*.py", "pkg/a.py", None)]
        result = check_files([self.root / "pkg" / "b.py"], self.root, rules)
        self.assertEqual([v.target for v in result.violations], ["pkg/a.py"])

    def test_file_not_matching_from_glob_ignored(self):
        self._write("app.py", "import os\n")
        self._write("other.py", "import os\n")
        rules = [ImportRule("other.py", "os", "no os")]
        result = check_files([self.root / "app.py", self.root / "other.py"], self.root, rules)
        self.assertEqual([v.file for v in result.violations], ["other.py"])

    def test_first_matching_rule_reported(self):
        self._write("app.py", "import os\n")
        rules = [ImportRule("*.py", "os", "first"), ImportRule("*.py", "os", "second")]
        result = check_files([self.root / "app.py"], self.root, rules)
        self.assertEqual([v.rule.message for v in result.violations], ["first"])

    def test_importfrom_module_matched_by_raw_name(self):
        self._write("app.py", "from collections import OrderedDict\n")
        rules = [ImportRule("*.py", "collections", None)]
        result = check_files([self.root / "app.py"], self.root, rules)
        self.assertEqual(len(result.violations), 1)

    def test_test_files_skipped(self):
        self._write("test_app.py", "import os\n")
        rules = [ImportRule("*.py", "os", None)]
        result = check_files([self.root / "test_app.py"], self.root, rules)
        self.assertEqual((result.checked, result.violations), (0, []))

    def test_summary_lines(self):
        rules = [ImportRule("*.py", "os", None)]
        self._write("app.py", "import os\n")
        bad = check_files([self.root / "app.py"], self.root, rules)
        self.assertEqual(summary(bad), "1 banned imports in 1 files")
        self._write("clean.py", "x = 1\n")
        clean = check_files([self.root / "clean.py"], self.root, rules)
        self.assertEqual(summary(clean), "no banned imports in 1 files")


class RunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _write_app(self):
        (self.root / "app.py").write_text("import os\n", encoding="utf-8")

    def test_no_rules_passes_and_says_so(self):
        self._write_app()
        self.assertEqual(run([], self.root), 0)

    def test_rules_but_no_files_exits_0(self):
        self.assertEqual(run(["--from", "*.py", "--forbid", "os"], self.root), 0)

    def test_violation_without_message(self):
        self._write_app()
        rc = run(["--from", "*.py", "--forbid", "os"], self.root)
        self.assertEqual(rc, 2)

    def test_violations_exit_2(self):
        self._write_app()
        rc = run(["--from", "*.py", "--forbid", "os"], self.root)
        self.assertEqual(rc, 2)

    def test_no_violations_exit_0(self):
        self._write_app()
        self.assertEqual(run(["--from", "*.py", "--forbid", "sys"], self.root), 0)

    def test_unpaired_from_forbid_is_usage_error(self):
        with self.assertRaises(SystemExit) as ctx:
            run(["--from", "*.py"], self.root)
        self.assertEqual(ctx.exception.code, 1)

    def test_rules_zipped_in_cli_order(self):
        (self.root / "app.py").write_text("import os\nimport sys\n", encoding="utf-8")
        rc = run(
            [
                "--from",
                "*.py",
                "--forbid",
                "sys",
                "--from",
                "app.py",
                "--forbid",
                "os",
                "--message",
                "rule two",
            ],
            self.root,
        )
        self.assertEqual(rc, 2)


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

    def test_no_rules_prints_pass_message(self):
        r = self._run("banned-imports")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("No banned-import rules given", r.stdout)

    def test_violation_with_message(self):
        (self.root / "app.py").write_text("import os\n", encoding="utf-8")
        r = self._run(
            "banned-imports",
            "--from",
            "*.py",
            "--forbid",
            "os",
            "--message",
            "use services instead",
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("app.py:1: import 'os' is banned", r.stdout)
        self.assertIn("use services instead", r.stdout)

    def test_unpaired_flags_exit_1(self):
        r = self._run("banned-imports", "--forbid", "os")
        self.assertEqual(r.returncode, 1, r.stderr)


if __name__ == "__main__":
    unittest.main()
