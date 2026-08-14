"""End-to-end CLI tests via subprocess, against a temp fixture directory."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import crap4py

FIXTURES = Path(__file__).parent / "fixtures"


class CliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        shutil.copy(FIXTURES / "sample.py", self.root / "sample.py")
        shutil.copy(FIXTURES / "coverage.json", self.root / "coverage.json")

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "crap4py", *args],
            cwd=str(self.root),
            capture_output=True,
            text=True,
        )

    def test_version_exits_0(self):
        r = self._run("--version")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), f"crap4py {crap4py.__version__}")

    def test_help_exits_0(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Usage", r.stdout)

    def test_default_run_exits_2_when_threshold_exceeded(self):
        r = self._run()
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("CRAP Report", r.stdout)
        self.assertIn("risky", r.stdout)
        self.assertIn("CRAP threshold exceeded", r.stderr)
        self.assertIn("30.0", r.stderr)

    def test_high_threshold_exits_0(self):
        r = self._run("--threshold", "100")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("CRAP Report", r.stdout)
        self.assertIn("passed", r.stdout)

    def test_no_coverage_file_yields_na_exits_0(self):
        (self.root / "coverage.json").unlink()
        r = self._run("--threshold", "100")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("N/A", r.stdout)

    def test_changed_with_paths_exits_1(self):
        r = self._run("--changed", "sample.py")
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_explicit_path_run(self):
        r = self._run("sample.py", "--threshold", "100")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("simple", r.stdout)
        self.assertIn("branchy", r.stdout)

    def test_no_python_files_message_exits_0(self):
        (self.root / "sample.py").unlink()
        r = self._run("--threshold", "100")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("No Python files to analyze", r.stdout)

    def test_unknown_flag_exits_1(self):
        r = self._run("--bogus-flag")
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_profile_bad_flag_exits_1(self):
        r = self._run("profile", "--bogus")
        self.assertEqual(r.returncode, 1, r.stderr)


if __name__ == "__main__":
    unittest.main()
