"""Unit + CLI tests for the `unused-files` subcommand."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from crap4py.files import find_source_files
from crap4py.imports import imported_paths, package_root, resolve_dotted
from crap4py.unused_files import check_files, run, summary


def _parse(path: Path):
    from crap4py.files import parse_file

    tree = parse_file(path)
    assert tree is not None
    return tree


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)

    def _write(self, rel, source="x = 1\n"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(source, encoding="utf-8")
        return p

    def test_resolve_dotted_hits_module_and_package(self):
        mod = self._write("pkg/a.py")
        pkg = self._write("pkg/__init__.py")
        hits = resolve_dotted("pkg.a", self.root)
        self.assertIn(mod.resolve(), hits)
        self.assertEqual(resolve_dotted("pkg", self.root), {pkg.resolve()})

    def test_stdlib_does_not_resolve(self):
        self.assertEqual(resolve_dotted("os.path", self.root), set())

    def test_relative_import_resolves(self):
        a = self._write("pkg/a.py")
        b = self._write("pkg/b.py", "from . import a\n")
        self.assertEqual(imported_paths(_parse(b), b.resolve(), self.root), {a.resolve()})

    def test_dotted_relative_import_resolves(self):
        a = self._write("pkg/a.py")
        b = self._write("pkg/b.py", "from .a import x\n")
        self.assertEqual(imported_paths(_parse(b), b.resolve(), self.root), {a.resolve()})

    def test_absolute_import_resolves_against_package_root(self):
        a = self._write("pkg/a.py")
        b = self._write("pkg/b.py", "import pkg.a\n")
        self.assertEqual(
            imported_paths(_parse(b), b.resolve(), package_root(self.root)), {a.resolve()}
        )

    def test_parent_relative_import_resolves(self):
        a = self._write("pkg/a.py")
        b = self._write("pkg/sub/b.py", "from .. import a\n")
        self.assertEqual(imported_paths(_parse(b), b.resolve(), self.root), {a.resolve()})

    def test_init_reexport_counts_as_import(self):
        """0.7.1: a package's __init__ re-exports reach implementation files."""
        impl = self._write("pkg/impl.py", "thing = 1\n")
        init = self._write("pkg/__init__.py", "from .impl import thing\n")
        self.assertEqual(imported_paths(_parse(init), init.resolve(), self.root), {impl.resolve()})


class CheckFilesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _write(self, rel, source="x = 1\n"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(source, encoding="utf-8")
        return p

    def test_orphan_flagged_imported_exempt(self):
        self._write("pkg/__init__.py")
        self._write("pkg/__main__.py", "from pkg import a, b\n")
        self._write("pkg/a.py", "from . import b\n")
        self._write("pkg/b.py", "value = 1\n")
        orphan = self._write("pkg/orphan.py", "value = 2\n")
        result = check_files(find_source_files(self.root), self.root)
        self.assertEqual(result.violations, [str(orphan.relative_to(self.root))])
        self.assertEqual(summary(result), "1/5 files never imported")

    def test_stdlib_only_file_is_still_an_orphan(self):
        self._write("app.py", "import os\nprint(os)\n")
        self._write("helper.py", "value = 1\n")
        self._write("__main__.py", "import helper\n")
        result = check_files(find_source_files(self.root), self.root)
        self.assertEqual(result.violations, ["app.py"])

    def test_test_file_imports_do_not_count(self):
        self._write("tests/test_uses.py", "import helper\n")
        self._write("helper.py", "value = 1\n")
        result = check_files(find_source_files(self.root), self.root)
        self.assertEqual(result.violations, ["helper.py"])


class RunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_partial_selection_skips_with_exit_0(self):
        (self.root / "orphan.py").write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(run(["orphan.py"], self.root), 0)

    def test_orphan_exits_2(self):
        (self.root / "orphan.py").write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(run([], self.root), 2)

    def test_all_imported_exits_0(self):
        (self.root / "helper.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / "__main__.py").write_text("import helper\n", encoding="utf-8")
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
        (self.root / "orphan.py").write_text("x = 1\n", encoding="utf-8")
        r = self._run("unused-files", "orphan.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("not meaningful for a partial selection", r.stdout)

    def test_bad_flag_exits_1(self):
        r = self._run("unused-files", "--bogus")
        self.assertEqual(r.returncode, 1, r.stderr)


if __name__ == "__main__":
    unittest.main()
