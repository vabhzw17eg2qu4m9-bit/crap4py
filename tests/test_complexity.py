"""Unit tests for AST cyclomatic-complexity counting (shared-contract.md §2)."""

import unittest

from crap4py.complexity import extract_methods


def _cc(src: str, name: str) -> int:
    methods = {m.name: m.complexity for m in extract_methods(src)}
    return methods[name]


class ComplexityTest(unittest.TestCase):
    def test_base_complexity_is_one(self):
        self.assertEqual(_cc("def f():\n    pass\n", "f"), 1)

    def test_if(self):
        self.assertEqual(_cc("def f(x):\n    if x:\n        return 1\n", "f"), 2)

    def test_for(self):
        self.assertEqual(_cc("def f(xs):\n    for x in xs:\n        pass\n", "f"), 2)

    def test_while(self):
        self.assertEqual(_cc("def f(n):\n    while n:\n        n -= 1\n", "f"), 2)

    def test_except_handler(self):
        src = "def f():\n    try:\n        pass\n    except ValueError:\n        pass\n"
        self.assertEqual(_cc(src, "f"), 2)

    def test_ifexp_ternary(self):
        self.assertEqual(_cc("def f(x):\n    return 1 if x else 0\n", "f"), 2)

    def test_boolop_n_minus_one(self):
        # a and b and c -> 3 values -> +2
        self.assertEqual(_cc("def f(a, b, c):\n    return a and b and c\n", "f"), 3)

    def test_comprehension_counts_each_for_clause(self):
        src = "def f(xs, ys):\n    return [x for x in xs for y in ys]\n"
        self.assertEqual(_cc(src, "f"), 3)  # base 1 + 2 generators

    def test_dict_comprehension(self):
        src = "def f(xs):\n    return {x: x for x in xs}\n"
        self.assertEqual(_cc(src, "f"), 2)

    def test_match_case(self):
        src = (
            "def f(x):\n"
            "    match x:\n"
            "        case 1:\n"
            "            return 'a'\n"
            "        case 2:\n"
            "            return 'b'\n"
        )
        self.assertEqual(_cc(src, "f"), 3)  # base 1 + 2 cases

    def test_async_for(self):
        src = "async def f(xs):\n    async for x in xs:\n        pass\n"
        self.assertEqual(_cc(src, "f"), 2)

    def test_combined_function(self):
        src = (
            "def combined(x, xs):\n"
            "    if x > 0:\n"
            "        for i in xs:\n"
            "            if i:\n"
            "                pass\n"
            "    return x and True\n"
        )
        # base 1 + if + for + if + (2-1 boolop) = 5
        self.assertEqual(_cc(src, "combined"), 5)

    def test_nested_def_not_counted_in_parent(self):
        src = "def outer():\n    def inner():\n        if True:\n            pass\n    return 0\n"
        methods = {m.name: m.complexity for m in extract_methods(src)}
        self.assertEqual(methods["outer"], 1)  # inner's if doesn't count toward parent
        self.assertEqual(methods["outer.inner"], 2)

    def test_lambda_counts_toward_enclosing(self):
        src = "def f(xs):\n    return list(filter(lambda x: x if x else 0, xs))\n"
        # ternary inside lambda counts toward f
        self.assertEqual(_cc(src, "f"), 2)

    def test_class_method_naming_includes_init(self):
        src = (
            "class Foo:\n"
            "    def __init__(self):\n"
            "        pass\n"
            "    def bar(self):\n"
            "        if True:\n"
            "            return 1\n"
        )
        methods = {m.name: m.complexity for m in extract_methods(src)}
        self.assertIn("Foo.__init__", methods)
        self.assertEqual(methods["Foo.__init__"], 1)
        self.assertEqual(methods["Foo.bar"], 2)

    def test_nested_function_naming(self):
        src = "def outer():\n    def inner():\n        pass\n    return inner\n"
        names = {m.name for m in extract_methods(src)}
        self.assertEqual(names, {"outer", "outer.inner"})

    def test_start_and_end_lines(self):
        src = "def f():\n    a = 1\n    b = 2\n    return a + b\n"
        m = extract_methods(src)[0]
        self.assertEqual(m.start_line, 1)
        self.assertEqual(m.end_line, 4)


if __name__ == "__main__":
    unittest.main()
