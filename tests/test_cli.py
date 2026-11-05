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

    def test_no_flags_report_is_byte_stable(self):
        # Golden from the pre-lcov release (v0.29.0): new flags must not
        # perturb the default run's output.
        r = self._run("--threshold", "100")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            r.stdout,
            "CRAP Report\n"
            "===========\n"
            "Method                         File                                  CC    Cov%     CRAP\n"  # noqa: E501
            "----------------------------------------------------------------------------------------\n"  # noqa: E501
            "risky                          sample.py                              5    0.0%     30.0\n"  # noqa: E501
            "branchy                        sample.py                              3   66.7%      3.3\n"  # noqa: E501
            "simple                         sample.py                              1  100.0%      1.0\n"  # noqa: E501
            "\n"
            "Max CRAP: 30.0 (threshold 100.0) — passed\n",
        )


class LcovCliTest(unittest.TestCase):
    """``*.info`` coverage input: lcov and native JSON must agree byte for byte."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        shutil.copy(FIXTURES / "sample.py", self.root / "sample.py")
        shutil.copy(FIXTURES / "coverage.json", self.root / "coverage.json")
        shutil.copy(FIXTURES / "coverage.info", self.root / "coverage.info")

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "crap4py", *args],
            cwd=str(self.root),
            capture_output=True,
            text=True,
        )

    def test_lcov_report_matches_native_json_report(self):
        json_run = self._run("--coverage", "coverage.json", "--threshold", "100")
        info_run = self._run("--coverage", "coverage.info", "--threshold", "100")
        self.assertEqual(json_run.returncode, 0, json_run.stderr)
        self.assertEqual(info_run.returncode, 0, info_run.stderr)
        # The lcov fixture mirrors coverage.json line for line and carries
        # FN/FNDA/LF/LH/BRDA records the reader must ignore.
        self.assertEqual(json_run.stdout, info_run.stdout)
        self.assertIn("risky", info_run.stdout)
        self.assertIn("0.0%", info_run.stdout)

    def test_lcov_crlf_report_matches_native_json_report(self):
        info = self.root / "coverage.info"
        info.write_bytes(info.read_bytes().replace(b"\n", b"\r\n"))
        json_run = self._run("--coverage", "coverage.json", "--threshold", "100")
        crlf_run = self._run("--coverage", "coverage.info", "--threshold", "100")
        self.assertEqual(json_run.returncode, 0, json_run.stderr)
        self.assertEqual(crlf_run.returncode, 0, crlf_run.stderr)
        self.assertEqual(json_run.stdout, crlf_run.stdout)


class StrictCliTest(unittest.TestCase):
    """``--strict`` turns missing/empty/unparseable coverage into exit 1."""

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

    def test_strict_missing_coverage_exits_1(self):
        (self.root / "coverage.json").unlink()
        r = self._run("--strict", "--threshold", "100")
        self.assertEqual(r.returncode, 1)
        self.assertIn("coverage", r.stderr)

    def test_strict_unparseable_json_exits_1(self):
        (self.root / "coverage.json").write_text("")
        r = self._run("--strict", "--threshold", "100")
        self.assertEqual(r.returncode, 1)

    def test_strict_zero_record_lcov_exits_1(self):
        (self.root / "coverage.json").write_text("TN:\nend_of_record\n")
        (self.root / "coverage.json").rename(self.root / "coverage.info")
        r = self._run("--strict", "--coverage", "coverage.info", "--threshold", "100")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no coverage records", r.stderr)

    def test_strict_missing_explicit_path_exits_1(self):
        r = self._run("--strict", "ghost.py", "--threshold", "100")
        self.assertEqual(r.returncode, 1)
        self.assertIn("ghost.py", r.stderr)

    def test_strict_parse_failure_exits_1(self):
        (self.root / "coverage.json").write_text("SF:a.py\nDA:x,y\nend_of_record\n")
        (self.root / "coverage.json").rename(self.root / "coverage.info")
        r = self._run("--strict", "--coverage", "coverage.info", "--threshold", "100")
        self.assertEqual(r.returncode, 1)
        self.assertIn("cannot parse", r.stderr)

    def test_strict_file_without_coverage_entry_shows_zero_rows(self):
        # Coverage exists but names another file: sample.py methods are 0.0%,
        # not N/A.
        (self.root / "coverage.json").write_text(
            '{"files": {"other.py": {"executed_lines": [1], "missing_lines": []}}}'
        )
        r = self._run("--strict", "--threshold", "100")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("N/A", r.stdout)
        self.assertIn("0.0%", r.stdout)
        self.assertIn("risky", r.stdout)

    def test_strict_type_only_function_stays_na(self):
        # The file has an entry, but branchy/risky's lines carry no DA
        # records: their coverage stays N/A.
        (self.root / "coverage.json").write_text(
            '{"files": {"sample.py": {"executed_lines": [4, 5], "missing_lines": []}}}'
        )
        r = self._run("--strict", "--threshold", "100")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("simple", r.stdout)
        self.assertIn("100.0%", r.stdout)
        self.assertIn("N/A", r.stdout)


class ExcludeCliTest(unittest.TestCase):
    """``--exclude`` drops files whose project-relative path matches a glob."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        shutil.copy(FIXTURES / "sample.py", self.root / "sample.py")
        shutil.copy(FIXTURES / "coverage.json", self.root / "coverage.json")
        shutil.copy(FIXTURES / "sample.py", self.root / "other.py")

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "crap4py", *args],
            cwd=str(self.root),
            capture_output=True,
            text=True,
        )

    def test_exclude_drops_matching_file(self):
        r = self._run("--exclude", "other.py", "--threshold", "100")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("other.py", r.stdout)
        self.assertIn("sample.py", r.stdout)

    def test_exclude_is_repeatable(self):
        r = self._run("--exclude", "other.py", "--exclude", "sample.py", "--threshold", "100")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("No Python files to analyze", r.stdout)

    def test_exclude_glob_matches_subdir_path(self):
        sub = self.root / "pkg"
        sub.mkdir()
        shutil.copy(FIXTURES / "sample.py", sub / "inner.py")
        r = self._run("--exclude", "pkg/*", "--threshold", "100")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("inner.py", r.stdout)


