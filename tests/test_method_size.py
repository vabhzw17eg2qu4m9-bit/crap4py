"""Unit + CLI tests for the `method-size` subcommand."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from crap4py.method_size import MAX_LINES, MAX_PARAMS, check_files, run, summary


def _long_method(body: int = 4) -> str:
    lines = ["def big(x):"] + [f"    y = {i}" for i in range(body)] + ["    return y"]
    return "\n".join(lines) + "\n"


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

    def test_long_method_flagged_at_def_line(self):
        p = self._write("long.py", _long_method(body=4))
        result = check_files([p], self.root, max_lines=3)
        self.assertEqual(len(result.violations), 1)
        self.assertEqual(result.violations[0].name, "big")
        self.assertEqual(result.violations[0].line, 1)
        self.assertEqual(result.violations[0].reason, "has 6 lines > max 3")

    def test_exactly_at_line_limit_passes(self):
        p = self._write("ok.py", _long_method(body=2))  # def + 2 + return = 4 lines
        result = check_files([p], self.root, max_lines=4)
        self.assertEqual(result.violations, [])
        self.assertEqual(result.checked, 1)

    def test_over_params_flagged(self):
        p = self._write("wide.py", "def wide(a, b, c, d, e, f, g):\n    return a\n")
        result = check_files([p], self.root)
        self.assertEqual(
            [(v.name, v.reason) for v in result.violations],
            [("wide", "has 7 params > max 6")],
        )

    def test_exactly_at_param_limit_passes(self):
        p = self._write("ok.py", "def six(a, b, c, d, e, f):\n    return a\n")
        result = check_files([p], self.root)
        self.assertEqual(result.violations, [])

    def test_star_kwargs_count_as_params(self):
        p = self._write("var.py", "def v(a, b, c, d, e, *rest, **kw):\n    return a\n")
        result = check_files([p], self.root)
        self.assertEqual(result.violations[0].reason, "has 7 params > max 6")

    def test_positional_only_and_kw_only_count(self):
        p = self._write("pos.py", "def p(a, b, c, d, /, e, *, f, g):\n    return a\n")
        result = check_files([p], self.root)
        self.assertEqual(result.violations[0].reason, "has 7 params > max 6")

    def test_init_checked_for_params_only(self):
        source = (
            "class K:\n"
            "    def __init__(self):\n"
            "        self.a = 1\n"
            "        self.b = 2\n"
            "        self.c = 3\n"
            "        self.d = 4\n"
        )
        p = self._write("ctor.py", source)
        result = check_files([p], self.root, max_lines=3)
        self.assertEqual(result.violations, [])
        self.assertEqual(result.checked, 1)

    def test_init_over_params_flagged(self):
        source = "class K:\n    def __init__(self, a, b, c, d, e, f, g):\n        self.x = a\n"
        p = self._write("ctor.py", source)
        result = check_files([p], self.root)
        self.assertEqual(
            [(v.name, v.reason) for v in result.violations],
            [("K.__init__", "has 8 params > max 6")],
        )

    def test_both_limits_reported_separately(self):
        source = "def big(a, b, c, d, e, f, g):\n    x = 1\n    x = 2\n    x = 3\n    return x\n"
        p = self._write("both.py", source)
        result = check_files([p], self.root, max_lines=3)
        self.assertEqual(len(result.violations), 2)

    def test_qualified_names_and_nested_functions(self):
        source = (
            "class C:\n"
            "    def m(self):\n"
            "        def inner():\n"
            "            x = 1\n"
            "            x = 2\n"
            "            return x\n"
            "        return inner\n"
        )
        p = self._write("nested.py", source)
        result = check_files([p], self.root, max_lines=3)
        # the nested inner is flagged under its immediate parent's name,
        # while C.m spans (and flags for) the whole nested def — the
        # complexity gate's naming convention
        self.assertEqual(
            [(v.name, v.line) for v in result.violations],
            [("C.m", 2), ("m.inner", 3)],
        )

    def test_test_files_skipped(self):
        p = self._write("tests/long.py", _long_method(body=100))
        result = check_files([p], self.root, max_lines=3)
        self.assertEqual(result.checked, 0)

    def test_summary(self):
        p = self._write("long.py", _long_method(body=4))
        result = check_files([p], self.root, max_lines=3)
        self.assertEqual(summary(result), "1/1 methods over their size limits")
        self.assertEqual(summary(check_files([p], self.root)), "1 methods within their size limits")


class RunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_no_files_exits_0(self):
        self.assertEqual(run([], self.root), 0)

    def test_violations_exit_2(self):
        (self.root / "long.py").write_text(_long_method(body=100), encoding="utf-8")
        self.assertEqual(run([], self.root), 2)

    def test_default_limits_pass_small_methods(self):
        self.assertEqual(MAX_LINES, 60)
        self.assertEqual(MAX_PARAMS, 6)
        (self.root / "ok.py").write_text(_long_method(body=10), encoding="utf-8")
        self.assertEqual(run([], self.root), 0)

    def test_line_marker_suppresses(self):
        (self.root / "long.py").write_text(
            "def big(x):  # ignore:method_size\n" + "    y = 1\n" * 99 + "    return y\n",
            encoding="utf-8",
        )
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

    def test_bad_flag_exits_1(self):
        r = self._run("method-size", "--bogus")
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_violation_exits_2_with_message(self):
        (self.root / "wide.py").write_text(
            "def wide(a, b, c, d, e, f, g):\n    return a\n", encoding="utf-8"
        )
        r = self._run("method-size")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("wide.py:1: wide has 7 params > max 6", r.stdout)


if __name__ == "__main__":
    unittest.main()
