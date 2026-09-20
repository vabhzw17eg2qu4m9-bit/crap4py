"""Tests for the ``duplicates`` subcommand (crap4dart duplication gate port)."""

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import crap4py.duplicates as duplicates
from crap4py.duplicates import _line_count, _load, scan_files

# A duplicated block big enough for default --min-tokens 50 windows to fit
# entirely inside one copy (144 tokens over 12 distinct lines).
BLOCK = "".join(f"    value_{i} = compute_item(item, {i}, offset={i * 3})\n" for i in range(12))


def _unique_stem(name: str, salt: int) -> str:
    """Function body with no repeated token windows, even across files:
    every filler line carries the salt in its names and literals."""
    lines = [f"def {name}(items):", f"    acc_{salt} = 0"]
    for i in range(20):
        lines.append(f"    acc_{salt} += step_{salt}_{i}(items, {salt}{i:02d})")
    lines.append(f"    return acc_{salt}")
    return "\n".join(lines) + "\n"


def _pair_body() -> tuple[str, str]:
    """Two files sharing head+block but with distinct unique tails."""
    head = "def duplicated(items):\n    for item in items:\n"
    tail_a = _unique_stem("only_in_alpha", 1)
    tail_b = _unique_stem("only_in_beta", 2)
    return head + BLOCK + tail_a, head + BLOCK + tail_b


def _result(tmp: Path, **kwargs):
    return scan_files(sorted(tmp.rglob("*.py")), tmp, **kwargs)


class DuplicateDetectionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _write(self, name: str, content: str) -> None:
        (self.root / name).write_text(content, encoding="utf-8")

    def test_within_file_duplicate_is_flagged(self):
        body = "def twice(items):\n"
        for name in ("first", "second"):
            body += f"    if {name}(items):\n" + BLOCK
        body += "    return items\n"
        self._write("solo.py", body)
        result = _result(self.root)
        self.assertEqual(len(result.violations), 1)
        self.assertEqual(result.violations[0].file, "solo.py")
        # The shared `items ) :` tail of both `if` lines already starts a
        # matching window, so the first duplicated line is 2, not 3.
        self.assertEqual(result.violations[0].line, 2)
        self.assertIn("duplicated lines > 1.0%", result.violations[0].message)
        self.assertIn("1/1 files over 1.0% duplication", result.summary)

    def test_cross_file_duplicate_marks_both_files(self):
        alpha, beta = _pair_body()
        self._write("alpha.py", alpha)
        self._write("beta.py", beta)
        result = _result(self.root)
        self.assertEqual({v.file for v in result.violations}, {"alpha.py", "beta.py"})

    def test_unique_files_pass_with_zero_share(self):
        self._write("alpha.py", _unique_stem("process_a", 1))
        self._write("beta.py", _unique_stem("process_b", 2))
        result = _result(self.root)
        self.assertEqual(result.violations, ())
        self.assertIn("2 files, 0.00% duplicated lines", result.summary)

    def test_window_below_min_lines_never_matches(self):
        # ~27 tokens across 3 lines: every 10-token window spans < 4 lines,
        # even though x.py and y.py are byte-identical.
        body = "def f(a, b, c, d, e, g, h, i, j, k, l, m):\n    a = b = 0\n    return a\n"
        self._write("x.py", body)
        self._write("y.py", body)
        result = _result(self.root, min_tokens=10, min_lines=4)
        self.assertEqual(result.violations, ())

    def test_window_meeting_min_lines_matches_across_files(self):
        body = (
            "def f(a, b, c, d, e, g, h, i, j, k, l, m):\n    a = b = 0\n    c = d = 1\n"
            "    return a\n"
        )
        self._write("x.py", body)
        self._write("y.py", body)
        result = _result(self.root, min_tokens=10, min_lines=3)
        self.assertEqual({v.file for v in result.violations}, {"x.py", "y.py"})

    def test_file_below_min_tokens_is_skipped_from_the_scan(self):
        # alpha carries the duplicate within itself; tiny.py holds just two
        # tokens and never enters the scan or the summary count.
        alpha = "def twice(items):\n    if first(items):\n" + BLOCK
        alpha += "    if second(items):\n" + BLOCK + "    return items\n"
        self._write("alpha.py", alpha)
        self._write("tiny.py", "x = 1\n")
        result = _result(self.root, min_tokens=10, min_lines=2)
        self.assertFalse(any(v.file == "tiny.py" for v in result.violations))
        self.assertIn("1/1 files over 1.0% duplication", result.summary)

    def test_threshold_boundary_is_exclusive(self):
        # Two identical 2-line functions: 16.67%-style shares are messy at
        # these sizes, so pin the exact 100.00% boundary instead.
        body = "def f(items):\n    return items\n" + "def g(items):\n    return items\n"
        self._write("solo.py", body)
        at_limit = _result(self.root, min_tokens=5, min_lines=1, threshold=100.0)
        self.assertEqual(at_limit.violations, ())
        over = _result(self.root, min_tokens=5, min_lines=1, threshold=99.99)
        self.assertEqual([v.line for v in over.violations], [1])
        self.assertIn("100.00% duplicated lines > 99.99%", over.violations[0].message)

    def test_no_tokenizable_files_passes_with_note(self):
        self._write("tiny.py", "x = 1\n")
        result = _result(self.root)
        self.assertEqual(result.violations, ())
        self.assertEqual(result.summary, "no files with enough tokens")


class SourceAndExcludeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _write(self, name: str, content: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_source_unions_cross_module_file_into_detection(self):
        self._write("src/one.py", _unique_stem("wrapper", 1) + BLOCK)
        self._write("other/two.py", _unique_stem("elsewhere", 2) + BLOCK)
        only_default = scan_files(
            [self.root / "src" / "one.py"], self.root, min_tokens=10, min_lines=2
        )
        self.assertEqual(only_default.violations, ())
        unioned = scan_files(
            [self.root / "src" / "one.py"],
            self.root,
            min_tokens=10,
            min_lines=2,
            sources=["other"],
        )
        self.assertEqual({v.file for v in unioned.violations}, {"other/two.py", "src/one.py"})

    def test_source_plain_py_file_taken_directly(self):
        self._write("keep.py", _unique_stem("keeper", 1))
        self._write("other/lib.py", _unique_stem("libber", 2))
        result = scan_files(
            [self.root / "keep.py"],
            self.root,
            min_tokens=10,
            sources=[str(self.root / "other" / "lib.py")],
        )
        self.assertIn("2 files, 0.00% duplicated lines", result.summary)

    def test_source_non_py_file_is_ignored(self):
        self._write("keep.py", _unique_stem("keeper", 1))
        self._write("other/notes.txt", "hello world\n")
        result = scan_files(
            [self.root / "keep.py"],
            self.root,
            min_tokens=10,
            sources=[str(self.root / "other" / "notes.txt")],
        )
        self.assertIn("1 files, 0.00% duplicated lines", result.summary)

    def test_missing_source_paths_are_skipped_silently(self):
        self._write("keep.py", _unique_stem("keeper", 1))
        result = scan_files(
            [self.root / "keep.py"],
            self.root,
            min_tokens=10,
            sources=["does/not/exist", "nor_this.py"],
        )
        self.assertIn("1 files, 0.00% duplicated lines", result.summary)

    def test_exclude_glob_removes_file_from_the_scan(self):
        self._write("generated/gen.py", _unique_stem("generated_fn", 1))
        result = scan_files(
            [self.root / "generated" / "gen.py"],
            self.root,
            min_tokens=10,
            excludes=["generated/*"],
        )
        self.assertEqual(result.summary, "no files with enough tokens")

    def test_excluded_copy_is_not_reported_while_kept_one_is(self):
        # keep.py duplicates BLOCK within itself; gen.py (excluded) never
        # enters the scan, so only keep.py is reported.
        self._write("keep.py", _unique_stem("keeper", 1) + BLOCK + BLOCK)
        self._write("generated/gen.py", _unique_stem("gen_fn", 2) + BLOCK)
        result = scan_files(
            [self.root / "keep.py", self.root / "generated" / "gen.py"],
            self.root,
            min_tokens=10,
            min_lines=2,
            excludes=["generated/*"],
        )
        self.assertEqual([v.file for v in result.violations], ["keep.py"])


class TokenizeHelpersTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_comments_and_whitespace_tokens_are_dropped(self):
        path = self.root / "m.py"
        path.write_text("# comment\ndef f(x):\n    return x  # trailing\n", encoding="utf-8")
        source, tokens = _load(path)
        self.assertEqual([t.value for t in tokens], ["def", "f", "(", "x", ")", ":", "return", "x"])
        self.assertEqual(_line_count(source), 3)

    def test_docstrings_are_string_tokens_not_comments(self):
        path = self.root / "m.py"
        path.write_text('"""Doc."""\n\nx = "str"\n', encoding="utf-8")
        _, tokens = _load(path)
        values = [t.value for t in tokens]
        self.assertIn('"""Doc."""', values)
        self.assertIn('"str"', values)

    def test_empty_source_yields_no_tokens_and_zero_lines(self):
        path = self.root / "empty.py"
        path.write_text("", encoding="utf-8")
        source, tokens = _load(path)
        self.assertEqual(tokens, [])
        self.assertEqual(_line_count(source), 0)

    def test_broken_source_warns_and_returns_none(self):
        path = self.root / "bad.py"
        path.write_text("def f(:\n", encoding="utf-8")
        self.assertIsNone(_load(path))


class RunTest(unittest.TestCase):
    """In-process ``run()`` coverage (subprocess runs don't count here)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        alpha, beta = _pair_body()
        (self.root / "alpha.py").write_text(alpha, encoding="utf-8")
        (self.root / "beta.py").write_text(beta, encoding="utf-8")

    def test_run_flags_violations_and_exits_2(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = duplicates.run(["--min-tokens", "10", "--min-lines", "2"], self.root)
        self.assertEqual(code, 2)
        out = stdout.getvalue()
        self.assertIn("alpha.py:1:", out)
        self.assertIn("beta.py:1:", out)
        self.assertIn("2/2 files over 1.0% duplication", out)

    def test_run_passes_unique_files_and_exits_0(self):
        (self.root / "alpha.py").write_text(_unique_stem("first", 1), encoding="utf-8")
        (self.root / "beta.py").write_text(_unique_stem("second", 2), encoding="utf-8")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = duplicates.run([], self.root)
        self.assertEqual(code, 0)
        self.assertIn("2 files, 0.00% duplicated lines", stdout.getvalue())

    def test_positional_paths_select_the_scan_set(self):
        (self.root / "src").mkdir()
        (self.root / "other").mkdir()
        (self.root / "src" / "one.py").write_text(_unique_stem("one", 1) + BLOCK, encoding="utf-8")
        (self.root / "other" / "two.py").write_text(
            _unique_stem("two", 2) + BLOCK, encoding="utf-8"
        )
        clean = io.StringIO()
        with contextlib.redirect_stdout(clean):
            self.assertEqual(duplicates.run(["src"], self.root), 0)
        self.assertIn("1 files, 0.00% duplicated lines", clean.getvalue())
        flagged = io.StringIO()
        with contextlib.redirect_stdout(flagged):
            code = duplicates.run(["--source", "other", "src"], self.root)
        self.assertEqual(code, 2)
        self.assertIn("src/one.py:", flagged.getvalue())
        self.assertIn("other/two.py:", flagged.getvalue())

    def test_min_tokens_flag_reaches_the_detector(self):
        # A shared 40-token window across alpha.py/beta.py: with
        # --min-tokens 20 it is detected, with the 50-token default it is
        # not — the flag demonstrably drives the window size.
        body = (
            "def f(a, b, c, d, e, g, h, i, j, k, l, m):\n    a = b = 0\n    c = d = 1\n"
            "    return a\n"
        )
        (self.root / "alpha.py").write_text(body, encoding="utf-8")
        (self.root / "beta.py").write_text(body, encoding="utf-8")
        with_min = io.StringIO()
        with contextlib.redirect_stdout(with_min):
            code = duplicates.run(["--min-tokens", "20", "--min-lines", "2"], self.root)
        self.assertEqual(code, 2)
        self.assertIn("2/2 files over 1.0% duplication", with_min.getvalue())
        with_defaults = io.StringIO()
        with contextlib.redirect_stdout(with_defaults):
            self.assertEqual(duplicates.run([], self.root), 0)
        # 40-token files sit below the 50-token default: they are skipped
        # from the scan entirely, so the summary is the no-files note.
        self.assertIn("no files with enough tokens", with_defaults.getvalue())

    def test_threshold_flag_round_trips(self):
        # Two identical 2-line functions: exactly 100.00% duplicated lines.
        body = "def f(items):\n    return items\n" + "def g(items):\n    return items\n"
        (self.root / "alpha.py").write_text(body, encoding="utf-8")
        (self.root / "beta.py").write_text(body, encoding="utf-8")
        passes = io.StringIO()
        with contextlib.redirect_stdout(passes):
            self.assertEqual(
                duplicates.run(
                    ["--min-tokens", "5", "--min-lines", "1", "--threshold", "100"], self.root
                ),
                0,
            )
        fails = io.StringIO()
        with contextlib.redirect_stdout(fails):
            self.assertEqual(
                duplicates.run(
                    ["--min-tokens", "5", "--min-lines", "1", "--threshold", "0.01"], self.root
                ),
                2,
            )
        self.assertIn("100.00% duplicated lines > 0.01%", fails.getvalue())

    def test_min_tokens_default_still_detects_large_blocks(self):
        alpha, _ = _pair_body()
        (self.root / "alpha.py").write_text(alpha, encoding="utf-8")
        (self.root / "beta.py").write_text(_pair_body()[1], encoding="utf-8")
        for extra in (["--min-tokens", "20"], []):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(duplicates.run([*extra, "--min-lines", "2"], self.root), 2)
            self.assertIn("2/2 files over 1.0% duplication", out.getvalue())


class DuplicatesCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        alpha, beta = _pair_body()
        (self.root / "alpha.py").write_text(alpha, encoding="utf-8")
        (self.root / "beta.py").write_text(beta, encoding="utf-8")

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "crap4py", "duplicates", *args],
            cwd=str(self.root),
            capture_output=True,
            text=True,
        )

    def test_duplicates_exits_2_and_reports_violations(self):
        r = self._run("--min-tokens", "10", "--min-lines", "2")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("alpha.py:", r.stdout)
        self.assertIn("beta.py:", r.stdout)
        self.assertIn("duplicated lines > 1.0%", r.stdout)
        self.assertIn("2/2 files over 1.0% duplication", r.stdout)

    def test_duplicates_unique_sources_exit_0(self):
        (self.root / "alpha.py").write_text(_unique_stem("first", 1), encoding="utf-8")
        (self.root / "beta.py").write_text(_unique_stem("second", 2), encoding="utf-8")
        r = self._run("--min-tokens", "10")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("2 files, 0.00% duplicated lines", r.stdout)

    def test_duplicates_default_gate_passes_unique_files(self):
        (self.root / "alpha.py").write_text(_unique_stem("first", 1), encoding="utf-8")
        (self.root / "beta.py").write_text(_unique_stem("second", 2), encoding="utf-8")
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_positional_path_selects_scan_set(self):
        (self.root / "src").mkdir()
        (self.root / "other").mkdir()
        (self.root / "src" / "one.py").write_text(_unique_stem("one", 1) + BLOCK, encoding="utf-8")
        (self.root / "other" / "two.py").write_text(
            _unique_stem("two", 2) + BLOCK, encoding="utf-8"
        )
        clean = self._run("src")
        self.assertEqual(clean.returncode, 0, clean.stderr)
        self.assertIn("1 files, 0.00% duplicated lines", clean.stdout)
        flagged = self._run("--source", "other", "src")
        self.assertEqual(flagged.returncode, 2, flagged.stderr)
        self.assertIn("src/one.py:", flagged.stdout)
        self.assertIn("other/two.py:", flagged.stdout)
        self.assertIn("2/2 files over 1.0% duplication", flagged.stdout)

    def test_duplicates_bad_flag_exits_1(self):
        r = self._run("--bogus")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("error", r.stderr)


if __name__ == "__main__":
    unittest.main()
