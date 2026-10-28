"""Unit + CLI tests for the `identical-branches` subcommand.

Each family member has a killing test: the fixture genuinely triggers the
scanned AST shape and the assertions pin messages/counts/lines, so removing
or mutating one check fails its test. Canonical-form equivalence (sorting,
flattening, parenthesis-insensitivity) and the documented py adaptations
(``is``/``not in`` unsorted, duplicate except types legal, else-if is an
If-in-orelse chain link) have their own killing tests.
"""

import ast
import contextlib
import io
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from crap4py.identical_branches import (
    BranchFinding,
    IdenticalBranchesResult,
    check_files,
    file_violations,
    run,
    summary,
)


def _violations(source: str) -> IdenticalBranchesResult:
    source = textwrap.dedent(source)
    return file_violations(ast.parse(source), "x.py")


def _messages(result: IdenticalBranchesResult) -> list[str]:
    return [v.message for v in result.violations]


class DuplicateConditionTest(unittest.TestCase):
    def test_exact_duplicate_flagged_with_first_line(self):
        result = _violations(
            """
            def classify(x):
                if x < 0:
                    return "neg"
                elif x == 0:
                    return "zero"
                elif x == 0:
                    return "zero again"
                return "other"
            """
        )
        self.assertEqual(len(result.violations), 1)
        violation = result.violations[0]
        self.assertEqual(violation.line, 7)
        self.assertIn("duplicate condition in if/elif chain", violation.message)
        self.assertIn("'x == 0'", violation.message)
        self.assertIn("same as line 5", violation.message)
        self.assertEqual(summary(result), "1 identical-branch findings (1 constructs checked)")

    def test_two_repeats_produce_two_violations(self):
        result = _violations(
            """
            def classify(x):
                if x == 0:
                    return "a"
                elif x == 0:
                    return "b"
                elif x == 0:
                    return "c"
                return "d"
            """
        )
        self.assertEqual([v.line for v in result.violations], [5, 7])

    def test_commutative_reorder_and_parens_still_duplicate(self):
        result = _violations(
            """
            def check(a, b, x, y):
                if a == b and x:
                    return y
                elif x and (a == b):
                    return False
                return True
            """
        )
        self.assertEqual([v.line for v in result.violations], [5])

    def test_spacing_does_not_hide_a_duplicate(self):
        result = _violations(
            """
            def classify(x):
                if x==0:
                    return "a"
                elif x  ==  0:
                    return "b"
                return "c"
            """
        )
        self.assertEqual(len(result.violations), 1)


class ChainScopeTest(unittest.TestCase):
    def test_distinct_conditions_pass(self):
        result = _violations(
            """
            def classify(x):
                if x < 0:
                    return "neg"
                elif x == 0:
                    return "zero"
                elif x < 10:
                    return "small"
                return "other"
            """
        )
        self.assertEqual(result.violations, [])
        self.assertIn("no identical-branch findings", summary(result))

    def test_same_condition_in_separate_ifs_passes(self):
        result = _violations(
            """
            def classify(x):
                label = "other"
                if x == 0:
                    label = "zero"
                if x == 0:
                    label = "zero"
                return label
            """
        )
        self.assertEqual(result.violations, [])

    def test_nested_if_is_its_own_chain_not_a_link(self):
        result = _violations(
            """
            def classify(x, y):
                if x == 0:
                    if y == 0:
                        return "origin"
                    return "x-zero"
                elif y == 0:
                    return "y-zero"
                return "other"
            """
        )
        self.assertEqual(result.violations, [])

    def test_if_after_else_is_a_chain_link_by_construction(self):
        # Python has no elif AST node: else + if IS the chain link, so the
        # upstream "nested in an else block is its own chain" shape here
        # stays one chain and the duplicate still reports.
        result = _violations(
            """
            def classify(x):
                if x > 0:
                    return "pos"
                else:
                    if x == 0:
                        return "zero"
                    elif x == 0:
                        return "zero!"
                return "neg"
            """
        )
        self.assertEqual([v.line for v in result.violations], [8])


