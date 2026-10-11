"""Tests for the ``mutation`` subcommand (crap4dart c865ec3 mutation gate port)."""

import ast
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crap4py.coverage import parse_coverage_dict
from crap4py.mutation import (
    Killed,
    MutationSite,
    MutationUnavailable,
    Survived,
    Timeout,
    default_test_command,
    detect_sites,
    run,
    run_mutant_tests,
    run_mutants,
    scan_files,
)

# `add` is on executable line 2, `zero` on line 6.
FIXTURE = "def add(a, b):\n    return a + b\n\n\ndef zero():\n    return 0\n"
TEST_BODY = (
    "import sys, pathlib\n"
    "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))\n"
    "import a\n"
    "import unittest\n"
    "\n"
    "\n"
    "class ATest(unittest.TestCase):\n"
    "    def test_add(self):\n"
    "        self.assertEqual(a.add(2, 3), 5)\n"
    "\n"
    "    def test_zero(self):\n"
    "        self.assertEqual(a.zero(), 0)\n"
)


def _detect(source, rel="a.py", categories=None):
    if categories is None:
        return detect_sites(ast.parse(source), source, rel)
    return detect_sites(ast.parse(source), source, rel, categories)


def _coverage(root, executed, missing=(), rel="a.py", write=True):
    data = {"files": {rel: {"executed_lines": list(executed), "missing_lines": list(missing)}}}
    if write:
        (root / "coverage.json").write_text(json.dumps(data))
    return parse_coverage_dict(data, root)


def _project(source=FIXTURE, test_body=TEST_BODY, coverage=([2, 6], ()), tests=True):
    """A temp project: ``a.py`` source, ``tests/test_a.py``, ``coverage.json``."""
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    (root / "a.py").write_text(source)
    if tests:
        (root / "tests").mkdir()
        (root / "tests" / "test_a.py").write_text(test_body)
    if coverage is not None:
        _coverage(root, *coverage)
    return tmp, root


def _scan(root, coverage, **kwargs):
    return scan_files([root / "a.py"], root, coverage, **kwargs)


def _killed_runner(calls=None):
    def runner(root, timeout):
        if calls is not None:
            calls.append(root)
        return Killed

    return runner


def _survived_runner(calls=None):
    def runner(root, timeout):
        if calls is not None:
            calls.append(root)
        return Survived

    return runner


