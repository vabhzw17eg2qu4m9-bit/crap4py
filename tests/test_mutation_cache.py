"""Tests for the mutation digest cache and tree-integrity check.

Port of upstream crap4dart 16d9c4c (digest-incremental cache, test_args
scoping) + 8b2e8e8 (end-of-run tree integrity check), pinned against real
scan_files behavior with injected runners.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from crap4py.coverage import parse_coverage_dict
from crap4py.mutation import (
    CATEGORIES,
    MUTATION_CACHE_FILE,
    MUTATION_CACHE_VERSION,
    CachedMethod,
    CachedOutcome,
    Killed,
    MutationCache,
    MutationCacheMeta,
    MutationUnavailable,
    Survived,
    _verify_integrity,
    default_test_command,
    fnv1a64_hex,
    scan_files,
)

# `add` is on executable line 2, `zero` on line 6 (two methods, two sites).
FIXTURE = "def add(a, b):\n    return a + b\n\n\ndef zero():\n    return 0\n"
FIXTURE_B = "def sub(a, b):\n    return a - b\n"
TEST_BODY = (
    "import sys, pathlib\n"
    "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))\n"
    "import a\n"
    "import unittest\n"
    "\n"
    "\n"
    "class ATest(unittest.TestCase):\n"
    "    def test_add(self):\n"
    "        self.assertEqual(a.add(2, 3), 5)\n"
    "\n"
    "    def test_zero(self):\n"
    "        self.assertEqual(a.zero(), 0)\n"
)


def _cov_dict(executed, rel="a.py"):
    return {"files": {rel: {"executed_lines": list(executed), "missing_lines": []}}}


def _merged_cov_dict(*per_file):
    files: dict = {}
    for data in per_file:
        files.update(data["files"])
    return {"files": files}


def _cov(root, executed=(2, 6), rel="a.py", write=True):
    data = _cov_dict(executed, rel)
    if write:
        (root / "coverage.json").write_text(json.dumps(data))
    return parse_coverage_dict(data, root)


def _written_meta() -> MutationCacheMeta:
    """The meta scan_files stamps by default (all categories, no test args)."""
    return MutationCacheMeta.current(CATEGORIES, ())


class CacheRunTest(unittest.TestCase):
    """scan_files-driven cache behavior (real site->method binding)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "a.py").write_text(FIXTURE)
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_a.py").write_text(TEST_BODY)
        _cov(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _scan(self, coverage, **kwargs):
        return scan_files([self.root / "a.py"], self.root, coverage, **kwargs)

    def test_second_run_replays_kills_without_executing(self):
        calls = []

        def runner(root, timeout):
            calls.append(root)
            return Killed

        first = self._scan(_cov(self.root, write=False), runner=runner)
        self.assertEqual(len(calls), 2)
        self.assertIn("score 100.0%", first.summary)
        self.assertIn("2 killed", first.summary)
        self.assertTrue((self.root / MUTATION_CACHE_FILE).exists())

        calls.clear()
        second = self._scan(_cov(self.root, write=False), runner=runner)
        self.assertEqual(calls, [], "killed mutants of unchanged methods must replay")
        self.assertIn("2 killed", second.summary)
        self.assertIn("2 cached", second.summary)
        self.assertIn("score 100.0%", second.summary)

    def test_survivors_are_always_reexecuted(self):
        seq = [Killed, Survived]
        first = self._scan(_cov(self.root, write=False), runner=lambda root, timeout: seq.pop(0))
        self.assertIn("1 killed", first.summary)
        self.assertIn("1 survived", first.summary)

        calls = []
        second = self._scan(
            _cov(self.root, write=False),
            runner=lambda root, timeout: calls.append(root) or Killed,
        )
        self.assertEqual(len(calls), 1, "only the survivor re-executes")
        self.assertIn("1 cached", second.summary)
        self.assertIn("2 killed", second.summary)
        self.assertIn("0 survived", second.summary)

    def test_edit_inside_one_method_reexecutes_only_its_mutants(self):
        self._scan(_cov(self.root, write=False), runner=lambda root, timeout: Killed)

        # A comment inside `add` only: its digest changes, `zero` stays identical.
        edited = FIXTURE.replace("def add(a, b):\n", "def add(a, b):\n    # note\n")
        (self.root / "a.py").write_text(edited)
        calls = []
        second = self._scan(
            _cov(self.root, executed=(3, 7), write=False),
            runner=lambda root, timeout: calls.append(root) or Killed,
        )
        self.assertEqual(len(calls), 1, "only add changed; zero replays from cache")
        self.assertIn("1 cached", second.summary)
        self.assertIn("2 killed", second.summary)

    def test_operator_meta_change_wipes_cache(self):
        self._scan(_cov(self.root, write=False), runner=lambda root, timeout: Killed)
        calls = []
        second = self._scan(
            _cov(self.root, write=False),
            runner=lambda root, timeout: calls.append(root) or Killed,
            operators=("arithmetic",),
        )
        self.assertEqual(len(calls), 1, "operator change must wipe the cache")
        self.assertIn("0 cached", second.summary)

    def test_test_args_meta_change_wipes_cache(self):
        self._scan(_cov(self.root, write=False), runner=lambda root, timeout: Killed)
        calls = []
        second = self._scan(
            _cov(self.root, write=False),
            runner=lambda root, timeout: calls.append(root) or Killed,
            test_args=("tests/test_a.py",),
        )
        self.assertEqual(len(calls), 2, "test_args change must wipe the cache")
        self.assertIn("0 cached", second.summary)

    def test_vanished_methods_are_pruned(self):
        (self.root / "b.py").write_text(FIXTURE_B)
        coverage = parse_coverage_dict(
            _merged_cov_dict(_cov_dict((2, 6)), _cov_dict((2,), rel="b.py")), self.root
        )
        scan_files(
            [self.root / "a.py", self.root / "b.py"],
            self.root,
            coverage,
            runner=lambda root, timeout: Killed,
        )
        cache = MutationCache.load(self.root / MUTATION_CACHE_FILE, _written_meta())
        self.assertEqual(len(cache.methods), 3)

        (self.root / "b.py").unlink()
        calls = []
        self._scan(
            _cov(self.root, write=False),
            runner=lambda root, timeout: calls.append(root) or Killed,
        )
        self.assertEqual(calls, [], "a.py kills still replay")
        cache = MutationCache.load(self.root / MUTATION_CACHE_FILE, _written_meta())
        self.assertEqual(set(cache.methods), {"a.py#add", "a.py#zero"})

    def test_same_name_methods_across_files_keyed_independently(self):
        (self.root / "b.py").write_text(FIXTURE_B)
        coverage = parse_coverage_dict(
            _merged_cov_dict(_cov_dict((2, 6)), _cov_dict((2,), rel="b.py")), self.root
        )
        # Sites come in file order: a.py's two kills, then b.py's survivor.
        seq = [Killed, Killed, Survived]
        first = scan_files(
            [self.root / "a.py", self.root / "b.py"],
            self.root,
            coverage,
            runner=lambda root, timeout: seq.pop(0),
        )
        self.assertIn("1 survived", first.summary)

        calls = []
        second = scan_files(
            [self.root / "a.py", self.root / "b.py"],
            self.root,
            coverage,
            runner=lambda root, timeout: calls.append(root) or Killed,
        )
        self.assertEqual(len(calls), 1, "only b.py's survivor re-executes")
        self.assertIn("3 killed", second.summary)
        self.assertIn("2 cached", second.summary)

    def test_no_cache_skips_replay_and_writes_no_file(self):
        cache_path = self.root / MUTATION_CACHE_FILE

        def runner(root, timeout):
            return Killed

        first = self._scan(_cov(self.root, write=False), runner=runner, use_cache=False)
        second = self._scan(_cov(self.root, write=False), runner=runner, use_cache=False)
        self.assertIn("0 cached", first.summary)
        self.assertIn("0 cached", second.summary)
        self.assertFalse(cache_path.exists())

    def test_corrupt_cache_starts_empty(self):
        (self.root / MUTATION_CACHE_FILE).write_text("{not json")
        calls = []
        second = self._scan(
            _cov(self.root, write=False),
            runner=lambda root, timeout: calls.append(root) or Killed,
        )
        self.assertEqual(len(calls), 2)
        self.assertIn("0 cached", second.summary)

    def test_version_mismatch_starts_empty(self):
        stale = {
            "version": MUTATION_CACHE_VERSION + 1,
            "meta": _written_meta().to_dict(),
            "methods": {},
        }
        (self.root / MUTATION_CACHE_FILE).write_text(json.dumps(stale))
        calls = []
        second = self._scan(
            _cov(self.root, write=False),
            runner=lambda root, timeout: calls.append(root) or Killed,
        )
        self.assertEqual(len(calls), 2)
        self.assertIn("0 cached", second.summary)


class IntegrityTest(unittest.TestCase):
    """End-of-run tree integrity: snapshot, re-verify, self-heal."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.path = self.root / "a.py"
        self.path.write_text(FIXTURE)
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_a.py").write_text(TEST_BODY)
        self.coverage = _cov(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_multi_site_run_leaves_files_byte_identical(self):
        result = scan_files(
            [self.path], self.root, self.coverage, runner=lambda root, timeout: Killed
        )
        self.assertEqual(self.path.read_text(), FIXTURE)
        self.assertNotIn("INTEGRITY", result.summary)

    def test_runner_tampering_cannot_leave_a_mutant_behind(self):
        def sabotage_then_kill(root, timeout):
            self.path.write_text(FIXTURE.replace("a + b", "a != b"))
            return Killed

        result = scan_files([self.path], self.root, self.coverage, runner=sabotage_then_kill)
        self.assertEqual(self.path.read_text(), FIXTURE)
        self.assertNotIn("INTEGRITY", result.summary)

    def test_exception_mid_loop_still_leaves_files_byte_identical(self):
        seq = [Killed, MutationUnavailable("python not found on PATH")]

        def runner(root, timeout):
            outcome = seq.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        result = scan_files([self.path], self.root, self.coverage, runner=runner)
        self.assertTrue(result.summary.startswith("skipped:"))
        self.assertEqual(self.path.read_text(), FIXTURE)

    def test_verify_integrity_self_heals_an_out_of_band_mutation(self):
        original = self.path.read_text()
        snapshot = {self.path: original}
        self.path.write_text(original.replace("a + b", "a == b"))

        violations = _verify_integrity(snapshot)
        self.assertEqual(violations, [str(self.path)])
        self.assertEqual(self.path.read_text(), original, "the mutant must be re-restored")

        self.assertEqual(_verify_integrity(snapshot), [], "healthy tree: no violations")


class CacheUnitTest(unittest.TestCase):
    """Digest stability and cache-file round-trip semantics."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_fnv1a_is_stable_and_sensitive(self):
        add_source = "def add(a, b):\n    return a + b\n"
        self.assertEqual(fnv1a64_hex(add_source), fnv1a64_hex(add_source))
        self.assertNotEqual(fnv1a64_hex(add_source), fnv1a64_hex(add_source.replace("+", "-")))
        # The empty digest is the FNV-1a offset basis; "a" pins xor-then-multiply.
        self.assertEqual(fnv1a64_hex(""), "cbf29ce484222325")
        self.assertEqual(fnv1a64_hex("a"), "1162bb908601ec8c")

    def test_round_trip_and_meta_mismatch(self):
        path = self.root / MUTATION_CACHE_FILE
        meta = MutationCacheMeta.current(("arithmetic", "constant"), ())
        cache = MutationCache(meta)
        cache.methods["a.py#add"] = CachedMethod(
            "deadbeef", (CachedOutcome(0, 2, "arithmetic", "+", "-", Killed),)
        )
        cache.save(path)
        loaded = MutationCache.load(path, meta)
        self.assertEqual(loaded.methods["a.py#add"], cache.methods["a.py#add"])

        other = MutationCache.load(path, MutationCacheMeta.current(("arithmetic",), ()))
        self.assertEqual(other.methods, {}, "meta mismatch must wipe the cache")

    def test_load_missing_file_starts_empty(self):
        cache = MutationCache.load(
            self.root / MUTATION_CACHE_FILE, MutationCacheMeta.current((), ())
        )
        self.assertEqual(cache.methods, {})

    def test_sites_outside_functions_are_never_cached(self):
        (self.root / "a.py").write_text("X = 1 + 1\n")
        coverage = parse_coverage_dict(_cov_dict((1,)), self.root)
        (self.root / "tests").mkdir()
        calls = []
        scan_files(
            [self.root / "a.py"],
            self.root,
            coverage,
            runner=lambda root, timeout: calls.append(root) or Killed,
        )
        self.assertEqual(len(calls), 3, "module-level mutants still execute (1 +, two 1s)")
        cache = MutationCache.load(self.root / MUTATION_CACHE_FILE, _written_meta())
        self.assertEqual(cache.methods, {}, "module-level sites must not be cached")


class TestArgsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "a.py").write_text(FIXTURE)
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_a.py").write_text(TEST_BODY)
        _cov(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_test_args_scope_the_test_command(self):
        baseline = default_test_command(str(self.root))
        scoped = default_test_command(str(self.root), ("tests/test_a.py", "-x"))
        self.assertEqual(scoped[-2:], ["tests/test_a.py", "-x"])
        self.assertEqual(scoped[: len(baseline)], baseline)

    def test_cli_rejects_empty_test_args(self):
        proc = subprocess.run(
            [sys.executable, "-m", "crap4py", "mutation", "--test-args", ""],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("non-empty", proc.stderr)

    def test_cli_warm_run_replays_and_writes_cache(self):
        for expected_cached in ("0 cached", "2 cached"):
            proc = subprocess.run(
                [sys.executable, "-m", "crap4py", "mutation", "a.py"],
                cwd=self.root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("score 100.0%", proc.stdout)
            self.assertIn(expected_cached, proc.stdout)
        cache = json.loads((self.root / MUTATION_CACHE_FILE).read_text())
        self.assertEqual(cache["version"], MUTATION_CACHE_VERSION)
        self.assertEqual(len(cache["methods"]), 2)


if __name__ == "__main__":
    unittest.main()
