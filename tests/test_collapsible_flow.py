"""Unit + CLI tests for the `collapsible-flow` subcommand.

Each flagged-shape test kills its check: the fixtures genuinely carry the
AST shapes (nested-if merges, always-exiting then-suites, elif chains) and
the assertions pin messages/counts/lines, so removing or mutating one check
fails its test. The fall-through fixtures pin the hard-learned upstream
rule: an inner `if` WITHOUT an else never counts as exiting.
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

from crap4py.collapsible_flow import (
    CollapsibleFlowResult,
    check_files,
    file_violations,
    run,
    summary,
)


def _violations(source: str) -> list[tuple[int, str]]:
    source = textwrap.dedent(source).lstrip("\n")
    found = file_violations(ast.parse(source), "x.py")
    return [(v.line, v.message) for v in found.violations]


class CollapsibleIfTest(unittest.TestCase):
    def test_nested_if_in_suites_flagged(self):
        ((line, msg),) = _violations(
            """
            def f(a, b):
                if a:
                    if b:
                        print('x')
            """
        )
        self.assertEqual(line, 2)
        self.assertIn("merged into a single condition with `and`", msg)

    def test_inner_suite_on_the_inner_if_line_flagged(self):
        # Python has no braces: the unbraced upstream shape maps to a
        # one-statement inner suite, same AST either way.
        ((line, _),) = _violations(
            """
            def f(a, b):
                if a:
                    if b: print('x')
            """
        )
        self.assertEqual(line, 2)

    def test_summary_counts_constructs(self):
        source = textwrap.dedent(
            """
            def f(a, b):
                if a:
                    if b:
                        print('x')
            """
        )
        result = file_violations(ast.parse(source), "x.py")
        self.assertEqual(summary(result), "1 collapsible-flow findings (2 if statements checked)")
        self.assertEqual(
            summary(CollapsibleFlowResult([], 3)), "3 if statements checked, no collapsible flow"
        )


class NoMergeTest(unittest.TestCase):
    def test_inner_else_blocks_merge(self):
        self.assertEqual(
            _violations(
                """
                def f(a, b):
                    if a:
                        if b:
                            print('x')
                        else:
                            print('y')
                """
            ),
            [],
        )

    def test_inner_elif_blocks_merge(self):
        # An elif chain on the inner if is an orelse — never merged.
        self.assertEqual(
            _violations(
                """
                def f(a, b):
                    if a:
                        if b:
                            print('x')
                        elif b:
                            print('y')
                """
            ),
            [],
        )

    def test_outer_else_blocks_merge(self):
        self.assertEqual(
            _violations(
                """
                def f(a, b):
                    if a:
                        if b:
                            print('x')
                    else:
                        print('z')
                """
            ),
            [],
        )

    def test_extra_statement_before_inner_if_blocks_merge(self):
        self.assertEqual(
            _violations(
                """
                def f(a, b):
                    if a:
                        print('before')
                        if b:
                            print('x')
                """
            ),
            [],
        )

    def test_extra_statement_after_inner_if_blocks_merge(self):
        self.assertEqual(
            _violations(
                """
                def f(a, b):
                    if a:
                        if b:
                            print('x')
                        print('after')
                """
            ),
            [],
        )


class RedundantElseTest(unittest.TestCase):
    def test_redundant_else_after_returning_then(self):
        ((line, msg),) = _violations(
            """
            def f(c):
                if c:
                    return 'a'
                else:
                    return 'b'
            """
        )
        self.assertEqual(line, 2)
        self.assertIn("redundant else after an always-exiting then branch", msg)
        self.assertIn("dedent", msg)

    def test_break_continue_raise_and_bare_raise_count_as_exiting(self):
        cases = [
            (
                """
                def f(items, c):
                    for item in items:
                        if c:
                            break
                        else:
                            print(item)
                """,
                3,
            ),
            (
                """
                def f(items, c):
                    for item in items:
                        if c:
                            continue
                        else:
                            print(item)
                """,
                3,
            ),
            (
                """
                def f(c):
                    if c:
                        raise ValueError('x')
                    else:
                        print('x')
                """,
                2,
            ),
            (
                # bare raise is Python's rethrow, same node as `raise X`.
                """
                def wrap(work, c):
                    try:
                        return work()
                    except OSError:
                        if c:
                            raise
                        else:
                            log('handled')
                """,
                5,
            ),
        ]
        for source, line in cases:
            with self.subTest(line=line):
                [(found, msg)] = _violations(source)
                self.assertEqual(found, line)
                self.assertIn("dedent", msg)

    def test_block_ending_in_exiting_if_chain_flags_both_levels(self):
        found = _violations(
            """
            def f(c, x):
                if x > 0:
                    if c:
                        return 'a'
                    else:
                        return 'b'
                else:
                    return 'c'
            """
        )
        self.assertEqual([line for line, _ in found], [2, 3])
        self.assertTrue(all("dedent" in msg for _, msg in found))


class FallthroughTest(unittest.TestCase):
    def test_plain_then_statement_never_exits(self):
        self.assertEqual(
            _violations(
                """
                def f(c):
                    if c:
                        print('work')
                    else:
                        return 'b'
                    return 'c'
                """
            ),
            [],
        )

    def test_inner_bare_if_blocks_the_flag(self):
        # The source_instrumenter trap: the then-suite ends with an if
        # WITHOUT else — control falls through when its condition is
        # false, so dedenting the else would change behavior.
        self.assertEqual(
            _violations(
                """
                def f(node):
                    if isinstance(node, str):
                        s = node
                        if not s:
                            return ''
                    else:
                        return 'other'
                    return 'x'
                """
            ),
            [],
        )


class TryShapesTest(unittest.TestCase):
    def test_body_exits_no_final(self):
        [(line, msg)] = _violations(
            """
            def f(c):
                if c:
                    try:
                        return 'a'
                    except OSError:
                        raise
                else:
                    return 'b'
            """
        )
        self.assertEqual(line, 2)
        self.assertIn("dedent", msg)

    def test_body_exits_pass_only_final_is_neutral(self):
        # Python cannot write an empty suite: `finally: pass` is the
        # empty-finally shape and keeps the flag.
        [(line, _)] = _violations(
            """
            def f(c):
                if c:
                    try:
                        return 'a'
                    finally:
                        pass
                else:
                    return 'b'
            """
        )
        self.assertEqual(line, 2)

    def test_body_exits_and_final_exits(self):
        [(line, _)] = _violations(
            """
            def f(c):
                if c:
                    try:
                        return 'a'
                    finally:
                        return 'z'
                else:
                    return 'b'
            """
        )
        self.assertEqual(line, 2)

    def test_non_exiting_final_keeps_the_else(self):
        self.assertEqual(
            _violations(
                """
                def f(c):
                    if c:
                        try:
                            return 'a'
                        finally:
                            log('done')
                    else:
                        return 'b'
                    return 'x'
                """
            ),
            [],
        )

    def test_non_exiting_body_blocks_the_flag(self):
        self.assertEqual(
            _violations(
                """
                def f(c):
                    if c:
                        try:
                            log('a')
                        finally:
                            return 'z'
                    else:
                        return 'b'
                    return 'x'
                """
            ),
            [],
        )

    def test_swallowing_handler_not_analyzed_like_upstream(self):
        # As upstream, only the body and finally decide: a swallowing
        # except handler does not block the flag.
        [(line, _)] = _violations(
            """
            def f(c):
                if c:
                    try:
                        return 'a'
                    except OSError:
                        log('swallowed')
                else:
                    return 'b'
            """
        )
        self.assertEqual(line, 2)


class ChainHintTest(unittest.TestCase):
    def test_elif_after_terminal_then_is_a_hint(self):
        ((line, msg),) = _violations(
            """
            def f(x):
                if x < 0:
                    return 'neg'
                elif x == 0:
                    return 'zero'
                return 'pos'
            """
        )
        self.assertEqual(line, 4)
        self.assertIn("else-if follows an always-exiting then branch", msg)
        self.assertIn("flatten", msg)

    def test_plain_else_after_terminal_then_is_not_the_chain(self):
        # The else-suite holds an if PLUS a trailing statement: a plain
        # else, so the redundant-else message fires at the head, not a
        # chain hint at the inner if.
        ((line, msg),) = _violations(
            """
            def f(c):
                if c:
                    return 'a'
                else:
                    if c:
                        return 'x'
                    return 'y'
            """
        )
        self.assertEqual(line, 2)
        self.assertIn("dedent", msg)

    def test_canonical_code_produces_no_findings(self):
        source = textwrap.dedent(
            """
            def f(x, c):
                if x < 0:
                    return 'neg'
                if c:
                    print('a')
                    if x == 0:
                        print('b')
                return 'done'
            """
        )
        result = file_violations(ast.parse(source), "x.py")
        self.assertEqual(result.violations, [])
        self.assertIn("no collapsible flow", summary(result))

    def test_violations_carry_file_and_line(self):
        # The file:line pair is what diff-mode and ignore filtering key on.
        source = textwrap.dedent(
            """
            def f(a, b):
                if a:
                    if b:
                        print('x')
            """
        ).lstrip("\n")
        [finding] = file_violations(ast.parse(source), "pkg/mod.py").violations
        self.assertEqual(finding.file, "pkg/mod.py")
        self.assertEqual(finding.line, 2)


class MatchTest(unittest.TestCase):
    def test_then_suite_ending_in_match_never_exits(self):
        # match/case is not exit-analyzed (upstream: switch bodies).
        self.assertEqual(
            _violations(
                """
                def f(c, value):
                    if c:
                        match value:
                            case 0:
                                return 'zero'
                    else:
                        return 'b'
                """
            ),
            [],
        )

    def test_ifs_inside_case_bodies_are_still_scanned(self):
        # Not analyzed for exits ≠ not visited: a collapsible if inside a
        # case body is still flagged.
        ((line, msg),) = _violations(
            """
            def f(value):
                match value:
                    case 0:
                        if value:
                            if value:
                                print('x')
            """
        )
        self.assertEqual(line, 4)
        self.assertIn("`and`", msg)


class CheckFilesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_broken_file_skipped_and_test_file_excluded(self):
        (self.root / "broken.py").write_text("def f(:\n", encoding="utf-8")
        (self.root / "test_x.py").write_text("if a:\n    if b:\n        pass\n", encoding="utf-8")
        (self.root / "app.py").write_text("if a:\n    if b:\n        pass\n", encoding="utf-8")
        result = check_files(
            [self.root / n for n in ("broken.py", "test_x.py", "app.py")], self.root
        )
        self.assertEqual(result.constructs, 2)
        self.assertEqual([(v.file, v.line) for v in result.violations], [("app.py", 1)])


class RunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _collapsible_app(self):
        (self.root / "app.py").write_text(
            "def f(a, b):\n    if a:\n        if b:\n            print('x')\n",
            encoding="utf-8",
        )

    def test_no_files_exits_0(self):
        self.assertEqual(run([], self.root), 0)

    def test_warning_severity_default_reports_without_failing(self):
        self._collapsible_app()
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = run([], self.root)
        self.assertEqual(code, 0)
        self.assertIn("[WARN] app.py:2: nested if", buffer.getvalue())
        self.assertIn("1 collapsible-flow findings", buffer.getvalue())

    def test_error_severity_exits_2(self):
        self._collapsible_app()
        with contextlib.redirect_stdout(io.StringIO()):
            code = run(["--severity", "error"], self.root)
        self.assertEqual(code, 2)

    def test_line_ignore_marker_suppresses(self):
        (self.root / "app.py").write_text(
            "def f(a, b):\n"
            "    # ignore:collapsible_flow left split for readability\n"
            "    if a:\n"
            "        if b:\n"
            "            print('x')\n",
            encoding="utf-8",
        )
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = run([], self.root)
        self.assertEqual(code, 0)
        self.assertIn("no collapsible flow", buffer.getvalue())

    def test_file_ignore_marker_suppresses(self):
        (self.root / "app.py").write_text(
            "# ignore-file:collapsible_flow dispatch WIP\n"
            "def f(a, b):\n"
            "    if a:\n"
            "        if b:\n"
            "            print('x')\n",
            encoding="utf-8",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run([], self.root), 0)

    def test_bad_severity_rejected(self):
        with self.assertRaises(SystemExit) as ctx:
            run(["--severity", "fatal"], self.root)
        self.assertEqual(ctx.exception.code, 1)


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
        r = self._run("collapsible-flow", "--bogus")
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_warning_output_and_exit_0(self):
        (self.root / "app.py").write_text(
            "def f(c):\n    if c:\n        return 'a'\n    else:\n        return 'b'\n",
            encoding="utf-8",
        )
        r = self._run("collapsible-flow")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[WARN] app.py:2: redundant else", r.stdout)

    def test_dispatched_from_main_cli(self):
        r = self._run("collapsible-flow", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("crap4py collapsible-flow", r.stdout)


if __name__ == "__main__":
    unittest.main()
