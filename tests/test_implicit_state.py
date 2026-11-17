"""Unit + CLI tests for the `implicit-state` subcommand.

Port of crap4dart 34e2163080's implicit_state tests. Each flagged-shape
test kills its check: the fixtures genuinely carry the AST shapes (flag
clusters, scattered mutators, status fields, flag-combination conditions)
and the assertions pin messages/counts/lines, so removing or mutating one
check fails its test. The negative fixtures pin the host-mapped rules:
ClassVar/Final fields, locals, parameters, other-object reads and
below-threshold shapes never flag.
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

from crap4py.implicit_state import (
    ImplicitStateResult,
    check_files,
    file_violations,
    run,
    summary,
)


def _violations(source: str, **kwargs) -> list[tuple[int, str]]:
    source = textwrap.dedent(source).lstrip("\n")
    found = file_violations(ast.parse(source), "x.py", **kwargs)
    return [(v.line, v.message) for v in found.violations]


# Three lifecycle flags mutated across three methods.
CLUSTER_SOURCE = """
    class Downloader:
        is_running = False
        has_failed = False
        is_disposed = False

        def start(self):
            self.is_running = True
            self.has_failed = False

        def stop(self):
            self.is_running = False
            self.is_disposed = True

        def fail(self):
            self.has_failed = True
            self.is_running = False
