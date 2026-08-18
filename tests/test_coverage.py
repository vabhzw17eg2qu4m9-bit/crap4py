"""Unit tests for coverage.py JSON parsing and method attribution."""

import io
import sys
import unittest

from crap4py.coverage import coverage_for_method, load_coverage, parse_coverage_dict


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

    def test_load_missing_file_warns_and_returns_empty(self):
        old_err = sys.stderr
        sys.stderr = io.StringIO()
        try:
            result = load_coverage("/nonexistent/path/coverage.json")
        finally:
            captured = sys.stderr.getvalue()
            sys.stderr = old_err
        self.assertEqual(result, {})
        self.assertIn("not found", captured)
        self.assertIn("Hint: generate coverage first", captured)
        self.assertIn("coverage run -m pytest && coverage json", captured)


if __name__ == "__main__":
    unittest.main()
