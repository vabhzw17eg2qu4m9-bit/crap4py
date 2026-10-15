"""Unit tests for cognitive complexity counting (SonarSource v1.2, port of
the crap4dart ``CognitiveCalculator``)."""

import ast
import unittest

from crap4py.cognitive import cognitive_complexity


def _cog(src: str, name: str) -> int:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return cognitive_complexity(node)
    raise AssertionError(f"function {name} not found")


class CognitiveBasicTest(unittest.TestCase):
    def test_linear_code_scores_zero(self):
        self.assertEqual(_cog("def add(a, b):\n    return a + b\n", "add"), 0)

    def test_flat_if_scores_one(self):
        self.assertEqual(_cog("def f(x):\n    if x > 0:\n        return 1\n    return 0\n", "f"), 1)

    def test_flat_elif_chain_scores_one_per_if(self):
        src = (
            "def classify(x):\n"
            "    if x < 0:\n"
            "        return 'neg'\n"
            "    elif x == 0:\n"
            "        return 'zero'\n"
            "    elif x < 10:\n"
            "        return 'small'\n"
            "    return 'other'\n"
        )
        self.assertEqual(_cog(src, "classify"), 3)

    def test_if_after_other_statements_in_else_pays_nesting(self):
        src = (
            "def classify(x):\n"
            "    if x < 0:\n"
            "        return 'neg'\n"
            "    else:\n"
            "        tag = 'other'\n"
            "        if x == 0:\n"
            "            tag = 'zero'\n"
            "    return tag\n"
        )
        # if +1, else +1, nested if +2
        self.assertEqual(_cog(src, "classify"), 4)

    def test_if_inside_loop_pays_nesting(self):
        src = (
            "def f(items):\n"
            "    total = 0\n"
            "    for i in items:\n"
            "        if i % 2 == 0:\n"
            "            total += i\n"
            "    return total\n"
        )
        # for +1, if +1 + nesting 1
        self.assertEqual(_cog(src, "f"), 3)

    def test_while_with_and_condition(self):
        src = "def f(a, b):\n    while a and b:\n        a -= 1\n    return a\n"
        # while +1, and-sequence +1
        self.assertEqual(_cog(src, "f"), 2)

    def test_except_handler_increments(self):
        src = "def f():\n    try:\n        return 1\n    except ValueError:\n        return 0\n"
        self.assertEqual(_cog(src, "f"), 1)

    def test_match_cases_each_increment(self):
        src = (
            "def f(x):\n"
            "    match x:\n"
            "        case 1:\n"
            "            return 'a'\n"
            "        case _:\n"
            "            return 'b'\n"
        )
        self.assertEqual(_cog(src, "f"), 2)

    def test_match_case_guard_sequence_counts_at_case_depth(self):
        src = (
            "def f(x, y):\n"
            "    match x:\n"
            "        case _ if y and x:\n"
            "            return 1\n"
            "        case _:\n"
            "            return 0\n"
        )
        # case +1 each, and-sequence +1 (flat — sequences never pay nesting)
        self.assertEqual(_cog(src, "f"), 3)

    def test_loop_else_nests_without_own_increment(self):
        src = (
            "def f(xs, y):\n"
            "    for x in xs:\n"
            "        if x:\n"
            "            return x\n"
            "    else:\n"
            "        if y:\n"
            "            return y\n"
            "    return 0\n"
        )
        # for +1, if +2 (nesting), else-if +2 (nested like the loop body)
        self.assertEqual(_cog(src, "f"), 5)

    def test_nested_def_skipped(self):
        src = "def outer():\n    def inner(x):\n        if x:\n            return 1\n    return 0\n"
        self.assertEqual(_cog(src, "outer"), 0)

    def test_def_inside_if_skipped_from_enclosing(self):
        src = (
            "def outer(x):\n"
            "    if x:\n"
            "        def inner(y):\n"
            "            if y:\n"
            "                return 1\n"
            "    return 0\n"
        )
        # if +1; inner's branches do not count toward outer
        self.assertEqual(_cog(src, "outer"), 1)


class CognitiveSequenceTest(unittest.TestCase):
    def test_same_operator_run_counts_once(self):
        self.assertEqual(_cog("def f(a, b, c):\n    return a and b and c\n", "f"), 1)
        self.assertEqual(_cog("def f(a, b, c):\n    return a or b or c\n", "f"), 1)

    def test_operator_change_starts_new_sequence(self):
        self.assertEqual(_cog("def f(a, b, c):\n    return a and b or c\n", "f"), 2)

    def test_parentheses_do_not_reset_same_operator_sequence(self):
        self.assertEqual(_cog("def f(a, b, c):\n    return (a and b) and c\n", "f"), 1)

    def test_sequences_are_flat(self):
        src = (
            "def f(xs, a, b):\n"
            "    for x in xs:\n"
            "        if a and b:\n"
            "            return x\n"
            "    return 0\n"
        )
        # for +1, if +2 (nesting), and-sequence +1 (flat, no nesting)
        self.assertEqual(_cog(src, "f"), 4)


class CognitiveNestedUnitTest(unittest.TestCase):
    def test_nested_ternary_pays_nesting_on_inner_branch(self):
        self.assertEqual(_cog("def f(x):\n    return 1 if x > 0 else -1 if x < 0 else 0\n", "f"), 3)

    def test_lambda_body_deepens_without_increment(self):
        self.assertEqual(
            _cog("def f(xs):\n    return list(filter(lambda x: x if x else 0, xs))\n", "f"), 2
        )

    def test_comprehension_body_deepens_without_increment(self):
        self.assertEqual(_cog("def f(xs):\n    return [x for x in xs if x]\n", "f"), 0)
        # a flow break inside the comprehension counts at the deeper level
        self.assertEqual(_cog("def f(xs, a, b):\n    return [x for x in xs if a and b]\n", "f"), 1)


if __name__ == "__main__":
    unittest.main()
