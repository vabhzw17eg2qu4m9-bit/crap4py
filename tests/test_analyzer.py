"""Unit tests for the analyzer (parsing + complexity + coverage -> MethodMetric)."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crap4py.analyzer import _relative_to_root, analyze, sort_metrics
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


class RelativeToRootTest(unittest.TestCase):
    """A caller may pass a root that is not fully resolved (a symlinked path).

    Both sides have to be resolved before comparing, otherwise the relative
    form is silently abandoned and an absolute path leaks into the report's
    file column.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name).resolve()
        self.real = base / "real"
        self.real.mkdir()
        (self.real / "mod.py").write_text("def f():\n    return 1\n")

    def _symlinked_root(self) -> Path:
        """A second spelling of ``self.real``, or skip where that is impossible."""
        link = Path(self._tmp.name).resolve() / "link"
        try:
            link.symlink_to(self.real, target_is_directory=True)
        except (OSError, NotImplementedError):  # pragma: no cover - platform guard
            self.skipTest("symlinks unavailable on this platform")
        return link

    def test_unresolved_root_still_yields_relative_path(self):
        link = self._symlinked_root()
        self.assertEqual(_relative_to_root(self.real / "mod.py", link), "mod.py")

    def test_resolved_root_is_unchanged(self):
        self.assertEqual(_relative_to_root(self.real / "mod.py", self.real), "mod.py")

    def _failing_resolve(self, target: Path, error: Exception):
        """Patch ``Path.resolve`` so it raises for ``target`` and works elsewhere."""
        original = Path.resolve

        def resolve(path_self, *args, **kwargs):
            if path_self == target:
                raise error
            return original(path_self, *args, **kwargs)

        return mock.patch.object(Path, "resolve", resolve)

    def test_unresolvable_root_is_compared_as_given(self):
        """Root resolution can fail; the root is then used as handed over."""
        for error in (OSError("simulated"), RuntimeError("symlink loop")):
            with self.subTest(error=type(error).__name__):
                with self._failing_resolve(self.real, error):
                    self.assertEqual(_relative_to_root(self.real / "mod.py", self.real), "mod.py")

    def test_file_resolution_failure_still_propagates(self):
        """Only the root gained a guard; the file keeps its former semantics."""
        target = self.real / "mod.py"
        with self._failing_resolve(target, OSError("simulated")):
            with self.assertRaises(OSError):
                _relative_to_root(target, self.real)

    def test_path_outside_root_falls_back_to_the_given_path(self):
        outside = Path(self._tmp.name).resolve() / "outside.py"
        outside.write_text("x = 1\n")
        self.assertEqual(_relative_to_root(outside, self.real), str(outside))


if __name__ == "__main__":
    unittest.main()