class CanonicalFormTest(unittest.TestCase):
    def test_directional_comparisons_never_reorder(self):
        result = _violations(
            """
            def classify(a, b):
                if a < b:
                    return "lt"
                elif b < a:
                    return "gt"
                elif a - b == 1:
                    return "next"
                elif b - a == 1:
                    return "prev"
                return "eq"
            """
        )
        self.assertEqual(result.violations, [])

    def test_in_operands_sort_per_contract(self):
        result = _violations(
            """
            def check(x, items):
                if x in items:
                    return "yes"
                elif items in x:
                    return "no"
                return "?"
            """
        )
        self.assertEqual([v.line for v in result.violations], [5])

    def test_unary_operands_canonicalize(self):
        result = _violations(
            """
            def f(flag):
                if not flag:
                    return 1
                elif not flag:
                    return 2
                return 3
            """
        )
        self.assertEqual([v.line for v in result.violations], [5])

    def test_is_operands_keep_order(self):
        result = _violations(
            """
            def check(a, b):
                if a is b:
                    return "yes"
                elif b is a:
                    return "no"
                return "?"
            """
        )
        self.assertEqual(result.violations, [])

    def test_minus_keeps_associativity(self):
        result = _violations(
            """
            def delta(a, b, c):
                if a - (b - c) == 1:
                    return "p"
                elif a - b - c == 1:
                    return "q"
                return "?"
            """
        )
        self.assertEqual(result.violations, [])

    def test_plus_chain_flattens_and_sorts(self):
        result = _violations(
            """
            def total(a, b, c):
                if a + b + c == 1:
                    return "x"
                elif c + (a + b) == 1:
                    return "y"
                return "?"
            """
        )
        self.assertEqual([v.line for v in result.violations], [5])

    def test_and_nests_flatten(self):
        result = _violations(
            """
            def decide(a, b, c):
                if a and (b and c):
                    return 1
                elif (c and a) and b:
                    return 2
                return 3
            """
        )
        self.assertEqual([v.line for v in result.violations], [5])


class DuplicateBodyTest(unittest.TestCase):
    def test_identical_then_bodies_across_chain(self):
        result = _violations(
            """
            def f(a, b):
                if a:
                    return "x"
                elif b:
                    return "x"
                return "y"
            """
        )
        self.assertEqual(len(result.violations), 1)
        violation = result.violations[0]
        self.assertEqual(violation.line, 6)
        self.assertIn("duplicate branch body", violation.message)
        self.assertIn("same as line 4", violation.message)

    def test_else_body_equal_to_then_body(self):
        result = _violations(
            """
            def f(a):
                if a:
                    return "x"
                else:
                    return "x"
                return "y"
            """
        )
        self.assertEqual(len(result.violations), 1)
        self.assertIn("duplicate branch body", result.violations[0].message)

    def test_identical_bodies_in_separate_ifs_legal(self):
        result = _violations(
            """
            def f(a, b):
                if a:
                    return "x"
                if b:
                    return "x"
                return "y"
            """
        )
        self.assertEqual(result.violations, [])


class DuplicateOperandTest(unittest.TestCase):
    def test_or_chain_repeated_operand(self):
        result = _violations(
            """
            def f(a, b):
                return a or a or b
            """
        )
        self.assertEqual(len(result.violations), 1)
        self.assertIn("duplicate operand in 'or' chain", result.violations[0].message)
        self.assertIn("'a' repeats", result.violations[0].message)

    def test_and_chain_repeated_operand(self):
        result = _violations(
            """
            def f(a):
                return a and a
            """
        )
        self.assertEqual(len(result.violations), 1)

    def test_repeated_call_operand(self):
        result = _violations(
            """
            def f():
                return ready() or ready()
            """
        )
        self.assertEqual(len(result.violations), 1)

    def test_distinct_operands_legal(self):
        result = _violations(
            """
            def f(a, b, c):
                return a or b or c
            """
        )
        self.assertEqual(result.violations, [])

    def test_only_outermost_link_reports(self):
        result = _violations(
            """
            def f(a, b):
                return a or (a or b)
            """
        )
        self.assertEqual(len(result.violations), 1)

    def test_absorbed_operand_flagged(self):
        result = _violations(
            """
            def f(a, b):
                return a and (b or a)
            """
        )
        self.assertEqual(len(result.violations), 1)
        self.assertIn("absorption", result.violations[0].message)

    def test_nested_different_operator_chain_clean(self):
        result = _violations(
            """
            def f(a, b, c):
                return a and (b or c)
            """
        )
        self.assertEqual(result.violations, [])

    def test_reordered_commutative_operands_collapse(self):
        result = _violations(
            """
            def f(a, b):
                return (a and b) or (b and a)
            """
        )
        self.assertEqual(len(result.violations), 1)


