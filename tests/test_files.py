"""In-process tests for files.py (imported in-process by the subcommand modules)."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from crap4py.files import (
    _is_excluded,
    _parse_status_line,
    changed_files,
    expand_paths,
    find_source_files,
    find_test_files,
)


class _TempRoot(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _write(self, rel, text="x = 1\n"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p


class FindSourceFilesTest(_TempRoot):
    def test_walks_root_and_prefers_src(self):
        self._write("mod.py")
        self._write("src/other.py")
        self.assertEqual([p.name for p in find_source_files(self.root)], ["other.py"])

    def test_walks_root_when_no_src(self):
        self._write("a.py")
        self._write("b.py")
        self.assertEqual(len(find_source_files(self.root)), 2)


class FindTestFilesTest(_TempRoot):
    def test_collects_test_files_only(self):
        t1 = self._write("tests/test_mod.py")
        t2 = self._write("test_root.py")
        helper = self._write("tests/helper.py")  # under a test dir → test file
        self._write("mod.py")
        self.assertEqual(find_test_files(self.root), sorted([t1, t2, helper]))

    def test_skips_excluded_dirs(self):
        self._write(".venv/pkg/tests/test_x.py")
        self.assertEqual(find_test_files(self.root), [])


class ExpandPathsTest(_TempRoot):
    def test_files_and_dirs_deduped_sorted(self):
        self._write("pkg/one.py")
        self._write("pkg/two.py")
        result = expand_paths(["pkg", str(self.root / "pkg" / "one.py")], self.root)
        self.assertEqual([p.name for p in result], ["one.py", "two.py"])

    def test_missing_path_ignored(self):
        self.assertEqual(expand_paths(["nope.py"], self.root), [])


class IsExcludedTest(_TempRoot):
    def test_excluded_dirs_and_test_names(self):
        self.assertTrue(_is_excluded(self._write("venv/x.py"), self.root))
        self.assertTrue(_is_excluded(self._write("__pycache__/x.py"), self.root))
        self.assertTrue(_is_excluded(self._write("conftest.py"), self.root))
        self.assertTrue(_is_excluded(self._write("test_x.py"), self.root))
        self.assertTrue(_is_excluded(self._write("x_test.py"), self.root))

    def test_regular_file_not_excluded(self):
        self.assertFalse(_is_excluded(self._write("mod.py"), self.root))

    def test_path_outside_root_uses_own_parts(self):
        self.assertTrue(_is_excluded(Path("/somewhere/venv/mod.py"), self.root))


class ParseStatusLineTest(unittest.TestCase):
    def test_plain_path(self):
        self.assertEqual(_parse_status_line(" M  mod.py"), "mod.py")

    def test_short_line_returns_none(self):
        self.assertIsNone(_parse_status_line("M"))

    def test_rename_takes_destination(self):
        self.assertEqual(_parse_status_line("R  old_name.py -> new_name.py"), "new_name.py")

    def test_quoted_path_unquoted(self):
        self.assertEqual(_parse_status_line('?? "weird name.py"'), "weird name.py")


class ChangedFilesTest(_TempRoot):
    def _git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
            check=True,
        )

    def _init_repo(self):
        self._git("init", "-q")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "t")

    def test_changed_and_untracked_py_files(self):
        self._init_repo()
        committed = self._write("committed.py")
        self._git("add", "committed.py")
        self._git("commit", "-qm", "init")
        committed.write_text("x = 2\n", encoding="utf-8")
        self._write("untracked.py")
        self._write("notes.txt")
        self.assertEqual(
            [p.name for p in changed_files(self.root)],
            ["committed.py", "untracked.py"],
        )

    def test_rename_and_deleted_paths(self):
        self._init_repo()
        self._write("old_name.py")
        self._git("add", "old_name.py")
        self._git("commit", "-qm", "init")
        self._write("gone.py")
        self._git("add", "gone.py")
        self._git("commit", "-qm", "add gone")
        (self.root / "gone.py").unlink()
        self._git("mv", "old_name.py", "new_name.py")
        names = [p.name for p in changed_files(self.root)]
        self.assertIn("new_name.py", names)
        self.assertNotIn("gone.py", names)  # in status but no longer on disk

    def test_not_a_repo_raises(self):
        with self.assertRaises(RuntimeError):
            changed_files(self.root)


if __name__ == "__main__":
    unittest.main()
