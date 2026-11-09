"""Unit + CLI tests for the `test-coverage` subcommand."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from crap4py.coverage import FileCoverage
from crap4py.coverage_gate import check_coverage, run, summary

FIXTURES = Path(__file__).parent / "fixtures"


def _coverage_json(executed, missing, key="sample.py"):
    return {
        "files": {
            key: {"executed_lines": executed, "missing_lines": missing},
        }
    }


class TotalRatioTest(unittest.TestCase):
    def test_statement_hit_ratio_across_files(self):
        records = {
            "a.py": FileCoverage(set(range(1, 7)), set(range(7, 16))),  # 6/15
            "b.py": FileCoverage({1, 2, 3, 4}, {5}),  # 4/5
        }
        self.assertAlmostEqual(check_coverage(records).percent, 10 / 20 * 100)

    def test_no_statements_scores_100(self):
        records = {"a.py": FileCoverage(set(), set())}
        self.assertEqual(check_coverage(records).percent, 100.0)

    def test_exclude_drops_files_from_ratio(self):
        records = {
            "a.py": FileCoverage({1, 2}, {3, 4, 5, 6, 7, 8}),  # 2/8 = 25%
            "gen/b.py": FileCoverage({1}, set()),
        }
        result = check_coverage(records, excludes=["gen/*"])
        self.assertAlmostEqual(result.percent, 25.0)

    def test_fixture_json_ratio(self):
        from crap4py.coverage import load_coverage_records

        records = load_coverage_records(FIXTURES / "coverage.json")
        self.assertAlmostEqual(check_coverage(records).percent, 40.0)


class CheckCoverageTest(unittest.TestCase):
    def test_below_min_violates(self):
        records = {"a.py": FileCoverage({1}, {2, 3, 4, 5})}
        result = check_coverage(records, min_percent=50.0)
        self.assertEqual(result.violations, ["total coverage 20.0% < min 50.0%"])

    def test_exactly_at_min_passes(self):
        records = {"a.py": FileCoverage({1, 2, 3, 4}, {5, 6, 7, 8})}  # 50%
        result = check_coverage(records, min_percent=50.0)
        self.assertEqual(result.violations, [])
        self.assertEqual(summary(result, 50.0), "coverage 50.0% >= 50.0%")

    def test_above_min_passes(self):
        records = {"a.py": FileCoverage({1, 2, 3}, {4})}
        result = check_coverage(records, min_percent=50.0)
        self.assertEqual(result.violations, [])
        self.assertEqual(summary(result, 50.0), "coverage 75.0% >= 50.0%")

    def test_violation_names_min_percent(self):
        records = {"a.py": FileCoverage({1, 2}, {3})}
        result = check_coverage(records, min_percent=99.0)
        self.assertEqual(result.violations, ["total coverage 66.7% < min 99.0%"])
        self.assertEqual(summary(result, 99.0), "coverage 66.7% < 99.0%")


class RunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _write_coverage(self, data, name="coverage.json"):
        path = self.root / name
        if isinstance(data, str):
            path.write_text(data, encoding="utf-8")
        else:
            path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_missing_coverage_fails_closed(self):
        self.assertEqual(run([], self.root), 1)

    def test_empty_records_fails_closed(self):
        self._write_coverage({"files": {}})
        self.assertEqual(run([], self.root), 1)

    def test_unparseable_json_fails_closed(self):
        self._write_coverage("{not json")
        self.assertEqual(run([], self.root), 1)

    def test_below_min_exits_2_with_message(self):
        self._write_coverage(_coverage_json([1], [2, 3, 4]))
        self.assertEqual(run([], self.root), 2)

    def test_above_min_exits_0(self):
        self._write_coverage(_coverage_json([1, 2, 3, 4], [5]))  # exactly 80%
        self.assertEqual(run([], self.root), 0)

    def test_min_percent_flag_relaxes(self):
        self._write_coverage(_coverage_json([1], [2, 3, 4]))  # 25%
        self.assertEqual(run(["--min-percent", "20"], self.root), 0)

    def test_coverage_flag_selects_input(self):
        self._write_coverage(_coverage_json([1, 2, 3], []), name="cov.info.json")
        self.assertEqual(run(["--coverage", "cov.info.json"], self.root), 0)

    def test_lcov_info_parsed(self):
        self._write_coverage("SF:sample.py\nDA:1,1\nDA:2,0\nend_of_record\n", name="cov.info")
        self.assertEqual(run(["--coverage", "cov.info"], self.root), 2)

    def test_exclude_drops_file_from_ratio(self):
        data = {
            "files": {
                "gen/bad.py": {"executed_lines": [1], "missing_lines": [2, 3, 4]},  # 25%
                "good.py": {"executed_lines": [1, 2, 3, 4], "missing_lines": [5]},  # 80%
            }
        }
        (self.root / "unused.json").write_text(json.dumps(data), encoding="utf-8")
        # combined 5/10 = 50% fails; excluding the generated file leaves 80% and passes
        self.assertEqual(run(["--coverage", "unused.json", "--exclude", "gen/*"], self.root), 0)
        self.assertEqual(run(["--coverage", "unused.json"], self.root), 2)

    def test_foreign_records_ignored(self):
        foreign = list(range(1, 13))  # 12/12 covered — would inflate any ratio
        data = {
            "files": {
                "/usr/lib/py3/sitepkg.py": {"executed_lines": foreign, "missing_lines": []},
                "local.py": {"executed_lines": [1], "missing_lines": [2, 3, 4]},  # 25%
            }
        }
        (self.root / "mixed.json").write_text(json.dumps(data), encoding="utf-8")
        # unfiltered: 13/15 = 86.7% >= 80 would falsely pass; local-only must fail
        self.assertEqual(run(["--coverage", "mixed.json"], self.root), 2)

    def test_foreign_only_fails_closed(self):
        data = {
            "files": {
                "/usr/lib/py3/sitepkg.py": {"executed_lines": [1, 2, 3], "missing_lines": []},
            }
        }
        (self.root / "foreign.json").write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(run(["--coverage", "foreign.json"], self.root), 1)

    def test_positional_path_is_usage_error(self):
        self._write_coverage(_coverage_json([1], []))
        with self.assertRaises(SystemExit) as cm:
            run(["some/path.py"], self.root)
        self.assertEqual(cm.exception.code, 1)


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
        r = self._run("test-coverage", "--bogus")
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_missing_coverage_error_on_stderr(self):
        r = self._run("test-coverage")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("coverage file not found", r.stderr)

    def test_below_min_exits_2_with_message(self):
        (self.root / "coverage.json").write_text(json.dumps(_coverage_json([1], [2, 3, 4])))
        r = self._run("test-coverage")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("coverage.json: total coverage 25.0% < min 80.0%", r.stdout)
        self.assertIn("coverage 25.0% < 80.0%", r.stdout)


if __name__ == "__main__":
    unittest.main()
