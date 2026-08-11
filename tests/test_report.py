"""Unit tests for the tabular report formatter."""

import unittest

from crap4py.crap import MethodMetric
from crap4py.report import format_report


def _metric(name, file, cc, cov, crap):
    return MethodMetric(name, file, cc, cov, crap)


class ReportFormatTest(unittest.TestCase):
    def test_header_lines(self):
        report = format_report([_metric("m", "a.py", 1, 1.0, 1.0)], 8.0)
        lines = report.splitlines()
        self.assertEqual(lines[0], "CRAP Report")
        self.assertEqual(lines[1], "===========")
        self.assertIn("Method", lines[2])
        self.assertIn("File", lines[2])
        self.assertIn("CC", lines[2])
        self.assertIn("Cov%", lines[2])
        self.assertIn("CRAP", lines[2])

    def test_separator_is_dashes(self):
        report = format_report([_metric("m", "a.py", 1, 1.0, 1.0)], 8.0)
        sep_line = report.splitlines()[3]
        self.assertTrue(set(sep_line) == {"-"}, f"separator must be all dashes: {sep_line!r}")
        self.assertTrue(len(sep_line) >= 30)

    def test_sort_numeric_desc_na_last(self):
        metrics = [
            _metric("low", "a.py", 1, 1.0, 1.0),
            _metric("high", "b.py", 5, 0.0, 30.0),
            _metric("unknown", "c.py", 2, None, None),
        ]
        report = format_report(metrics, 8.0)
        rows = [line for line in report.splitlines() if line.startswith(("low", "high", "unknown"))]
        self.assertEqual([r.split()[0] for r in rows], ["high", "low", "unknown"])

    def test_coverage_and_crap_formatting(self):
        report = format_report([_metric("m", "a.py", 5, 0.45, 18.6)], 8.0)
        self.assertIn("45.0%", report)
        self.assertIn("18.6", report)

    def test_na_formatting(self):
        report = format_report([_metric("m", "a.py", 2, None, None)], 8.0)
        self.assertIn("N/A", report)

    def test_summary_failed(self):
        report = format_report(
            [_metric("high", "b.py", 5, 0.0, 30.0), _metric("low", "a.py", 1, 1.0, 1.0)],
            8.0,
        )
        self.assertIn("Max CRAP: 30.0 (threshold 8.0) — FAILED", report)

    def test_summary_passed(self):
        report = format_report([_metric("low", "a.py", 1, 1.0, 1.0)], 8.0)
        self.assertIn("Max CRAP: 1.0 (threshold 8.0) — passed", report)

    def test_summary_all_na_treats_max_as_zero(self):
        report = format_report([_metric("m", "a.py", 2, None, None)], 8.0)
        self.assertIn("Max CRAP: 0.0 (threshold 8.0) — passed", report)


if __name__ == "__main__":
    unittest.main()
