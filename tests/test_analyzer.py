"""Unit tests for the analyzer (parsing + complexity + coverage -> MethodMetric)."""

import shutil
import tempfile
import unittest
from pathlib import Path

from crap4py.analyzer import analyze, sort_metrics
from crap4py.crap import MethodMetric, crap_score

FIXTURES = Path(__file__).parent / "fixtures"


class AnalyzerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        shutil.copy(FIXTURES / "sample.py", self.root / "sample.py")
        shutil.copy(FIXTURES / "coverage.json", self.root / "coverage.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_analyze_fixture_metrics(self):
        metrics = analyze(
            [str(self.root / "sample.py")],
            str(self.root / "coverage.json"),
            str(self.root),
        )
        by_name = {m.method_name: m for m in metrics}

        self.assertIn("simple", by_name)
        self.assertEqual(by_name["simple"].complexity, 1)
        self.assertAlmostEqual(by_name["simple"].coverage, 1.0)
        self.assertAlmostEqual(by_name["simple"].crap_score, 1.0)
        self.assertEqual(by_name["simple"].file, "sample.py")

        self.assertEqual(by_name["branchy"].complexity, 3)
        self.assertAlmostEqual(by_name["branchy"].coverage, 4 / 6)
        self.assertAlmostEqual(by_name["branchy"].crap_score, crap_score(3, 4 / 6))

        self.assertEqual(by_name["risky"].complexity, 5)
        self.assertAlmostEqual(by_name["risky"].coverage, 0.0)
        self.assertAlmostEqual(by_name["risky"].crap_score, 30.0)

    def test_missing_coverage_yields_na(self):
        metrics = analyze(
            [str(self.root / "sample.py")],
            str(self.root / "does_not_exist.json"),
            str(self.root),
        )
        self.assertTrue(metrics)
        self.assertTrue(all(m.coverage is None for m in metrics))
        self.assertTrue(all(m.crap_score is None for m in metrics))

    def test_file_outside_root_uses_path_as_file_field(self):
        # analyze still works when the file is outside project_root; rel path falls back.
        metrics = analyze(
            [str(self.root / "sample.py")],
            str(self.root / "coverage.json"),
            "/definitely/not/the/root",
        )
        self.assertTrue(metrics)
        # file field is the resolved abs path (couldn't relativize)
        self.assertTrue(any(m.file.endswith("sample.py") for m in metrics))


class SortMetricsTest(unittest.TestCase):
    def _metrics(self):
        return [
            MethodMetric("low", "a.py", 1, 1.0, 1.0),
            MethodMetric("high", "b.py", 5, 0.0, 30.0),
            MethodMetric("unknown", "c.py", 2, None, None),
        ]

    def test_numeric_desc_na_last(self):
        ordered = [m.method_name for m in sort_metrics(self._metrics())]
        self.assertEqual(ordered, ["high", "low", "unknown"])

    def test_tie_break_by_file_then_name(self):
        metrics = [
            MethodMetric("b", "z.py", 2, 1.0, 4.0),
            MethodMetric("a", "z.py", 2, 1.0, 4.0),
            MethodMetric("c", "a.py", 2, 1.0, 4.0),
        ]
        ordered = [(m.file, m.method_name) for m in sort_metrics(metrics)]
        self.assertEqual(ordered, [("a.py", "c"), ("z.py", "a"), ("z.py", "b")])


if __name__ == "__main__":
    unittest.main()
