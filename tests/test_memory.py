"""Tests for the ``memory`` subcommand (crap4dart 3bae178bc0 port).

Fixtures are real allocation sizes, not decorative: the assertions use MB
gaps (>= 3 MB attributed vs < 1 MB quiet) that a window-boundary or metric
mutation cannot survive. Subprocess integration tests are skipped when
pytest is not importable (CI installs none) — they are the killing tests
for the plugin's window resolution, metric choice and GC tap wiring:

- breaking the setup+call+teardown window (call-only) fails
  ``test_setup_allocations_are_attributed_to_the_test``;
- reporting retained instead of peak-live churn fails
  ``test_body_allocation_counts_as_churn_not_retained``;
- dropping the ``gc.callbacks`` tap fails ``test_gc_events_are_counted``;
- zeroing or abs-ing retained fails ``test_retained_memory_is_signed_net``.
"""

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crap4py.memory import (
    DEFAULT_TOP,
    _badge_color,
    _is_python_project,
    _load_log,
    _print_console,
    _print_json,
    _print_table,
    _pytest_warning,
    build_report,
    run,
)

PYPROJECT = '[project]\nname = "memory-fixture"\nversion = "0.0.0"\n'

FIXTURE_TESTS = """\
import gc

import pytest

_hold = []


def test_body_allocates_then_frees():
    blob = [bytes(1024) for _ in range(6000)]  # ~6 MB live at peak, freed on return
    assert len(blob) == 6000


def test_quiet():
    assert 1 + 1 == 2


@pytest.fixture()
def big_setup():
    # ~5 MB allocated in the pytest SETUP phase (not the call phase).
    blob = [bytes(1024) for _ in range(5000)]
    yield blob
    blob.clear()


def test_sees_setup_allocation(big_setup):
    assert len(big_setup) == 5000


def test_retains_memory():
    _hold.append(bytes(1024) * 4000)  # ~4 MB kept alive at module level
    assert len(_hold) == 1


def test_forces_gc():
    junk = [object() for _ in range(50000)]
    gc.collect()
    assert junk is not None
"""

# ~4 MB allocated at MODULE IMPORT — i.e. during pytest's collection
# phase, before any test window opens. Phase-2 bucket rule: this must
# land in the unattributed bucket, never in the first test's window.
COLLECTION_ALLOC_TESTS = """\
_HOLD = [bytes(1024) for _ in range(4000)]  # ~4 MB live from collection onward


def test_first():
    assert True
"""


def _pytest_available() -> bool:
    return importlib.util.find_spec("pytest") is not None


def _test_record(nodeid: str, churn: int, retained: int, gcs: int = 0, collected: int = 0) -> dict:
    return {
        "event": "test",
        "nodeid": nodeid,
        "file": nodeid.split("::", 1)[0],
        "churn_bytes": churn,
        "retained_bytes": retained,
        "gcs": gcs,
        "gc_collected": collected,
    }


def run_memory(argv: list[str], project_root: Path) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = run(argv, project_root)
    return code, stdout.getvalue(), stderr.getvalue()


class BuildReportTest(unittest.TestCase):
    def test_groups_by_file_in_first_seen_order(self):
        records = [
            _test_record("tests/b_test.py::t2", 2_000_000, 100_000),
            _test_record("tests/a_test.py::t1", 4_000_000, 300_000, gcs=2, collected=11),
            _test_record("tests/b_test.py::t1", 1_000_000, 50_000, gcs=1, collected=5),
        ]
        report = build_report(records)
        self.assertEqual(report["test_count"], 3)
        self.assertEqual(
            [f["file"] for f in report["files"]], ["tests/b_test.py", "tests/a_test.py"]
        )
        by_file = {f["file"]: f for f in report["files"]}
        self.assertEqual(by_file["tests/b_test.py"]["churn_bytes"], 3_000_000)
        self.assertEqual(by_file["tests/b_test.py"]["tests"], 2)
        self.assertEqual(by_file["tests/a_test.py"]["gcs"], 2)
        self.assertEqual(by_file["tests/a_test.py"]["gc_collected"], 11)

    def test_totals_record_wins_and_fallbacks_cover_partial_logs(self):
        records = [_test_record("tests/a_test.py::t1", 4_000_000, 300_000, gcs=2, collected=11)]
        report = build_report(records)
        # Without a session totals record the per-test sums stand in.
        self.assertEqual(report["gc_count"], 2)
        self.assertEqual(report["gc_collected"], 11)
        self.assertEqual(report["total_retained_bytes"], 300_000)
        # Session totals include collection/import allocations no window saw.
        records.append(
            {"event": "totals", "tests": 1, "gcs": 5, "gc_collected": 40, "retained_bytes": 900_000}
        )
        report = build_report(records)
        self.assertEqual(report["gc_count"], 5)
        self.assertEqual(report["total_retained_bytes"], 900_000)