class OperatorTableTest(unittest.TestCase):
    def test_arithmetic_rows_produce_replacement_pairs(self):
        sites = _detect("def f(a, b):\n    return a + b\n")
        self.assertEqual([s.original for s in sites], ["+"])
        self.assertEqual(sites[0].mutant, "-")
        self.assertEqual(sites[0].category, "arithmetic")

    def test_division_yields_two_mutants(self):
        sites = _detect("def f(a, b):\n    return a / b\n")
        self.assertEqual([s.mutant for s in sites], ["*", "//"])

    def test_floor_division_is_never_an_original(self):
        # Upstream's table is asymmetric: `/` mutates to `*` and `~/`, but a
        # `~/` expression has no row — Python `//` maps to that lexeme.
        self.assertEqual(_detect("def f(a, b):\n    return a // b\n"), [])

    def test_boundary_rows_match_comparison_operators(self):
        sites = _detect("def f(a, b):\n    return a > b\n")
        self.assertEqual([s.mutant for s in sites], [">=", "<"])

    def test_equality_logical_constant_rows_match_together(self):
        source = "def f(a, b):\n    return a == b and b != 0\n"
        sites = _detect(source)
        self.assertEqual(
            [f"{s.original}->{s.mutant}" for s in sites],
            ["==->!=", "and->or", "!=->==", "0->1"],
        )

    def test_boolean_literals_flip(self):
        sites = _detect("def f():\n    return True\n\ndef g():\n    return False\n")
        self.assertEqual(
            [f"{s.original}->{s.mutant}" for s in sites], ["True->False", "False->True"]
        )
        self.assertEqual({s.category for s in sites}, {"boolean"})

    def test_integer_literals_zero_and_one_flip_others_ignored(self):
        sites = _detect(
            "def f():\n    return 0\n\ndef g():\n    return 1\n\ndef h():\n    return 2\n"
        )
        self.assertEqual([f"{s.original}->{s.mutant}" for s in sites], ["0->1", "1->0"])

    def test_negative_one_becomes_zero_across_whole_span(self):
        source = "def f():\n    return -1\n"
        sites = _detect(source)
        neg = [s for s in sites if s.original == "-1"][0]
        self.assertEqual(neg.category, "constant")
        self.assertEqual(neg.apply_to(source), "def f():\n    return 0\n")

    def test_not_prefix_deletion_keeps_operand(self):
        source = "def f(a):\n    return not a\n"
        sites = _detect(source)
        self.assertEqual(len(sites), 1)
        self.assertEqual(sites[0].category, "unary")
        self.assertEqual(sites[0].mutant, "")
        # Span-exact deletion: only the `not` token goes; the following
        # space stays (upstream deletes just the `!` prefix, operand as-is).
        self.assertEqual(sites[0].apply_to(source), "def f(a):\n    return  a\n")

    def test_category_filter_limits_sites(self):
        source = "def f(a, b):\n    return a > b and a == 0\n"
        self.assertEqual(
            {s.category for s in _detect(source, categories=("equality",))}, {"equality"}
        )
        self.assertEqual(_detect(source, categories=()), [])
        self.assertEqual(
            [s.category for s in _detect(source, categories=("unary", "boundary"))][0], "boundary"
        )

    def test_sites_carry_file_line_and_method(self):
        source = "class Calc:\n    def add(self, a, b):\n        return a + b\n"
        sites = _detect(source)
        self.assertEqual(len(sites), 1)
        self.assertEqual(sites[0].file, "a.py")
        self.assertEqual(sites[0].line, 3)
        self.assertEqual(sites[0].method, "Calc.add")

    def test_top_level_function_gets_its_name_as_method(self):
        sites = _detect("def f(a, b):\n    return a * b\n")
        self.assertEqual(sites[0].method, "f")

    def test_offset_based_mutation_rewrites_source(self):
        source = FIXTURE
        sites = _detect(source)
        self.assertEqual(len(sites), 2)
        self.assertEqual(
            sites[0].apply_to(source),
            "def add(a, b):\n    return a - b\n\n\ndef zero():\n    return 0\n",
        )
        self.assertIn("return a + b", source)


class EngineTest(unittest.TestCase):
    def setUp(self):
        self._tmp, self.root = _project()
        self.path = self.root / "a.py"

    def tearDown(self):
        self._tmp.cleanup()

    def _site(self, original, mutant, start, end):
        return MutationSite(
            file="a.py",
            line=1,
            method=None,
            category="arithmetic",
            original=original,
            mutant=mutant,
            start=start,
            end=end,
        )

    def test_mutates_one_site_at_a_time_and_restores(self):
        seen = []
        text = self.path.read_text()
        sites = [
            self._site("+", "X", text.index("+"), text.index("+") + 1),
            self._site("0", "Y", text.index("0"), text.index("0") + 1),
        ]

        def runner(root, timeout):
            seen.append(self.path.read_text())
            return Killed

        records, integrity = run_mutants(str(self.root), sites, 5, runner)
        self.assertEqual(seen, [text.replace("+", "X"), text.replace("0", "Y")])
        self.assertEqual(self.path.read_text(), text)
        self.assertEqual([record.outcome for record in records], [Killed, Killed])
        self.assertEqual(integrity, [])

    def test_restores_when_runner_raises(self):
        text = self.path.read_text()
        site = self._site("+", "X", text.index("+"), text.index("+") + 1)

        def runner(root, timeout):
            raise MutationUnavailable("python not found on PATH: no such file")

        with self.assertRaises(MutationUnavailable):
            run_mutants(str(self.root), [site], 5, runner)
        self.assertEqual(self.path.read_text(), text)


