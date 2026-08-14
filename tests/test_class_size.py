"""Unit + CLI tests for the `class-size` subcommand."""

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from crap4py.class_size import check_files, class_totals, run, summary, violation_message


def _totals(source: str):
    import ast

    return list(class_totals(ast.parse(textwrap.dedent(source))))


def _god_class(methods: int, body: str = "        return 1") -> str:
    lines = ["class God:"]
    for i in range(methods):
        lines.append(f"    def m{i}(self):")
        lines.append(f"    {body}")
    return "\n".join(lines) + "\n"


class ClassTotalsTest(unittest.TestCase):
    def test_small_class_within_limits(self):
        (totals,) = _totals(
            """
            class Small:
                def a(self):
                    if self.x:
                        return 1
                    return 2
                def b(self):
                    return 2
            """
        )
        self.assertEqual((totals.name, totals.methods, totals.wmc), ("Small", 2, 3))

    def test_async_methods_count(self):
        (totals,) = _totals(
            """
            class C:
                async def a(self):
                    return 1
            """
        )
        self.assertEqual(totals.methods, 1)

    def test_nested_functions_are_not_methods(self):
        (totals,) = _totals(
            """
            class C:
                def a(self):
                    def inner():
                        return 1
                    return inner
            """
        )
        self.assertEqual(totals.methods, 1)

    def test_wmc_sums_all_method_complexities(self):
        (totals,) = _totals(
            """
            class C:
                def a(self):
                    return 1 if self.x else 2
                def b(self):
                    for i in self.xs:
                        if i:
                            return i
                    return None
            """
        )
        self.assertEqual(totals.wmc, 5)  # a=2, b=3


class ViolationMessageTest(unittest.TestCase):
    def test_within_limits_is_none(self):
        (totals,) = _totals("class Ok:\n    def a(self):\n        return 1\n")
        self.assertIsNone(violation_message(totals))

    def test_too_many_methods(self):
        (totals,) = _totals(_god_class(26))
        self.assertEqual(violation_message(totals), "class God: 26 methods (max 25)")

    def test_wmc_over_limit(self):
        # 17 methods x WMC 5 = 85 > 80, but only 17 methods (<= 25).
        source = ["class Heavy:"]
        for i in range(17):
            source.append(f"    def m{i}(self, x):")
            source.append("        if x:")
            source.append("            for y in x:")
            source.append("                if y:")
            source.append("                    while y:")
            source.append("                        return y")
            source.append("        return None")
        (totals,) = _totals("\n".join(source) + "\n")
        self.assertEqual((totals.methods, totals.wmc), (17, 85))
        self.assertEqual(violation_message(totals), "class Heavy: weighted methods 85 (max 80)")


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

    def test_god_class_flagged(self):
        self._write("god.py", _god_class(26))
        result = check_files([self.root / "god.py"], self.root)
        self.assertEqual(len(result.violations), 1)
        self.assertEqual(result.violations[0].file, "god.py")
        self.assertEqual(summary(result), "1/1 classes over 25 methods/WMC 80")

    def test_small_class_passes(self):
        self._write("ok.py", "class Ok:\n    def a(self):\n        return 1\n")
        result = check_files([self.root / "ok.py"], self.root)
        self.assertEqual(result.violations, [])
        self.assertEqual(summary(result), "1 classes within 25 methods/WMC 80")

    def test_test_files_skipped(self):
        self._write("tests/god.py", _god_class(30))
        result = check_files([self.root / "tests" / "god.py"], self.root)
        self.assertEqual(result.checked, 0)


class RunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_no_files_exits_0(self):
        self.assertEqual(run([], self.root), 0)

    def test_violations_exit_2(self):
        (self.root / "god.py").write_text(_god_class(26), encoding="utf-8")
        self.assertEqual(run([], self.root), 2)

    def test_explicit_path_arg(self):
        (self.root / "god.py").write_text(_god_class(26), encoding="utf-8")
        self.assertEqual(run(["god.py"], self.root), 2)


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
        r = self._run("class-size", "--bogus")
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_god_class_exits_2(self):
        (self.root / "god.py").write_text(_god_class(26), encoding="utf-8")
        r = self._run("class-size")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("class God: 26 methods (max 25)", r.stdout)


if __name__ == "__main__":
    unittest.main()
