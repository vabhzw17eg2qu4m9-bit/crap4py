"""Unit + CLI tests for the `folder-structure` subcommand."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from crap4py.folder_structure import check_dirs, default_dirs, run, summary


class DefaultDirsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_package_children_detected(self):
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "__init__.py").write_text("")
        (self.root / "loose").mkdir()  # no __init__.py — not a package
        self.assertEqual(default_dirs(self.root), ["pkg"])

    def test_src_layout_packages_detected(self):
        (self.root / "src" / "app").mkdir(parents=True)
        (self.root / "src" / "app" / "__init__.py").write_text("")
        self.assertEqual(default_dirs(self.root), ["src/app"])


class CheckDirsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _make_pkg(self, name, loose_count):
        pkg = self.root / name
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("")
        for i in range(loose_count):
            (pkg / f"mod{i}.py").write_text("x = 1\n")
        return pkg

    def test_loose_files_flagged_with_default_max_0(self):
        self._make_pkg("pkg", 2)
        result = check_dirs(["pkg"], self.root)
        self.assertEqual(result.checked, 1)
        self.assertEqual(len(result.violations), 1)
        self.assertEqual(
            result.violations[0].message,
            "2 loose .py files directly in pkg — group them into feature packages (max 0)",
        )

    def test_organized_package_passes(self):
        pkg = self._make_pkg("pkg", 0)
        (pkg / "feature").mkdir()
        (pkg / "feature" / "a.py").write_text("x = 1\n")  # nested — the organized form
        self._make_pkg("solo", 1)  # 1 loose file, --max 1
        self.assertEqual(check_dirs(["pkg"], self.root).violations, [])
        self.assertEqual(check_dirs(["solo"], self.root, max_loose_files=1).violations, [])

    def test_dunder_setup_files_not_counted(self):
        pkg = self._make_pkg("pkg", 0)
        (pkg / "__main__.py").write_text("x = 1\n")
        self.assertEqual(check_dirs(["pkg"], self.root).violations, [])

    def test_missing_dir_skipped(self):
        result = check_dirs(["nope"], self.root)
        self.assertEqual((result.checked, result.violations), (0, []))

    def test_summary_lines(self):
        self.assertEqual(
            summary(type("R", (), {"violations": [], "checked": 2})()),
            "2 directories organized into packages",
        )
        self.assertEqual(
            summary(type("R", (), {"violations": [1, 2], "checked": 2})()),
            "2 directory(ies) with loose-file sprawl",
        )


class RunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        pkg = self.root / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "a.py").write_text("x = 1\n")
        self.addCleanup(self._tmp.cleanup)

    def test_run_exits_2_on_sprawl(self):
        self.assertEqual(run([], self.root), 2)

    def test_max_and_dir_flags(self):
        self.assertEqual(run(["--max", "1"], self.root), 0)
        self.assertEqual(run(["--dir", "pkg", "--max", "0"], self.root), 2)


class CliSubprocessTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        pkg = self.root / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "a.py").write_text("x = 1\n")
        self.addCleanup(self._tmp.cleanup)

    def test_cli_reports_and_exits_2(self):
        r = subprocess.run(
            [sys.executable, "-m", "crap4py", "folder-structure"],
            cwd=self.root,
            capture_output=True,
            text=True,
            env={"PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn(
            "pkg: 1 loose .py files directly in pkg — group them into feature packages (max 0)",
            r.stdout,
        )

    def test_cli_bad_flag_exits_1(self):
        r = subprocess.run(
            [sys.executable, "-m", "crap4py", "folder-structure", "--bogus"],
            cwd=self.root,
            capture_output=True,
            text=True,
            env={"PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
        )
        self.assertEqual(r.returncode, 1, r.stderr)


if __name__ == "__main__":
    unittest.main()
