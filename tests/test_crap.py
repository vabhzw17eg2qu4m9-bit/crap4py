"""Unit tests for the CRAP formula (shared-contract.md §1 edge cases)."""

import unittest

from crap4py.crap import crap_score


class CrapScoreTest(unittest.TestCase):
    def test_full_coverage(self):
        self.assertAlmostEqual(crap_score(5, 1.0), 5.0)

    def test_zero_coverage(self):
        self.assertAlmostEqual(crap_score(5, 0.0), 30.0)

    def test_partial_coverage(self):
        # 18.648 within 0.01
        self.assertAlmostEqual(crap_score(8, 0.45), 18.648, places=2)

    def test_null_coverage_returns_none(self):
        self.assertIsNone(crap_score(3, None))


if __name__ == "__main__":
    unittest.main()
