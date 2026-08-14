"""Unit + CLI tests for the `nesting` subcommand."""

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from crap4py.nesting import check_files, function_depths, max_nesting, run, summary

# One function, five nested control-flow blocks -> depth 6 (> MAX_NESTING 5).
_DEEP = (
    "def f(a):\n"
    "    if a:\n"
    "        for b in a:\n"
    "            while b:\n"
    "                with b:\n"
    "                    if b:\n"
    "                        return b\n"
)


def _depths(source: str) -> dict[str, int]:
    import ast

    return {
        name: depth for name, _line, depth in function_depths(ast.parse(textwrap.dedent(source)))
    }


class MaxNestingTest(unittest.TestCase):
    def test_flat_function_is_level_1(self):
        import ast

        func = ast.parse("def f():\n    return 1\n").body[0]
        self.assertEqual(max_nesting(func), 1)

    def test_single_if_is_level_2(self):
        self.assertEqual(_depths("def f(a):\n    if a:\n        return 1\n")["f"], 2)

    def test_each_control_construct_adds_one(self):
        self.assertEqual(
            _depths(
                """
                def f(a):
                    if a:
                        for b in a:
                            while b:
                                with b:
                                    if b:
                                        return b
                """
            )["f"],
            6,
        )

    def test_elif_chain_each_adds_a_level(self):
        self.assertEqual(
            _depths(
                """
                def f(a):
                    if a == 1:
                        return 1
                    elif a == 2:
                        return 2
                    elif a == 3:
                        return 3
                """
            )["f"],
            4,
        )

    def test_try_and_each_except_add_a_level(self):
        self.assertEqual(
            _depths(
                """
                def f(a):
                    try:
                        if a:
                            return 1
                    except ValueError:
                        for b in a:
                            return b
                    finally:
                        return 2
                """
            )["f"],
            4,  # try=2, if=3, except body=4 (try + handler)
        )

    def test_match_case_adds_a_level(self):
        self.assertEqual(
            _depths(
                """
                def f(a):
                    match a:
                        case 1:
                            return 1
                        case _:
                            return 2
                """
            )["f"],
            2,
        )

    def test_nested_def_does_not_count_toward_parent(self):
        depths = _depths(
            """
            def outer(b):
                def inner(c):
                    if c:
                        for d in c:
                            while d:
                                return d
                if b:
                    return inner
                return None
            """
        )
        self.assertEqual(depths["outer"], 2)  # inner's body does not add to outer
        self.assertEqual(depths["outer.inner"], 4)

    def test_methods_are_qualified_by_class(self):
        depths = _depths(
            """
            class C:
                def m(self, a):
                    if a:
                        for b in a:
                            return b
            """
        )
        self.assertEqual(depths["C.m"], 3)


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

    def test_violation_reported_with_line(self):
        self._write(
            "deep.py",
            """
            def deep(a):
                if a:
                    for b in a:
                        while b:
                            with b:
                                if b:
                                    for c in b:
                                        return c
            """,
        )
        result = check_files([self.root / "deep.py"], self.root)
        self.assertEqual(len(result.violations), 1)
        self.assertEqual(result.violations[0].file, "deep.py")
        self.assertEqual(result.violations[0].line, 2)
        self.assertEqual(result.violations[0].depth, 7)
        self.assertEqual(summary(result), "1/1 functions exceed nesting depth 5")

    def test_clean_function_passes(self):
        self._write("flat.py", "def f(a):\n    if a:\n        return 1\n")
        result = check_files([self.root / "flat.py"], self.root)
        self.assertEqual(result.violations, [])
        self.assertEqual(summary(result), "1 functions within nesting depth 5")

    def test_test_files_skipped(self):
        self._write(
            "test_deep.py",
            "def f(a):\n    if a:\n        for b in a:\n            return b\n",
        )
        result = check_files([self.root / "test_deep.py"], self.root)
        self.assertEqual(result.checked, 0)

    def test_broken_file_skipped(self):
        self._write("broken.py", "def f(:\n")
        result = check_files([self.root / "broken.py"], self.root)
        self.assertEqual((result.checked, result.violations), (0, []))


class RunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_no_files_exits_0(self):
        self.assertEqual(run([], self.root), 0)

    def test_violations_exit_2(self):
        (self.root / "deep.py").write_text(_DEEP, encoding="utf-8")
        self.assertEqual(run([], self.root), 2)

    def test_explicit_path_arg(self):
        (self.root / "sub").mkdir()
        (self.root / "sub" / "deep.py").write_text(_DEEP, encoding="utf-8")
        self.assertEqual(run(["sub"], self.root), 2)


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
        r = self._run("nesting", "--bogus")
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_deep_function_exits_2(self):
        (self.root / "deep.py").write_text(_DEEP, encoding="utf-8")
        r = self._run("nesting")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("nesting depth 6 > 5", r.stdout)


if __name__ == "__main__":
    unittest.main()
