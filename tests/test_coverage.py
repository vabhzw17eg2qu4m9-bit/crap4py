"""Unit tests for coverage.py JSON/lcov parsing and method attribution."""

import tempfile
import unittest
from pathlib import Path

from crap4py.coverage import (
    FileCoverage,
    coverage_for_method,
    load_coverage_records,
    parse_coverage_dict,
    parse_lcov,
)


class CoverageAttributionTest(unittest.TestCase):
    def test_full_coverage(self):
        cov = parse_coverage_dict(
            {"files": {"a.py": {"executed_lines": [1, 2, 3], "missing_lines": []}}}
        )
        self.assertAlmostEqual(coverage_for_method(cov["a.py"], 1, 3), 1.0)

    def test_partial_coverage(self):
        cov = parse_coverage_dict(
            {"files": {"a.py": {"executed_lines": [1, 3, 5], "missing_lines": [2, 4]}}}
        )
        # range [1,5]: covered=3, total=5
        self.assertAlmostEqual(coverage_for_method(cov["a.py"], 1, 5), 0.6)

    def test_zero_coverage(self):
        cov = parse_coverage_dict(
            {"files": {"a.py": {"executed_lines": [], "missing_lines": [1, 2, 3]}}}
        )
        self.assertAlmostEqual(coverage_for_method(cov["a.py"], 1, 3), 0.0)

    def test_none_when_no_lines_in_range(self):
        cov = parse_coverage_dict(
            {"files": {"a.py": {"executed_lines": [1, 2], "missing_lines": [3]}}}
        )
        self.assertIsNone(coverage_for_method(cov["a.py"], 10, 20))

    def test_none_when_file_coverage_none(self):
        self.assertIsNone(coverage_for_method(None, 1, 5))

    def test_attribution_ignores_lines_outside_range(self):
        cov = parse_coverage_dict(
            {"files": {"a.py": {"executed_lines": [1, 2, 100], "missing_lines": [3, 200]}}}
        )
        # range [1,3]: executed={1,2}, missing={3}, total=3
        self.assertAlmostEqual(coverage_for_method(cov["a.py"], 1, 3), 2 / 3)


class CoverageParsingTest(unittest.TestCase):
    def test_empty_files_dict(self):
        self.assertEqual(parse_coverage_dict({"files": {}}), {})

    def test_missing_files_key(self):
        self.assertEqual(parse_coverage_dict({"meta": {}}), {})

    def test_relativize_absolute_path(self):
        cov = parse_coverage_dict(
            {"files": {"/tmp/proj/a.py": {"executed_lines": [1], "missing_lines": []}}},
            project_root="/tmp/proj",
        )
        self.assertIn("a.py", cov)
        self.assertNotIn("/tmp/proj/a.py", cov)

    def test_path_outside_root_kept_as_is(self):
        cov = parse_coverage_dict(
            {"files": {"/other/a.py": {"executed_lines": [1], "missing_lines": []}}},
            project_root="/tmp/proj",
        )
        self.assertIn("/other/a.py", cov)

    def test_load_missing_json_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_coverage_records("/nonexistent/path/coverage.json")


class LcovParsingTest(unittest.TestCase):
    def test_parse_lcov_matches_native_format(self):
        lcov = "SF:a.py\nDA:1,1\nDA:2,0\nDA:3,1\nend_of_record\n"
        expected = parse_coverage_dict(
            {"files": {"a.py": {"executed_lines": [1, 3], "missing_lines": [2]}}}
        )
        self.assertEqual(parse_lcov(lcov), expected)

    def test_parse_lcov_relative_sf_resolved_against_root(self):
        cov = parse_lcov("SF:src/a.py\nDA:1,1\nDA:2,0\nend_of_record\n", project_root="/tmp/proj")
        self.assertEqual(cov.get("src/a.py"), FileCoverage(executed_lines={1}, missing_lines={2}))

    def test_parse_lcov_absolute_sf_relativized(self):
        cov = parse_lcov("SF:/tmp/proj/a.py\nDA:1,0\nend_of_record\n", project_root="/tmp/proj")
        self.assertIn("a.py", cov)
        self.assertNotIn("/tmp/proj/a.py", cov)

    def test_parse_lcov_sf_outside_root_kept_as_is(self):
        cov = parse_lcov("SF:/other/a.py\nDA:1,1\nend_of_record\n", project_root="/tmp/proj")
        self.assertIn("/other/a.py", cov)

    def test_parse_lcov_sums_duplicate_sf_records(self):
        lcov = "SF:a.py\nDA:1,2\nDA:2,0\nend_of_record\nSF:a.py\nDA:1,0\nDA:2,3\nend_of_record\n"
        cov = parse_lcov(lcov)
        self.assertEqual(cov["a.py"].executed_lines, {1, 2})
        self.assertEqual(cov["a.py"].missing_lines, set())

    def test_parse_lcov_separate_files(self):
        lcov = "SF:a.py\nDA:1,1\nend_of_record\nSF:b.py\nDA:1,0\nend_of_record\n"
        cov = parse_lcov(lcov)
        self.assertEqual(cov["a.py"].executed_lines, {1})
        self.assertEqual(cov["b.py"].missing_lines, {1})

    def test_parse_lcov_crlf_line_endings(self):
        lcov = "SF:a.py\r\nDA:1,1\r\nDA:2,0\r\nend_of_record\r\n"
        cov = parse_lcov(lcov)
        self.assertEqual(cov["a.py"].executed_lines, {1})
        self.assertEqual(cov["a.py"].missing_lines, {2})

    def test_parse_lcov_ignores_function_and_branch_records(self):
        lcov = (
            "TN:test\nSF:a.py\nFN:1,5,some_fn\nFNDA:1,some_fn\nFNF:1\nFNH:1\n"
            "BRDA:1,0,0,1\nBRF:1\nBRH:1\nLF:2\nLH:1\nDA:1,1\nDA:2,0\nend_of_record\n"
        )
        cov = parse_lcov(lcov)
        self.assertEqual(cov, {"a.py": FileCoverage(executed_lines={1}, missing_lines={2})})

    def test_parse_lcov_malformed_da_raises_value_error(self):
        with self.assertRaises(ValueError):
            parse_lcov("SF:a.py\nDA:x,y\nend_of_record\n")

    def test_parse_lcov_da_without_hits_raises_value_error(self):
        with self.assertRaises(ValueError):
            parse_lcov("SF:a.py\nDA:5\nend_of_record\n")

    def test_parse_lcov_da_before_sf_ignored(self):
        self.assertEqual(parse_lcov("DA:1,1\nSF:a.py\nend_of_record\n"), {})

    def test_load_coverage_records_dispatches_info_suffix_to_lcov(self):
        with tempfile.TemporaryDirectory() as tmp:
            info = Path(tmp) / "coverage.info"
            info.write_text("SF:a.py\nDA:1,1\nDA:2,0\nend_of_record\n")
            cov = load_coverage_records(info)
        self.assertEqual(cov, {"a.py": FileCoverage(executed_lines={1}, missing_lines={2})})

    def test_load_coverage_records_missing_raises(self):
        with self.assertRaises(OSError):
            load_coverage_records("/nonexistent/path/coverage.info")


if __name__ == "__main__":
    unittest.main()