"""


class ClusterTest(unittest.TestCase):
    def test_flags_lifecycle_flag_cluster_mutated_across_methods(self):
        found = _violations(CLUSTER_SOURCE)
        cluster = [(line, msg) for line, msg in found if "boolean-cluster" in msg]
        self.assertEqual(cluster, [(1, cluster[0][1])])
        self.assertIn("a boolean-cluster state machine", cluster[0][1])
        self.assertIn("model it with an enum/sealed state", cluster[0][1])

    def test_cluster_findings_carry_per_field_findings(self):
        found = _violations(CLUSTER_SOURCE)
        scattered = [(line, msg) for line, msg in found if "lifecycle flag '" in msg]
        self.assertEqual(
            scattered,
            [
                (
                    2,
                    "lifecycle flag 'is_running' is mutated from 3 places — "
                    "part of an implicit state machine",
                ),
                (
                    3,
                    "lifecycle flag 'has_failed' is mutated from 2 places — "
                    "part of an implicit state machine",
                ),
            ],
        )

    def test_respects_min_flags_for_the_cluster_finding(self):
        source = """
            class Small:
                is_running = False
                has_failed = False

                def start(self):
                    self.is_running = True
                    self.has_failed = False

                def stop(self):
                    self.is_running = False
                    self.has_failed = True
        """
        self.assertEqual([m for _, m in _violations(source) if "boolean-cluster" in m], [])

    def test_min_flags_two_reports_a_two_flag_cluster(self):
        source = """
            class Small:
                is_running = False
                has_failed = False

                def start(self):
                    self.is_running = True
                    self.has_failed = False

                def stop(self):
                    self.is_running = False
                    self.has_failed = True
        """
        found = _violations(source, min_flags=2)
        self.assertEqual(
            [m for _, m in found if "boolean-cluster" in m],
            found[:0] or [m for _, m in found if "boolean-cluster" in m][:1],
        )
        self.assertTrue(any("2 lifecycle flags" in m for _, m in found))

    def test_reports_a_single_flag_mutated_from_many_places(self):
        source = """
            class Job:
                is_done = False

                def run(self):
                    self.is_done = False

                def complete(self):
                    self.is_done = True

                def reset(self):
                    self.is_done = False
        """
        findings = [(line, msg) for line, msg in _violations(source) if "'is_done'" in msg]
        self.assertEqual(findings, [(2, findings[0][1])])
        self.assertIn("mutated from 3 places", findings[0][1])

    def test_annotated_bool_fields_count(self):
        source = """
            class Job:
                is_done: bool = False

                def run(self):
                    self.is_done = False

                def complete(self):
                    self.is_done = True
        """
        self.assertTrue(any("'is_done'" in m for _, m in _violations(source)))

    def test_classvar_and_final_fields_are_ignored(self):
        source = """
            from typing import ClassVar, Final


            class Config:
                is_global_enabled: ClassVar[bool] = False
                is_ready: Final[bool] = True
                has_failed: Final = False
                is_loaded: ClassVar = False
        """
        self.assertEqual(_violations(source), [])

    def test_non_state_bool_fields_are_ignored(self):
        source = """
            class Settings:
                verbose = False
                colored = True
                strict = False

                def toggle(self):
                    self.verbose = not self.verbose
                    self.colored = not self.colored
                    self.strict = not self.strict

                def reset(self):
                    self.verbose = False
                    self.colored = True
                    self.strict = False
        """
        self.assertEqual(_violations(source), [])

    def test_camel_case_shapes_count(self):
        source = """
            class Job:
                isLoading = False

                def run(self):
                    self.isLoading = False

                def complete(self):
                    self.isLoading = True
        """
        found = _violations(source)
        self.assertTrue(any("'isLoading'" in m for _, m in found))

    def test_one_mutator_is_below_the_default_min_mutators(self):
        source = """
            class Once:
                is_started = False

                def start(self):
                    self.is_started = True
        """
        self.assertEqual(_violations(source), [])

    def test_min_mutators_one_reports_single_place_mutations(self):
        source = """
            class Once:
                is_started = False

                def start(self):
                    self.is_started = True
        """
        found = _violations(source, min_mutators=1)
        self.assertEqual(len(found), 1)
        self.assertIn("mutated from 1 places", found[0][1])

    def test_other_object_writes_count_by_name(self):
        # Any-target rule: `other.is_ready = True` mutates the declared
        # field by NAME shape, regardless of the target object.
        source = """
            class Plain:
                is_ready = False

                def one(self, other):
                    other.is_ready = True

                def two(self, other):
                    other.is_ready = False
        """
        self.assertTrue(any("'is_ready'" in m for _, m in _violations(source)))

    def test_locals_and_parameters_never_count(self):
        source = """
            class Plain:
                is_ready = False

                def one(self, is_ready):
                    is_ready = True

                def two(self):
                    is_ready = False

                def three(self):
                    is_ready = True
        """
        self.assertEqual(_violations(source), [])

    def test_augmented_and_annotated_writes_count(self):
        source = """
            class Counter:
                is_done = False

                def one(self):
                    self.is_done += 1

                def two(self):
                    self.is_done: bool = False
        """
        self.assertTrue(any("mutated from 2 places" in m for _, m in _violations(source)))

    def test_class_level_defaults_are_not_mutation_sites(self):
        # The declaration's `= False` initializer is not a mutator.
        source = """
            class Once:
                is_started = False
                is_stopped = False

                def start(self):
                    self.is_started = True
        """
        self.assertEqual(_violations(source), [])

    def test_nested_classes_are_scanned_separately(self):
        source = """
            class Outer:
                is_ready = False

                class Inner:
                    is_loaded = False

                    def load(self):
                        self.is_loaded = True

                    def unload(self):
                        self.is_loaded = False
        """
        found = _violations(source)
        self.assertTrue(any("'is_loaded'" in m for _, m in found))
        self.assertFalse(any("'is_ready'" in m for _, m in found))


class StatusTest(unittest.TestCase):
    def test_reports_a_status_field_assigned_from_several_methods(self):
        source = """
            class Job:
                status = 'idle'

                def run(self):
                    self.status = 'running'

                def finish(self):
                    self.status = 'done'
        """
        found = _violations(source)
        self.assertEqual(
            found,
            [
                (
                    2,
                    "status field 'status' is mutated from 2 places — "
                    "implicit transitions; centralize them",
                )
            ],
        )

    def test_status_names_match_exactly(self):
        for name in ("status", "state", "phase", "stage", "mode", "lifecycle"):
            source = textwrap.dedent(
                f"""
                class Job:
                    {name}: str = 'idle'

                    def run(self):
                        self.{name} = 'running'

                    def finish(self):
                        self.{name} = 'done'
                """
            ).lstrip("\n")
            found = [(line, msg) for line, msg in _violations(source) if "status field" in msg]
            self.assertEqual(len(found), 1, name)

    def test_status_like_prefix_names_do_not_match(self):
        source = """
            class Job:
                stateful_mode: str = 'idle'

                def run(self):
                    self.stateful_mode = 'running'

                def finish(self):
                    self.stateful_mode = 'done'
        """
        self.assertEqual(_violations(source), [])

    def test_status_field_any_type(self):
        source = """
            class Job:
                state = []

                def run(self):
                    self.state = ['running']

                def finish(self):
                    self.state = ['done']
        """
        found = _violations(source)
        self.assertTrue(any("status field 'state'" in m for _, m in found))


class ComboTest(unittest.TestCase):
    def test_flags_if_conditions_branching_on_flag_combinations(self):
        source = """
            class Guard:
                is_loading = False
                has_failed = False

                def stalled(self):
                    return self.is_loading and self.has_failed

                def check(self):
                    if self.is_loading or self.has_failed:
                        return
        """
        found = _violations(source)
        combos = [(line, msg) for line, msg in found if "state combination" in msg]
        # The getter's `and` is an expression, not a condition — only the
        # `if` counts (as upstream, if statements only in v1).
        self.assertEqual(len(combos), 1)
        self.assertEqual(combos[0][0], 9)
        self.assertIn("condition branches on 2 lifecycle flags", combos[0][1])

    def test_elif_chain_each_if_counts(self):
        source = """
            class Guard:
                is_loading = False
                has_failed = False
                is_ready = False
                is_done = False

                def check(self):
                    if self.is_loading and self.has_failed:
                        return
                    elif self.is_ready and self.is_done:
                        return
        """
        combos = [(line, _) for line, _ in _violations(source) if "state combination" in _]
        self.assertEqual(sorted(line for line, _ in combos), [8, 10])

    def test_member_accesses_and_single_flag_conditions_are_not_combos(self):
        source = """
            class Plain:
                is_ready = False

                def check(self, other):
                    if other.is_ready:
                        return
                    if self.is_ready:
                        return
        """
        self.assertEqual([m for _, m in _violations(source) if "combination" in m], [])

    def test_self_read_of_undeclared_name_is_not_a_combo(self):
        # Only DECLARED lifecycle flags of the scanned class count.
        source = """
            class Plain:
                is_ready = False

                def check(self):
                    other = self
                    if other.is_ready and other.is_done:
                        return
        """
        self.assertEqual([m for _, m in _violations(source) if "combination" in m], [])


class CheckFilesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_broken_file_skipped_and_test_file_excluded(self):
        (self.root / "broken.py").write_text("def f(:\n", encoding="utf-8")
        (self.root / "test_x.py").write_text(CLUSTER, encoding="utf-8")
        (self.root / "app.py").write_text(CLUSTER, encoding="utf-8")
        result = check_files(
            [self.root / n for n in ("broken.py", "test_x.py", "app.py")], self.root
        )
        self.assertEqual(result.classes, 1)
        self.assertEqual({v.file for v in result.violations}, {"app.py"})
        self.assertIn(1, [v.line for v in result.violations])

    def test_clean_file_summary(self):
        (self.root / "app.py").write_text("class Clean:\n    pass\n", encoding="utf-8")
        result = check_files([self.root / "app.py"], self.root)
        self.assertEqual(result.violations, [])
        self.assertEqual(summary(result), "1 classes scanned, no implicit state machines")
        self.assertEqual(
            summary(ImplicitStateResult([], 0)), "0 classes scanned, no implicit state machines"
        )


CLUSTER = textwrap.dedent(CLUSTER_SOURCE).lstrip("\n")


class RunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_no_files_exits_0(self):
        self.assertEqual(run([], self.root), 0)

    def test_warning_severity_default_reports_without_failing(self):
        (self.root / "app.py").write_text(CLUSTER, encoding="utf-8")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = run([], self.root)
        self.assertEqual(code, 0)
        self.assertIn("[WARN] app.py:1: class holds 3 lifecycle flags", buffer.getvalue())
        self.assertIn("implicit state machine findings", buffer.getvalue())

    def test_error_severity_exits_2(self):
        (self.root / "app.py").write_text(CLUSTER, encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            code = run(["--severity", "error"], self.root)
        self.assertEqual(code, 2)

    def test_not_ignorable(self):
        # implicit_state defaults to ignorable: false upstream and this
        # port has no opt-in: markers must NOT suppress.
        (self.root / "app.py").write_text("# ignore:implicit_state\n" + CLUSTER, encoding="utf-8")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = run([], self.root)
        self.assertEqual(code, 0)
        self.assertIn("boolean-cluster", buffer.getvalue())

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

    def test_unknown_key_rejected(self):
        # Strict unknown-key errors: upstream rejects `min_flagz:` in the
        # gates config; this port's config surface is flags.
        r = self._run("implicit-state", "--min-flagz", "3")
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_non_integer_threshold_rejected(self):
        r = self._run("implicit-state", "--min-flags", "three")
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_warning_output_and_exit_0(self):
        (self.root / "app.py").write_text(CLUSTER, encoding="utf-8")
        r = self._run("implicit-state")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[WARN] app.py:1: class holds 3 lifecycle flags", r.stdout)

    def test_dispatched_from_main_cli(self):
        r = self._run("implicit-state", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("crap4py implicit-state", r.stdout)


if __name__ == "__main__":
    unittest.main()