class MutantRunnerTest(unittest.TestCase):
    def setUp(self):
        self._tmp, self.root = _project(tests=False)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_test(self, body):
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_probe.py").write_text(body)

    def test_clean_suite_survives_and_failing_suite_kills(self):
        ok = (
            "import unittest\n\n\nclass T(unittest.TestCase):\n"
            "    def test_ok(self):\n"
            "        pass\n"
        )
        self._write_test(ok)
        self.assertEqual(run_mutant_tests(str(self.root), 60), Survived)
        (self.root / "tests" / "test_probe.py").write_text(
            "import unittest\n\n\nclass T(unittest.TestCase):\n"
            "    def test_bad(self):\n        self.fail('killed')\n"
        )
        self.assertEqual(run_mutant_tests(str(self.root), 60), Killed)

    def test_broken_import_kills(self):
        self._write_test("import nonexisting_module_xyz\n")
        self.assertEqual(run_mutant_tests(str(self.root), 60), Killed)

    def test_hanging_suite_times_out_and_is_killed(self):
        holder = {}

        def spawn(cmd, **kwargs):
            proc = subprocess.Popen(["sleep", "30"], **kwargs)
            holder["proc"] = proc
            return proc

        self.assertEqual(run_mutant_tests(str(self.root), 1, spawn=spawn), Timeout)
        self.assertIsNotNone(holder["proc"].poll())

    def test_spawn_failure_raises_unavailable(self):
        def spawn(cmd, **kwargs):
            raise FileNotFoundError("no such file")

        with self.assertRaises(MutationUnavailable):
            run_mutant_tests(str(self.root), 60, spawn=spawn)

    def test_default_command_prefers_pytest_falls_back_to_unittest(self):
        with mock.patch("importlib.util.find_spec", return_value=object()):
            self.assertEqual(default_test_command(str(self.root)), [sys.executable, "-m", "pytest"])
        with mock.patch("importlib.util.find_spec", return_value=None):
            self.assertEqual(
                default_test_command(str(self.root)),
                [sys.executable, "-m", "unittest", "discover", "-s", "test"],
            )


