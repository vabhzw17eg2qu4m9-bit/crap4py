"""Unit + CLI tests for the `folder-structure` subcommand."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from crap4py.folder_structure import (
    StructureResult,
    check_dirs,
    default_dirs,
    run,
    summary,
)


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


class DirectOnlyTest(unittest.TestCase):
    """A directory is judged on its own contents: nested directories are
    the organized form and never reach the parent's budget."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_deep_subtree_does_not_inflate_parent(self):
        pkg = self.root / "pkg"
        pkg.mkdir()
        for i in range(3):
            (pkg / f"m{i}.py").write_text("x = 1\n")
        deep = pkg / "deep" / "deeper"
        deep.mkdir(parents=True)
        for i in range(10):
            (deep / f"g{i}.py").write_text("x = 1\n")
        # 3 direct units against cap 3 — clean. Subtree counting would
        # see 13 units and flag.
        self.assertEqual(check_dirs(["pkg"], self.root, max_loose_files=3).violations, [])


class StemCollapseTest(unittest.TestCase):
    """Generated siblings of one source file count as one logical unit."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_generated_siblings_collapse_into_one_unit(self):
        pkg = self.root / "pkg"
        pkg.mkdir()
        (pkg / "service.py").write_text("x = 1\n")
        (pkg / "service_pb2.py").write_text("# generated\n")
        (pkg / "service_pb2_grpc.py").write_text("# generated\n")
        (pkg / "other.py").write_text("x = 1\n")
        # 2 units (service, other), not 4 files.
        self.assertEqual(check_dirs(["pkg"], self.root, max_loose_files=2).violations, [])

    def test_pyi_stubs_are_package_metadata_not_loose_files(self):
        pkg = self.root / "pkg"
        pkg.mkdir()
        (pkg / "foo.pyi").write_text("...\n")
        self.assertEqual(check_dirs(["pkg"], self.root).violations, [])
        (pkg / "foo.py").write_text("x = 1\n")
        self.assertEqual(check_dirs(["pkg"], self.root, max_loose_files=1).violations, [])


class GlobDirsTest(unittest.TestCase):
    """``--dir`` entries accept glob patterns expanding to every matching
    directory; hidden and build dirs are never enumerated."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _pkg_dir(self, name, count=2):
        d = self.root / "pkg" / name
        d.mkdir(parents=True)
        for i in range(count):
            (d / f"m{i}.py").write_text("x = 1\n")
        return d

    def test_glob_expands_to_matching_directories(self):
        self._pkg_dir("one")
        self._pkg_dir("two")
        result = check_dirs(["pkg/*"], self.root, max_loose_files=1)
        self.assertEqual(sorted(v.directory for v in result.violations), ["pkg/one", "pkg/two"])

    def test_single_star_does_not_cross_separators(self):
        self._pkg_dir("one/inner", count=1)
        # pkg/* matches only pkg/one; the nested inner dir is not checked
        # and pkg/one holds no direct file.
        self.assertEqual(check_dirs(["pkg/*"], self.root).violations, [])

    def test_doublestar_reaches_nested_directories(self):
        self._pkg_dir("one/inner", count=1)
        result = check_dirs(["pkg/**"], self.root)
        self.assertEqual([v.directory for v in result.violations], ["pkg/one/inner"])

    def test_hidden_and_build_dirs_never_enumerated(self):
        for name in (".venv", "__pycache__", "build", "pkg.egg-info"):
            self._pkg_dir(name, count=1)
        self._pkg_dir("real", count=1)
        result = check_dirs(["pkg/*"], self.root)
        self.assertEqual([v.directory for v in result.violations], ["pkg/real"])

    def test_missing_glob_expands_to_nothing(self):
        self.assertEqual(check_dirs(["nope/*"], self.root), StructureResult([], 0))


class CapsTest(unittest.TestCase):
    """The caps form gives each pattern its own cap, first pattern wins,
    and takes precedence over the classic form."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _pkg_dir(self, name, count):
        d = self.root / "pkg" / name
        d.mkdir(parents=True)
        for i in range(count):
            (d / f"m{i}.py").write_text("x = 1\n")

    def test_per_pattern_caps(self):
        self._pkg_dir("core", 2)
        self._pkg_dir("gen", 3)
        result = check_dirs([], self.root, caps={"pkg/core": 1, "pkg/gen": 3})
        self.assertEqual(len(result.violations), 1)
        self.assertEqual(result.violations[0].directory, "pkg/core")
        self.assertIn("(max 1)", result.violations[0].message)

    def test_caps_take_precedence_over_dirs_and_max(self):
        self._pkg_dir("pkg2", 3)
        result = check_dirs(["pkg/pkg2"], self.root, max_loose_files=99, caps={"pkg/pkg2": 2})
        self.assertEqual(len(result.violations), 1)
        self.assertIn("(max 2)", result.violations[0].message)

    def test_first_pattern_wins_on_overlap(self):
        self._pkg_dir("tools", 3)
        # tools matches both patterns; the first pattern's cap 10 wins.
        result = check_dirs([], self.root, caps={"pkg/**": 10, "pkg/*": 1})
        self.assertEqual(result.violations, [])


class ExcludeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_exclude_removes_files_from_the_count(self):
        pkg = self.root / "pkg"
        pkg.mkdir()
        for i in range(3):
            (pkg / f"m{i}.py").write_text("x = 1\n")
        (pkg / "huge_pb2.py").write_text("# generated\n")
        self.assertEqual(len(check_dirs(["pkg"], self.root, max_loose_files=3).violations), 1)
        excluded = check_dirs(["pkg"], self.root, max_loose_files=3, exclude=["*_pb2.py"])
        self.assertEqual(excluded.violations, [])


class CapFlagTest(unittest.TestCase):
    """``--cap PATTERN=CAP`` pairs; malformed values are usage errors."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        pkg = self.root / "pkg"
        pkg.mkdir()
        for i in range(3):
            (pkg / f"m{i}.py").write_text("x = 1\n")
        self.addCleanup(self._tmp.cleanup)

    def test_cap_flag_flags_and_exits_2(self):
        self.assertEqual(run(["--cap", "pkg=2"], self.root), 2)

    def test_cap_flag_passes_under_cap(self):
        self.assertEqual(run(["--cap", "pkg=3"], self.root), 0)

    def test_cap_flag_takes_precedence_over_dir_and_max(self):
        self.assertEqual(run(["--dir", "pkg", "--max", "99", "--cap", "pkg=2"], self.root), 2)

    def test_max_files_stays_an_unknown_flag(self):
        # Upstream pins `max_files` as rejected by the loader; the CLI
        # analog is an unknown-flag usage error.
        with self.assertRaises(SystemExit) as cm:
            run(["--max-files", "3"], self.root)
        self.assertEqual(cm.exception.code, 1)

    def test_exclude_flag(self):
        argv = ["--dir", "pkg", "--max", "3", "--exclude", "*_pb2.py"]
        self.assertEqual(run(argv, self.root), 0)

    def test_malformed_cap_is_usage_error(self):
        with self.assertRaises(SystemExit) as cm:
            run(["--cap", "pkg"], self.root)
        self.assertEqual(cm.exception.code, 1)

    def test_non_integer_cap_is_usage_error(self):
        with self.assertRaises(SystemExit) as cm:
            run(["--cap", "pkg=many"], self.root)
        self.assertEqual(cm.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