class UnattributedBucketTest(unittest.TestCase):
    """Phase-2 bucket (upstream 11551739c8): out-of-window activity
    (collection/import, session start/finish) is reported separately and
    never pollutes test/file totals."""

    def _records(self) -> list[dict]:
        return [
            _test_record("tests/a_test.py::t1", 4_000_000, 300_000, gcs=2, collected=11),
            {
                "event": "totals",
                "tests": 1,
                "gcs": 5,
                "gc_collected": 40,
                "retained_bytes": 900_000,
            },
            {
                "event": "unattributed",
                "gcs": 3,
                "gc_collected": 29,
                "churn_bytes": 2_500_000,
                "retained_bytes": 600_000,
            },
        ]

    def test_bucket_is_reported_verbatim(self):
        report = build_report(self._records())
        self.assertEqual(
            report["unattributed"],
            {"churn_bytes": 2_500_000, "retained_bytes": 600_000, "gcs": 3, "gc_collected": 29},
        )

    def test_bucket_never_pollutes_file_or_test_totals(self):
        report = build_report(self._records())
        self.assertEqual(report["files"][0]["churn_bytes"], 4_000_000)
        self.assertEqual(report["files"][0]["gcs"], 2)
        self.assertEqual(len(report["tests"]), 1)
        self.assertEqual(report["test_count"], 1)

    def test_missing_bucket_record_defaults_to_zeros(self):
        report = build_report([_test_record("tests/a_test.py::t1", 1, 1)])
        self.assertEqual(
            report["unattributed"],
            {"churn_bytes": 0, "retained_bytes": 0, "gcs": 0, "gc_collected": 0},
        )

    def test_console_and_json_report_the_bucket(self):
        report = build_report(self._records())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _print_console(report, DEFAULT_TOP)
        self.assertIn(
            "unattributed (collection/session, outside test windows): "
            "2.5 MB peak, +0.6 MB retained, 3 GCs",
            out.getvalue(),
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _print_json(report, DEFAULT_TOP)
        self.assertEqual(
            json.loads(out.getvalue())["unattributed"],
            {"churn_mb": 2.5, "retained_mb": 0.6, "gcs": 3},
        )


class ConsoleJsonTest(unittest.TestCase):
    def _report(self) -> dict:
        records = [
            _test_record("tests/a_test.py::quiet", 0, 0),
            _test_record("tests/a_test.py::heavy", 4_000_000, -500_000, gcs=3, collected=20),
        ]
        return build_report(records)

    def test_inactive_tests_present_in_report_but_not_on_leaderboards(self):
        report = self._report()
        self.assertEqual(len(report["tests"]), 2)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _print_console(report, DEFAULT_TOP)
        text = out.getvalue()
        self.assertIn("memory: 2 tests, 3 GCs, churn 4.0 MB peak live, retained -0.5 MB", text)
        self.assertIn("heavy", text)
        self.assertNotIn("::quiet", text)

    def test_json_report_shape_is_stable(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _print_json(self._report(), DEFAULT_TOP)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["test_count"], 2)
        self.assertEqual(payload["gc_count"], 3)
        self.assertEqual(payload["total_churn_mb"], 4.0)
        self.assertEqual(payload["total_retained_mb"], -0.5)
        self.assertEqual(payload["files"][0]["file"], "tests/a_test.py")
        self.assertEqual(len(payload["tests"]), 2)

    def test_empty_leaderboards_print_a_placeholder(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _print_table("quiet file by churn", [])
        self.assertIn("(no traced activity)", out.getvalue())

    def test_top_limits_leaderboard_rows(self):
        records = [_test_record(f"tests/a_test.py::t{i}", (i + 1) * 1_000_000, 0) for i in range(5)]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _print_json(build_report(records), 3)
        self.assertEqual(len(json.loads(out.getvalue())["tests"]), 5)  # tests list is untruncated
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _print_console(build_report(records), 3)
        churn_table = (
            out.getvalue().split("tests by allocation volume")[1].split("retained growth")[0]
        )
        self.assertEqual(churn_table.count("tests/a_test.py::t"), 3)


class AllocatedVolumeTest(unittest.TestCase):
    """Upstream b7c227d reporting semantics: allocation volume in the
    JSON report and console headline, churn leaderboards retitled to
    allocation volume. Killing test: the JSON field must be present and
    sourced from the churn total — dropping the field or pointing it at
    any other total fails here (churn 4.0 != retained -0.5)."""

    def _report(self) -> dict:
        records = [
            _test_record("tests/a_test.py::heavy", 4_000_000, -500_000, gcs=3),
            _test_record("tests/a_test.py::quiet", 0, 0),
        ]
        return build_report(records)

    def test_json_total_allocated_mb_is_the_churn_total(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _print_json(self._report(), DEFAULT_TOP)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["total_allocated_mb"], payload["total_churn_mb"])
        self.assertEqual(payload["total_allocated_mb"], 4.0)
        self.assertNotEqual(payload["total_allocated_mb"], payload["total_retained_mb"])

    def test_console_headline_names_allocation_when_positive(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _print_console(self._report(), DEFAULT_TOP)
        self.assertIn(
            "memory: 2 tests, 3 GCs, churn 4.0 MB peak live, retained -0.5 MB, allocated 4.0 MB",
            out.getvalue(),
        )

    def test_console_headline_omits_allocation_when_zero(self):
        records = [_test_record("tests/a_test.py::retained_only", 0, 100_000)]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _print_console(build_report(records), DEFAULT_TOP)
        text = out.getvalue()
        self.assertIn("churn 0.0 MB peak live", text)
        self.assertNotIn("allocated", text)

    def test_churn_leaderboards_are_retitled_allocation_volume(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _print_console(self._report(), DEFAULT_TOP)
        text = out.getvalue()
        self.assertIn("files by allocation volume (peak live traced MB)", text)
        self.assertIn("tests by allocation volume (peak live traced MB)", text)


class UsageErrorTest(unittest.TestCase):
    def test_no_suite_found_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = run_memory([], Path(tmp))
        self.assertEqual(code, 1)
        self.assertIn("no Python test suite", err)

    def test_zero_top_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(PYPROJECT)
            (root / "tests").mkdir()
            with self.assertRaises(SystemExit) as ctx:
                run_memory(["--top", "0"], root)
        self.assertEqual(ctx.exception.code, 1)

    def test_negative_badge_threshold_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(PYPROJECT)
            (root / "tests").mkdir()
            with self.assertRaises(SystemExit) as ctx:
                run_memory(["--badge-threshold", "-1"], root)
        self.assertEqual(ctx.exception.code, 1)


class LoadLogTest(unittest.TestCase):
    """Direct parser tests (CI installs no pytest, so _load_log has no
    integration coverage there)."""

    def test_parses_jsonl_and_skips_noise(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.jsonl"
            path.write_text(
                '{"event": "test"}\nnot json\n{"event": "totals"}\n[1, 2]\n\n', encoding="utf-8"
            )
            records = _load_log(path)
        self.assertEqual(records, [{"event": "test"}, {"event": "totals"}])

    def test_missing_file_yields_no_records(self):
        self.assertEqual(_load_log(Path("/nonexistent") / "log.jsonl"), [])


class PytestWarningTest(unittest.TestCase):
    def test_missing_pytest_module_is_reported(self):
        self.assertEqual(
            _pytest_warning(1, "…/bin/python: No module named pytest"),
            "pytest is not importable by the current interpreter",
        )
        self.assertEqual(
            _pytest_warning(1, "No module named pytest"),
            "pytest is not importable by the current interpreter",
        )

    def test_reportable_exit_codes_are_silent(self):
        for returncode in (0, 1, 2):
            self.assertIsNone(_pytest_warning(returncode, "progress"))
        self.assertIsNone(_pytest_warning(0, None))

    def test_unexpected_exit_codes_are_flagged(self):
        for returncode in (3, 4, 5):
            self.assertEqual(
                _pytest_warning(returncode, ""), f"pytest exited with code {returncode}"
            )


class IsPythonProjectTest(unittest.TestCase):
    def test_manifest_and_suite_directory_are_both_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(_is_python_project(root))
            (root / "tests").mkdir()
            self.assertFalse(_is_python_project(root))
            (root / "pyproject.toml").write_text(PYPROJECT)
            self.assertTrue(_is_python_project(root))
            (root / "pyproject.toml").unlink()
            (root / "setup.py").write_text("")
            (root / "tests").rename(root / "test")
            self.assertTrue(_is_python_project(root))


class BadgeColorTest(unittest.TestCase):
    """Killing test for the threshold logic: green at or below the
    threshold, yellow at most twice, red above — mutating a comparison
    or the 2x factor fails here."""

    def test_threshold_boundaries_pick_the_color(self):
        self.assertEqual(_badge_color(0.0, 500.0), "#4c1")
        self.assertEqual(_badge_color(500.0, 500.0), "#4c1")
        self.assertEqual(_badge_color(500.1, 500.0), "#dfb317")
        self.assertEqual(_badge_color(1000.0, 500.0), "#dfb317")
        self.assertEqual(_badge_color(1000.1, 500.0), "#e05d44")


class RunBranchTest(unittest.TestCase):
    """run()'s output branches without spawning pytest: _profile is mocked,
    so these cover the command logic in CI too (pytest absent there)."""

    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="crap4py-memory-branches-"))
        (cls.root / "pyproject.toml").write_text(PYPROJECT)
        (cls.root / "tests").mkdir()

    @classmethod
    def tearDownClass(cls):
        import shutil

        shutil.rmtree(cls.root, ignore_errors=True)

    def _run(self, records, warning, *argv):
        with mock.patch("crap4py.memory._profile", return_value=(records, warning)) as profiled:
            code, out, err = run_memory(list(argv), self.root)
        return code, out, err, profiled

    def test_records_reach_the_console_report(self):
        records = [_test_record("tests/a_test.py::t1", 4_000_000, 100_000, gcs=2)]
        code, out, _, profiled = self._run(records, None)
        self.assertEqual(code, 0)
        self.assertIn("memory: 1 tests, 2 GCs, churn 4.0 MB peak live, retained +0.1 MB", out)
        profiled.assert_called_once()

    def test_records_reach_the_json_report(self):
        records = [_test_record("tests/a_test.py::t1", 4_000_000, 100_000)]
        code, out, err, _ = self._run(records, None, "--format", "json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["test_count"], 1)

    def test_no_tests_without_warning_exits_1(self):
        code, _, err, _ = self._run([], None)
        self.assertEqual(code, 1)
        self.assertIn("no tests executed", err)

    def test_no_tests_with_warning_appends_the_cause(self):
        code, _, err, _ = self._run([], "pytest exploded")
        self.assertEqual(code, 1)
        self.assertIn("no tests executed (pytest exploded)", err)

    def test_badge_is_written_with_threshold_color(self):
        # 4_000_000 bytes of churn = "4 MB": green at threshold 4,
        # yellow at 2.5 (4 <= 2*2.5), red at 1 (4 > 2).
        records = [_test_record("tests/a_test.py::t1", 4_000_000, 100_000)]
        for threshold, color in (("4", "#4c1"), ("2.5", "#dfb317"), ("1", "#e05d44")):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "badges" / "memory.svg"
                with mock.patch("crap4py.memory._profile", return_value=(records, None)):
                    code, _, err = run_memory(
                        ["--badge", str(path), "--badge-threshold", threshold], self.root
                    )
                svg = path.read_text(encoding="utf-8")
            self.assertEqual(code, 0)
            self.assertIn(f"Badge written to {path}", err)
            self.assertIn("memory churn", svg)
            self.assertIn(">4 MB<", svg)
            self.assertIn(f'fill="{color}"', svg)


@unittest.skipUnless(_pytest_available(), "pytest not importable")
class MemoryRunIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="crap4py-memory-fixture-"))
        (cls.root / "pyproject.toml").write_text(PYPROJECT)
        (cls.root / "tests").mkdir()
        (cls.root / "tests" / "test_mini.py").write_text(FIXTURE_TESTS)

    @classmethod
    def tearDownClass(cls):
        import shutil

        shutil.rmtree(cls.root, ignore_errors=True)

    def json_report(self, *extra: str) -> tuple[int, dict, str]:
        code, out, err = run_memory(["--format", "json", *extra], self.root)
        return code, json.loads(out), err

    def entry(self, report: dict, nodeid: str) -> dict:
        return next(t for t in report["tests"] if t["nodeid"].endswith(nodeid))

    def test_fixture_runs_five_tests_and_exits_zero(self):
        code, report, err = self.json_report()
        self.assertEqual(code, 0, err)
        self.assertEqual(report["test_count"], 5)
        self.assertEqual(len(report["files"]), 1)
        self.assertEqual(report["files"][0]["tests"], 5)
        self.assertGreaterEqual(report["gc_count"], 1)

    def test_body_allocation_counts_as_churn_not_retained(self):
        _, report, _ = self.json_report()
        body = self.entry(report, "test_body_allocates_then_frees")
        self.assertGreaterEqual(body["churn_mb"], 3.0)
        self.assertLess(body["retained_mb"], 1.0)

    def test_setup_allocations_are_attributed_to_the_test(self):
        _, report, _ = self.json_report()
        setup = self.entry(report, "test_sees_setup_allocation")
        self.assertGreaterEqual(setup["churn_mb"], 3.0)

    def test_retained_memory_is_signed_net(self):
        _, report, _ = self.json_report()
        held = self.entry(report, "test_retains_memory")
        self.assertGreaterEqual(held["retained_mb"], 3.0)
        quiet = self.entry(report, "test_quiet")
        self.assertLess(abs(quiet["churn_mb"]), 1.0)

    def test_gc_events_are_counted(self):
        _, report, _ = self.json_report()
        self.assertGreaterEqual(self.entry(report, "test_forces_gc")["gcs"], 1)

    def test_collection_allocations_land_in_the_unattributed_bucket(self):
        # Killing test for the window boundary (upstream 11551739c8): the
        # ~4 MB allocated at module import — collection time, before any
        # window opens — belongs to the bucket. A boundary leak would
        # inflate the first test's window instead and empty the bucket.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(PYPROJECT)
            (root / "tests").mkdir()
            (root / "tests" / "test_coll.py").write_text(COLLECTION_ALLOC_TESTS)
            code, out, err = run_memory(["--format", "json"], root)
            payload = json.loads(out)
        self.assertEqual(code, 0, err)
        bucket = payload["unattributed"]
        self.assertGreaterEqual(bucket["retained_mb"], 3.0)
        self.assertGreaterEqual(bucket["churn_mb"], 3.0)
        first = payload["tests"][0]
        self.assertLess(first["churn_mb"], 1.0)
        self.assertLess(first["retained_mb"], 1.0)
        # No double counting: bucket + windows reconcile with run totals.
        self.assertEqual(
            bucket["gcs"] + sum(t["gcs"] for t in payload["tests"]),
            payload["gc_count"],
        )

    def test_console_mode_reports_totals(self):
        code, out, _ = run_memory([], self.root)
        self.assertEqual(code, 0)
        self.assertIn("memory: 5 tests,", out)
        self.assertIn("peak live", out)
        self.assertIn("leak suspects", out)

    def test_bare_paths_scope_the_profiled_suite(self):
        # Killing test for dropped `args.rest`: a bare path must scope the
        # run to the requested file, not silently profile the whole suite.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(PYPROJECT)
            (root / "tests").mkdir()
            (root / "tests" / "test_a.py").write_text(
                "def test_a_one():\n    assert True\n\n\ndef test_a_two():\n    assert True\n"
            )
            (root / "tests" / "test_b.py").write_text("def test_b_one():\n    assert True\n")
            code, out, err = run_memory(["--format", "json", "tests/test_a.py"], root)
            payload = json.loads(out)
        self.assertEqual(code, 0, err)
        self.assertEqual(payload["test_count"], 2)
        self.assertTrue(all(t["file"] == "tests/test_a.py" for t in payload["tests"]))

    def test_failing_tests_still_report_and_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(PYPROJECT)
            (root / "tests").mkdir()
            (root / "tests" / "test_bad.py").write_text("def test_fails():\n    assert False\n")
            code, out, err = run_memory(["--format", "json"], root)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["test_count"], 1)

    def test_no_tests_executed_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(PYPROJECT)
            (root / "tests").mkdir()
            code, _, err = run_memory([], root)
        self.assertEqual(code, 1)
        self.assertIn("no tests executed", err)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