class GateVerdictTest(unittest.TestCase):
    """Gate-level tests with an injected runner (real subprocess E2E below)."""

    def setUp(self):
        self._tmp, self.root = _project()
        self.coverage = _coverage(self.root, [2, 6])

    def tearDown(self):
        self._tmp.cleanup()

    def test_passes_when_every_covered_mutant_is_killed(self):
        calls = []
        result = _scan(self.root, self.coverage, runner=_killed_runner(calls))
        self.assertEqual(result.violations, ())
        self.assertIn("score 100.0%", result.summary)
        self.assertIn("2 killed", result.summary)
        self.assertIn("0 uncovered", result.summary)
        self.assertEqual(len(calls), 2)

    def test_fails_with_one_violation_per_survived_mutant(self):
        result = _scan(self.root, self.coverage, runner=_survived_runner())
        self.assertEqual(len(result.violations), 2)
        first = result.violations[0]
        self.assertEqual(first.file, "a.py")
        self.assertEqual(first.line, 2)
        self.assertIn("survived arithmetic mutant", first.message)
        self.assertIn("`+` -> `-`", first.message)
        self.assertIn("in add", first.message)
        self.assertIn("score 0.0%", result.summary)
        self.assertIn("survivors by category: arithmetic=1, constant=1", result.summary)

    def test_timeouts_count_as_survived(self):
        def runner(root, timeout):
            return Timeout

        result = _scan(self.root, self.coverage, runner=runner)
        self.assertEqual(len(result.violations), 2)

    def test_uncovered_sites_are_excluded_from_score(self):
        coverage = _coverage(self.root, [6], [2])  # only `zero` covered
        calls = []
        result = _scan(self.root, coverage, runner=_survived_runner(calls))
        self.assertFalse(result.violations == ())
        self.assertEqual(len(result.violations), 1)
        self.assertEqual(result.violations[0].line, 6)
        self.assertIn("score 0.0%", result.summary)
        self.assertIn("1 survived", result.summary)
        self.assertIn("1 uncovered", result.summary)
        self.assertEqual(len(calls), 1)

    def test_all_uncovered_passes_without_running_mutants(self):
        coverage = _coverage(self.root, [], [2, 6])
        calls = []
        result = _scan(self.root, coverage, runner=_killed_runner(calls))
        self.assertEqual(result.violations, ())
        self.assertEqual(calls, [])
        self.assertEqual(result.summary, "no covered mutation sites (2 uncovered)")

    def test_file_missing_from_coverage_contributes_nothing(self):
        coverage = _coverage(self.root, [1], rel="other.py")
        calls = []
        result = _scan(self.root, coverage, runner=_killed_runner(calls))
        self.assertEqual(result.violations, ())
        self.assertEqual(calls, [])
        self.assertEqual(result.summary, "no covered mutation sites (2 uncovered)")

    def test_min_score_0_still_fails_on_survivors_when_max_survivors_0(self):
        result = _scan(self.root, self.coverage, min_score=0.0, runner=_survived_runner())
        self.assertNotEqual(result.violations, ())

    def test_low_min_score_passes_with_a_lone_survivor(self):
        calls = []

        def runner(root, timeout):
            calls.append(1)
            return Killed if len(calls) == 1 else Survived

        result = _scan(self.root, self.coverage, min_score=0.4, max_survivors=1, runner=runner)
        self.assertEqual(result.violations, ())

    def test_caps_executed_mutants_at_max_mutants(self):
        calls = []
        result = _scan(self.root, self.coverage, max_mutants=1, runner=_killed_runner(calls))
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.violations, ())
        self.assertIn("1 killed", result.summary)

    def test_excluded_files_leave_no_sites(self):
        result = _scan(self.root, self.coverage, excludes=["a.py"], runner=_killed_runner())
        self.assertEqual(result.violations, ())
        self.assertEqual(result.summary, "no mutation sites")

    def test_operator_subset_restricts_sites(self):
        coverage = _coverage(self.root, [2])  # `add` covered, `zero` not
        calls = []
        result = _scan(self.root, coverage, operators=("constant",), runner=_killed_runner(calls))
        # `constant` leaves the `+` site undetected; the `0` site is uncovered.
        self.assertEqual(calls, [])
        self.assertEqual(result.summary, "no covered mutation sites (1 uncovered)")

    def test_skips_without_coverage(self):
        result = _scan(self.root, None, runner=_killed_runner())
        self.assertEqual(result.violations, ())
        self.assertIn("no coverage data", result.summary)

    def test_skips_without_tests_dir(self):
        import shutil

        shutil.rmtree(self.root / "tests")
        result = _scan(self.root, self.coverage, runner=_killed_runner())
        self.assertEqual(result.violations, ())
        self.assertIn("no tests directory", result.summary)

    def test_skips_when_test_command_unavailable(self):
        def runner(root, timeout):
            raise MutationUnavailable("python not found on PATH: no such file")

        result = _scan(self.root, self.coverage, runner=runner)
        self.assertEqual(result.violations, ())
        self.assertIn("skipped: python not found on PATH", result.summary)