class TernaryTest(unittest.TestCase):
    def test_identical_ternary_branches(self):
        result = _violations(
            """
            def f(c, x):
                return x if c else x
            """
        )
        self.assertEqual(len(result.violations), 1)
        self.assertIn(
            "identical then/else branches of conditional expression",
            result.violations[0].message,
        )

    def test_different_ternary_branches_legal(self):
        result = _violations(
            """
            def f(c, x, y):
                return x if c else y
            """
        )
        self.assertEqual(result.violations, [])

    def test_identical_nested_ternary_branches(self):
        result = _violations(
            """
            def f(c, d, x, y):
                return (x if d else y) if c else (x if d else y)
            """
        )
        self.assertEqual(len(result.violations), 1)


class DuplicateArgumentTest(unittest.TestCase):
    def test_identifier_repeat_flagged(self):
        result = _violations(
            """
            def f(width, height):
                return measure(width, height, width)
            """
        )
        self.assertEqual(len(result.violations), 1)
        message = result.violations[0].message
        self.assertIn("duplicate argument in call to 'measure'", message)
        self.assertIn("same as argument 1", message)

    def test_keyword_repeats_are_self_documenting(self):
        result = _violations(
            """
            def f(x, y):
                return box(a=x, b=y, c=x)
            """
        )
        self.assertEqual(result.violations, [])

    def test_all_identical_arguments_are_uniform(self):
        result = _violations(
            """
            def f(x):
                return box(a=x, b=x, c=x)
            """
        )
        self.assertEqual(result.violations, [])

    def test_literal_repeats_exempt(self):
        result = _violations(
            """
            def f():
                return edges(8, 8, 8, 4)
            """
        )
        self.assertEqual(result.violations, [])

    def test_literal_never_the_repeat(self):
        result = _violations(
            """
            def f(width):
                return edges(8, 8, width)
            """
        )
        self.assertEqual(result.violations, [])

    def test_star_star_expansion_is_an_unnamed_slot(self):
        result = _violations(
            """
            def f(opts):
                return combine(**opts, **opts)
            """
        )
        self.assertEqual(len(result.violations), 1)

    def test_attribute_callee_uses_method_name(self):
        result = _violations(
            """
            def f(width, height):
                return grid.measure(width, height, width)
            """
        )
        self.assertEqual(len(result.violations), 1)
        self.assertIn("call to 'measure'", result.violations[0].message)

    def test_lambda_callee_renders_as_lambda(self):
        result = _violations(
            """
            def f(w):
                return (lambda x: x)(w, w)
            """
        )
        self.assertEqual(len(result.violations), 1)

    def test_negative_number_is_a_unary_not_a_literal(self):
        # Mirrors upstream: -8 is a unary expression, not a literal, so a
        # repeated -8 positional is a name-like repeat, not an exempt one.
        result = _violations(
            """
            def f(width):
                return edges(-8, -8, width)
            """
        )
        self.assertEqual(len(result.violations), 1)


