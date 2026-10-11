"""Killing test for the core-mutation-run survivor on the threshold verdict.

The dogfood mutation run left ``format_report``'s ``max_crap > threshold``
boundary comparison as the sole report survivor: nothing pinned the tie case
(max == threshold must still pass — port of upstream crap4dart 862acc1's
threshold-tie killing test, found as this port's own survivor).
"""

import unittest

from crap4py.crap import MethodMetric
from crap4py.report import format_report


class ThresholdTieTest(unittest.TestCase):
    def test_max_crap_equal_to_threshold_passes(self):
        metric = MethodMetric("m", "a.py", 5, 0.0, 8.0)
        report = format_report([metric], 8.0)
        self.assertIn("Max CRAP: 8.0 (threshold 8.0) — passed", report)
        self.assertNotIn("FAILED", report)


if __name__ == "__main__":
    unittest.main()