class EndToEndTest(unittest.TestCase):
    """Real subprocess runs: the temp project's suite kills or misses mutants."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_killing_suite_passes_with_full_score(self):
        (self.root / "a.py").write_text("def add(a, b):\n    return a + b\n")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_a.py").write_text(
            "import sys, pathlib\n"
            "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))\n"
            "import a\n"
            "import unittest\n\n\n"
            "class ATest(unittest.TestCase):\n"
            "    def test_add(self):\n            self.assertEqual(a.add(2, 3), 5)\n"
        )
        _coverage(self.root, [2])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = run(["--timeout-seconds", "60"], self.root)
        self.assertEqual(code, 0)
        self.assertIn("score 100.0%", out.getvalue())
        self.assertIn("1 killed", out.getvalue())

    def test_assertion_free_suite_lets_mutant_survive(self):
        (self.root / "a.py").write_text("def add(a, b):\n    return a + b\n")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_a.py").write_text(
            "import sys, pathlib\n"
            "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))\n"
            "import a\n"
            "import unittest\n\n\n"
            "class ATest(unittest.TestCase):\n"
            "    def test_add(self):\n            a.add(2, 3)\n"
        )
        _coverage(self.root, [2])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = run(["--timeout-seconds", "60"], self.root)
        self.assertEqual(code, 2)
        self.assertIn("a.py:2: survived arithmetic mutant `+` -> `-` in add", out.getvalue())
        self.assertIn("score 0.0%", out.getvalue())

    def test_import_error_mutant_counts_as_killed(self):
        (self.root / "a.py").write_text("DIVISOR = 1\nRATIO = 10 / DIVISOR\n")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_a.py").write_text(
            "import sys, pathlib\n"
            "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))\n"
            "import a\n"
            "import unittest\n\n\n"
            "class ATest(unittest.TestCase):\n"
            "    def test_ratio(self):\n            self.assertEqual(a.RATIO, 10.0)\n"
        )
        _coverage(self.root, [1, 2])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = run(["--timeout-seconds", "60"], self.root)
        # `1 -> 0` breaks the module import (ZeroDivisionError) -> killed;
        # `/ -> *` and `/ -> //` still yield 10.0 -> survived, gate fails.
        self.assertEqual(code, 2)
        self.assertIn("score 33.3%", out.getvalue())
        self.assertIn("1 killed", out.getvalue())
        self.assertIn("2 survived", out.getvalue())
        self.assertIn("a.py:2: survived arithmetic mutant `/` -> `*`", out.getvalue())
        self.assertIn("a.py:2: survived arithmetic mutant `/` -> `//`", out.getvalue())
        self.assertNotIn("`1` -> `0`", out.getvalue())


class MutationCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp, self.root = _project()

    def tearDown(self):
        self._tmp.cleanup()

    def test_run_skips_without_coverage_file(self):
        (self.root / "coverage.json").unlink()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = run([], self.root)
        self.assertEqual(code, 0)
        self.assertIn("skipped: no coverage data", out.getvalue())

    def test_run_reports_violations_and_exit_2(self):
        # Make the mutation visible: zero the suite's assertions so it survives.
        (self.root / "tests" / "test_a.py").write_text(
            "import sys, pathlib\n"
            "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))\n"
            "import a\n"
            "import unittest\n\n\n"
            "class ATest(unittest.TestCase):\n"
            "    def test_add(self):\n            a.add(2, 3)\n"
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = run(["--timeout-seconds", "60", "--max-mutants", "1"], self.root)
        self.assertEqual(code, 2)
        self.assertIn("a.py:2:", out.getvalue())
        self.assertIn("score 0.0%", out.getvalue())

    def test_unknown_operator_exits_1(self):
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()) as err,
        ):
            with self.assertRaises(SystemExit):
                run(["--operator", "regex"], self.root)
        self.assertIn("invalid choice", err.getvalue())

    def test_plain_analyze_never_runs_mutants(self):
        # Disabled by default: plain `crap4py` analysis on the same project
        # (coverage + tests + mutants present) prints no mutation output.
        proc = subprocess.run(
            [sys.executable, "-m", "crap4py"],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            env={**dict(__import__("os").environ), "PYTHONPATH": str(Path(__file__).parent.parent)},
        )
        self.assertNotIn("survived", proc.stdout)
        self.assertNotIn("score ", proc.stdout)
        self.assertNotIn("killed", proc.stdout)

    def test_subcommand_dispatched_via_module(self):
        proc = subprocess.run(
            [sys.executable, "-m", "crap4py", "mutation", "--coverage", "missing.json"],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            env={**dict(__import__("os").environ), "PYTHONPATH": str(Path(__file__).parent.parent)},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("skipped: no coverage data", proc.stdout)


if __name__ == "__main__":
    unittest.main()