class DuplicateExceptBodyTest(unittest.TestCase):
    def test_identical_except_bodies(self):
        result = _violations(
            """
            def f():
                try:
                    run()
                except ValueError:
                    log("bad")
                except KeyError:
                    log("bad")
                return None
            """
        )
        self.assertEqual(len(result.violations), 1)
        self.assertIn("duplicate except body in try statement", result.violations[0].message)
        self.assertIn("same as line 6", result.violations[0].message)

    def test_different_except_bodies_legal(self):
        result = _violations(
            """
            def f():
                try:
                    run()
                except ValueError:
                    log("value")
                except KeyError:
                    log("key")
                return None
            """
        )
        self.assertEqual(result.violations, [])

    def test_exception_group_bodies_compared_too(self):
        result = _violations(
            """
            def f():
                try:
                    run()
                except* ValueError:
                    log("bad")
                except* KeyError:
                    log("bad")
                return None
            """
        )
        self.assertEqual(len(result.violations), 1)
        self.assertIn("duplicate except body in try statement", result.violations[0].message)

    def test_bare_except_equal_to_handler_body(self):
        result = _violations(
            """
            def f():
                try:
                    run()
                except ValueError:
                    log("bad")
                except:
                    log("bad")
                return None
            """
        )
        self.assertEqual(len(result.violations), 1)

    def test_duplicate_types_with_distinct_bodies_legal(self):
        # Python dispatches per type; types are semantically distinct, so
        # only bodies are compared (documented host adaptation).
        result = _violations(
            """
            def f():
                try:
                    run()
                except ValueError:
                    log("first")
                except ValueError:
                    log("second")
                return None
            """
        )
        self.assertEqual(result.violations, [])


class CheckFilesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_broken_file_skipped_and_test_file_excluded(self):
        (self.root / "broken.py").write_text("def f(:\n", encoding="utf-8")
        (self.root / "test_x.py").write_text("x = a or a\n", encoding="utf-8")
        (self.root / "app.py").write_text("x = a or a\n", encoding="utf-8")
        result = check_files(
            [self.root / n for n in ("broken.py", "test_x.py", "app.py")], self.root
        )
        self.assertEqual(result.constructs, 1)
        self.assertEqual(len(result.violations), 1)


class RunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_no_files_exits_0(self):
        self.assertEqual(run([], self.root), 0)

    def test_warning_severity_default_reports_without_failing(self):
        (self.root / "app.py").write_text("def f(a):\n    return a or a\n", encoding="utf-8")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = run([], self.root)
        self.assertEqual(code, 0)
        self.assertIn("[WARN] app.py:2:", buffer.getvalue())
        self.assertIn("1 identical-branch findings", buffer.getvalue())

    def test_error_severity_exits_2(self):
        (self.root / "app.py").write_text("def f(a):\n    return a or a\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            code = run(["--severity", "error"], self.root)
        self.assertEqual(code, 2)

    def test_ignore_marker_suppresses(self):
        (self.root / "app.py").write_text(
            "def f(a):\n    return a or a  # ignore:identical_branches\n", encoding="utf-8"
        )
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = run([], self.root)
        self.assertEqual(code, 0)
        self.assertIn("no identical-branch findings", buffer.getvalue())

    def test_test_file_not_scanned(self):
        (self.root / "test_dup.py").write_text("def f(a):\n    return a or a\n", encoding="utf-8")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = run([], self.root)
        self.assertEqual(code, 0)
        self.assertNotIn("[WARN]", buffer.getvalue())


class SummaryTest(unittest.TestCase):
    def test_summary(self):
        clean = summary(IdenticalBranchesResult([], 3))
        self.assertEqual(clean, "3 constructs checked, no identical-branch findings")
        found = IdenticalBranchesResult([BranchFinding("a.py", 1, "m")], 3)
        self.assertEqual(summary(found), "1 identical-branch findings (3 constructs checked)")


class CliSubprocessTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "crap4py", *args],
            cwd=str(self.root),
            capture_output=True,
            text=True,
        )

    def test_bad_flag_exits_1(self):
        r = self._run("identical-branches", "--bogus")
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_warning_output_and_exit_0(self):
        (self.root / "app.py").write_text("def f(a):\n    return a or a\n", encoding="utf-8")
        r = self._run("identical-branches")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[WARN] app.py:2: duplicate operand in 'or' chain", r.stdout)

    def test_dispatched_from_main_cli(self):
        r = self._run("identical-branches", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("crap4py identical-branches", r.stdout)


if __name__ == "__main__":
    unittest.main()