class DiffBaseCliTest(unittest.TestCase):
    """``--diff-base`` selects methods intersecting the diff (temp git repo)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        shutil.copy(FIXTURES / "sample.py", self.root / "sample.py")
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "t")
        self._git("add", "-A")
        self._git("commit", "-qm", "init")

    def tearDown(self):
        self._tmp.cleanup()

    def _git(self, *args):
        subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True)

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "crap4py", *args],
            cwd=str(self.root),
            capture_output=True,
            text=True,
        )

    def test_diff_base_keeps_only_changed_function(self):
        text = (self.root / "sample.py").read_text()
        (self.root / "sample.py").write_text(
            text.replace("    total = 0\n", "    total = 0  # touched\n")
        )
        r = self._run("--diff-base", "HEAD", "--threshold", "100")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("branchy", r.stdout)
        self.assertNotIn("simple", r.stdout)
        self.assertNotIn("risky", r.stdout)

    def test_diff_base_drops_deleted_only_file(self):
        # Every hunk in the diff is a deletion: the file has no changed
        # source lines, so it is dropped from the run entirely.
        (self.root / "extra.py").write_text("def gone(a):\n    return a\n")
        self._git("add", "-A")
        self._git("commit", "-qm", "extra")
        text = (self.root / "sample.py").read_text()
        (self.root / "sample.py").write_text(text.split("def risky")[0])
        r = self._run("--diff-base", "HEAD", "--threshold", "100")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "No Python files to analyze.\n")

    def test_diff_base_rename_uses_new_path(self):
        self._git("mv", "sample.py", "renamed.py")
        text = (self.root / "renamed.py").read_text()
        (self.root / "renamed.py").write_text(
            text.replace("    return a + b\n", "    return a + b  # touched\n")
        )
        r = self._run("--diff-base", "HEAD", "--threshold", "100")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("simple", r.stdout)
        self.assertNotIn("branchy", r.stdout)
        self.assertNotIn("risky", r.stdout)

    def test_diff_base_unknown_ref_exits_1(self):
        r = self._run("--diff-base", "no-such-ref", "--threshold", "100")
        self.assertEqual(r.returncode, 1)
        self.assertIn("git diff failed", r.stderr)

    def test_diff_base_with_changed_exits_1(self):
        r = self._run("--diff-base", "HEAD", "--changed", "--threshold", "100")
        self.assertEqual(r.returncode, 1)

    def test_diff_base_explicit_paths_scope_the_selection(self):
        shutil.copy(FIXTURES / "sample.py", self.root / "other.py")
        self._git("add", "-A")
        self._git("commit", "-qm", "other")
        for name in ("sample.py", "other.py"):
            text = (self.root / name).read_text()
            (self.root / name).write_text(
                text.replace("    return a + b\n", "    return a + b  # touched\n")
            )
        r = self._run("--diff-base", "HEAD", "other.py", "--threshold", "100")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("other.py", r.stdout)
        self.assertNotIn("sample.py", r.stdout)

    def test_diff_base_clean_tree_exits_0_with_no_files_message(self):
        r = self._run("--diff-base", "HEAD", "--threshold", "100")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "No Python files to analyze.\n")


if __name__ == "__main__":
    unittest.main()
