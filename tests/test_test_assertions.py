"""Unit + CLI tests for the `test-assertions` subcommand."""

import ast
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from crap4py.test_assertions import check_files, count_assertions, run, summary, test_candidates


def _counts(source: str) -> dict[str, int]:
    tree = ast.parse(textwrap.dedent(source))
    return {label: assertions for label, _, assertions in test_candidates(tree)}


class CountAssertionsTest(unittest.TestCase):
    def _count(self, body: str) -> int:
        return count_assertions(ast.parse(textwrap.dedent(body)).body)

    def test_bare_assert_statements(self):
        self.assertEqual(self._count("assert x == 1\nassert y\n"), 2)

    def test_self_assert_methods(self):
        self.assertEqual(self._count("self.assertEqual(a, 1)\nself.assertIn(b, c)\n"), 2)

    def test_self_fail_counts(self):
        self.assertEqual(self._count("self.fail('boom')\n"), 1)

    def test_raises_attribute_call(self):
        self.assertEqual(self._count("with pytest.raises(ValueError):\n    pass\n"), 1)

    def test_bare_raises_and_fail(self):
        self.assertEqual(self._count("raises(Exception)\nfail('x')\n"), 2)

    def test_other_self_methods_do_not_count(self):
        self.assertEqual(self._count("self.setUp()\nself.tearDown()\n"), 0)

    def test_non_assert_calls_do_not_count(self):
        self.assertEqual(self._count("print('a')\nobj.get('status')\n"), 0)


class TestCandidatesTest(unittest.TestCase):
    def test_unittest_methods_and_pytest_functions(self):
        counts = _counts(
            """
            def test_plain():
                assert True

            class T(unittest.TestCase):
                def test_ok(self):
                    self.assertEqual(1, 1)

                def test_empty(self):
                    pass

            def helper():
                pass
            """
        )
        self.assertEqual(counts, {"test_plain": 1, "T.test_ok": 1, "T.test_empty": 0})

    def test_async_test_functions_counted(self):
        self.assertEqual(_counts("async def test_async():\n    assert True\n"), {"test_async": 1})


class SkipMetadataTest(unittest.TestCase):
    """crap4dart 0.9.4 regression, Python shape: skip metadata must not hide
    the test body. Dart appends it as trailing named args (``skip:``), Python
    as decorators — either way the real body is still counted."""

    def test_unittest_skip_decorator_does_not_hide_body(self):
        counts = _counts(
            """
            class T(unittest.TestCase):
                @unittest.skip("later")
                def test_skipped_but_empty(self):
                    print("nothing asserted")

                @unittest.skip("later")
                def test_asserted_despite_skip(self):
                    self.assertEqual(1 + 1, 2)
            """
        )
        self.assertEqual(counts["T.test_skipped_but_empty"], 0)
        self.assertEqual(counts["T.test_asserted_despite_skip"], 1)

    def test_pytest_mark_skip_does_not_hide_body(self):
        counts = _counts(
            """
            @pytest.mark.skip(reason="later")
            def test_skipped_but_empty():
                print("nothing asserted")

            @pytest.mark.skipif(False, reason="later")
            def test_asserted_despite_skip():
                assert 1 + 1 == 2
            """
        )
        self.assertEqual(counts["test_skipped_but_empty"], 0)
        self.assertEqual(counts["test_asserted_despite_skip"], 1)


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

    def test_flags_assertion_free_tests(self):
        self._write(
            "tests/test_app.py",
            """
            def test_placeholder():
                do_work()
            """,
        )
        result = check_files([self.root / "tests" / "test_app.py"], self.root)
        self.assertEqual(result.checked, 1)
        self.assertEqual(len(result.violations), 1)
        self.assertIn("a test without assertions verifies nothing", result.violations[0].message)
        self.assertIn("'test_placeholder' has 0 assertion(s)", result.violations[0].message)

    def test_min_threshold_flagged(self):
        self._write(
            "tests/test_app.py",
            """
            def test_single():
                assert one()
            """,
        )
        path = self.root / "tests" / "test_app.py"
        self.assertEqual(check_files([path], self.root, min_assertions=1).violations, [])
        self.assertEqual(len(check_files([path], self.root, min_assertions=2).violations), 1)

    def test_summary_lines(self):
        self.assertEqual(
            summary(type("R", (), {"violations": [], "checked": 3})()),
            "3 tests assert their expectations",
        )
        self.assertEqual(
            summary(type("R", (), {"violations": [1], "checked": 3})()),
            "1/3 tests without assertions",
        )


class RunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "test_app.py").write_text(
            "def test_empty():\n    pass\n\n\ndef test_ok():\n    assert True\n",
            encoding="utf-8",
        )
        self.addCleanup(self._tmp.cleanup)

    def test_run_exits_2_on_violations(self):
        self.assertEqual(run([], self.root), 2)

    def test_explicit_dir_selects_test_files(self):
        self.assertEqual(run(["tests"], self.root), 2)

    def test_non_test_paths_select_nothing(self):
        (self.root / "app.py").write_text("def test_looks_like_test():\n    pass\n")
        self.assertEqual(run(["app.py"], self.root), 0)


class CliSubprocessTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "test_x.py").write_text("def test_noop():\n    pass\n", encoding="utf-8")
        self.addCleanup(self._tmp.cleanup)

    def test_cli_reports_and_exits_2(self):
        r = subprocess.run(
            [sys.executable, "-m", "crap4py", "test-assertions"],
            cwd=self.root,
            capture_output=True,
            text=True,
            env={"PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("test_x.py:1: 'test_noop' has 0 assertion(s)", r.stdout)
        self.assertIn("1/1 tests without assertions", r.stdout)

    def test_cli_bad_flag_exits_1(self):
        r = subprocess.run(
            [sys.executable, "-m", "crap4py", "test-assertions", "--bogus"],
            cwd=self.root,
            capture_output=True,
            text=True,
            env={"PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
        )
        self.assertEqual(r.returncode, 1, r.stderr)


if __name__ == "__main__":
    unittest.main()
