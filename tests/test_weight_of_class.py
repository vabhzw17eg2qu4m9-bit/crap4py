"""Unit + CLI tests for the `weight-of-class` subcommand."""

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from crap4py.weight_of_class import check_files, public_self_fields, run, summary


def _parse(source: str):
    import ast

    return ast.parse(textwrap.dedent(source))


class PublicSelfFieldsTest(unittest.TestCase):
    def test_fields_collected_from_init_and_methods(self):
        tree = _parse(
            """
            class C:
                def __init__(self):
                    self.a = 1
                def set(self):
                    self.b = 2
            """
        )
        node = tree.body[0]
        self.assertEqual(public_self_fields(node), {"a", "b"})

    def test_private_and_dunder_attrs_excluded(self):
        tree = _parse(
            """
            class C:
                def __init__(self):
                    self._hidden = 1
                    self.__mangled = 2
                    self.visible = 3
            """
        )
        self.assertEqual(public_self_fields(tree.body[0]), {"visible"})

    def test_distinct_targets_only(self):
        tree = _parse(
            """
            class C:
                def a(self):
                    self.x = 1
                def b(self):
                    self.x = 2
            """
        )
        self.assertEqual(public_self_fields(tree.body[0]), {"x"})

    def test_local_variables_not_fields(self):
        tree = _parse(
            """
            class C:
                def a(self):
                    x = 1
                    return x
            """
        )
        self.assertEqual(public_self_fields(tree.body[0]), set())


class WeightViolationTest(unittest.TestCase):
    def _violation(self, source):
        from crap4py.weight_of_class import weight_violation

        tree = _parse(source)
        return weight_violation(tree.body[0])

    def test_data_heavy_class_flagged(self):
        v = self._violation(
            """
            class Data:
                def __init__(self):
                    self.a = 1
                    self.b = 2
                def go(self):
                    return 1
            """
        )
        self.assertIsNotNone(v)
        self.assertEqual((v.fields, v.members), (2, 3))
        self.assertAlmostEqual(v.ratio, 2 / 3)

    def test_balanced_class_passes(self):
        v = self._violation(
            """
            class Lean:
                def __init__(self):
                    self.a = 1
                def one(self):
                    return 1
                def two(self):
                    return 2
                def three(self):
                    return 3
            """
        )
        self.assertIsNone(v)  # 1 field / 4 members = 0.25 <= 0.33

    def test_class_without_public_fields_never_flagged(self):
        v = self._violation(
            """
            class OnlyMethods:
                def one(self):
                    return 1
            """
        )
        self.assertIsNone(v)

    def test_private_class_never_flagged(self):
        v = self._violation(
            """
            class _Private:
                def __init__(self):
                    self.a = 1
                    self.b = 2
            """
        )
        self.assertIsNone(v)

    def test_static_and_class_methods_excluded_from_members(self):
        v = self._violation(
            """
            class WithStatic:
                def __init__(self):
                    self.a = 1
                @staticmethod
                def helper():
                    return 1
                @classmethod
                def build(cls):
                    return cls
            """
        )
        self.assertIsNotNone(v)  # 1 field / 1 member = 1.0
        self.assertEqual(v.members, 1)


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

    def test_violation_and_summary(self):
        self._write(
            "data.py",
            """
            class Data:
                def __init__(self):
                    self.a = 1
                    self.b = 2
                def go(self):
                    return 1
            """,
        )
        result = check_files([self.root / "data.py"], self.root)
        self.assertEqual(len(result.violations), 1)
        self.assertEqual(result.violations[0].file, "data.py")
        self.assertEqual(summary(result), "1/1 classes reveal more data than behavior")

    def test_test_files_skipped(self):
        self._write(
            "test_data.py",
            """
            class Data:
                def __init__(self):
                    self.a = 1
            """,
        )
        result = check_files([self.root / "test_data.py"], self.root)
        self.assertEqual(result.checked, 0)


class RunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_no_files_exits_0(self):
        self.assertEqual(run([], self.root), 0)

    def test_violations_exit_2(self):
        (self.root / "data.py").write_text(
            "class Data:\n    def __init__(self):\n        self.a = 1\n"
            "        self.b = 2\n    def go(self):\n        return 1\n",
            encoding="utf-8",
        )
        self.assertEqual(run([], self.root), 2)

    def test_explicit_path_arg(self):
        (self.root / "data.py").write_text(
            "class Data:\n    def __init__(self):\n        self.a = 1\n"
            "        self.b = 2\n    def go(self):\n        return 1\n",
            encoding="utf-8",
        )
        self.assertEqual(run(["data.py"], self.root), 2)


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
        r = self._run("weight-of-class", "--bogus")
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_data_class_exits_2(self):
        (self.root / "data.py").write_text(
            "class Data:\n    def __init__(self):\n        self.a = 1\n"
            "        self.b = 2\n    def go(self):\n        return 1\n",
            encoding="utf-8",
        )
        r = self._run("weight-of-class")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("data weight 0.67", r.stdout)


if __name__ == "__main__":
    unittest.main()
