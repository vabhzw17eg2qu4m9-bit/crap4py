"""Killing tests for the core-mutation-run survivors on the value objects.

The first dogfood mutation run on the core (crap.py, report.py, analyzer.py,
coverage.py) left four survivors: flipping ``frozen=True`` or ``slots=True``
to ``False`` on ``MethodDescriptor``/``MethodMetric``. No other test observes
the value-object contract, so these pin it.
"""

import unittest
from dataclasses import FrozenInstanceError

from crap4py.crap import MethodDescriptor, MethodMetric


class ValueObjectContractTest(unittest.TestCase):
    def test_frozen_dataclasses_reject_reassignment(self):
        desc = MethodDescriptor(name="f", start_line=1, end_line=2, complexity=1)
        metric = MethodMetric("f", "a.py", 1, 1.0, 1.0)
        with self.assertRaises(FrozenInstanceError):
            desc.complexity = 2
        with self.assertRaises(FrozenInstanceError):
            metric.crap_score = 99.0

    def test_slotted_dataclasses_have_no_dict(self):
        desc = MethodDescriptor(name="f", start_line=1, end_line=2, complexity=1)
        metric = MethodMetric("f", "a.py", 1, 1.0, 1.0)
        with self.assertRaises(TypeError):
            vars(desc)
        with self.assertRaises(TypeError):
            vars(metric)


if __name__ == "__main__":
    unittest.main()
