"""Tests for the ``similarity`` subcommand (crap4dart 2b21966 port).

Fixture sizes are measured, not decorative: every cross-file expectation
asserts the shingle counts first, so a fixture that normalizes below the
``min_shingles`` floor can never pass vacuously (a skipped file never
enters comparison). 6/12 lines of 10 words = 60/120 tokens = 53/113
shingles — above the tested floors of 40 and the 50 default.
"""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from crap4py.similarity import (
    CODE,
    MARKDOWN,
    RAW,
    SimilarityConfig,
    analyze,
    containment,
    intersection_count,
    jaccard,
    normalize_code,
    normalize_markdown,
    profile_for,
    run,
    shingles_for,
    tokens_for,
)


def _prose(tag: str, lines: int) -> str:
    """Deterministic unique prose: ``lines`` lines of 10 distinct words."""
    return (
        "\n".join(" ".join(f"{tag}word{i:02d}{j:02d}" for j in range(10)) for i in range(lines))
        + "\n"
    )


def _write(root: Path, name: str, content: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class ProfileTest(unittest.TestCase):
    def test_markdown_extensions(self):
        for ext in (".md", ".markdown", ".mdx", ".MD"):
            self.assertEqual(profile_for(f"doc{ext}"), MARKDOWN)

    def test_code_extensions(self):
        for ext in (".py", ".ts", ".dart", ".go", ".sh", ".sql"):
            self.assertEqual(profile_for(f"src/file{ext}"), CODE)

    def test_unknown_extensions_are_raw(self):
        for name in ("notes.txt", "Makefile", "cfg.yaml", "noext"):
            self.assertEqual(profile_for(name), RAW)


class MarkdownNormalizationTest(unittest.TestCase):
    def test_fenced_code_is_dropped(self):
        content = (
            "# Title\n\n```python\nsecret = 1\n```\n\nProse line here.\n~~~\nalso fenced\n~~~\n"
        )
        normalized = normalize_markdown(content)
        self.assertEqual(normalized, "# Title Prose line here.")

    def test_fence_toggle_survives_unterminated_block(self):
        normalized = normalize_markdown("before\n```\nhidden\n")
        self.assertEqual(normalized, "before")

    def test_fence_only_differences_still_cluster(self):
        """Killing fixture for dropping markdown normalization: the two
        files share all prose and differ only inside fences — dropping the
        fences makes them identical (Jaccard 1.0, 113 shingles each), while
        keeping fence words would leave ~22 divergent tokens and Jaccard
        far below the 0.8 threshold."""
        prose = _prose("fence", 12)
        alpha = prose + "```python\n" + _prose("alfa", 1)
        beta = prose + "```python\n" + _prose("beta", 1)
        for shingles in (shingles_for(alpha, MARKDOWN), shingles_for(beta, MARKDOWN)):
            self.assertGreaterEqual(len(shingles), 50)
        root = Path(tempfile.mkdtemp())
        self.addCleanup(_cleanup, root)
        _write(root, "a.md", alpha)
        _write(root, "b.md", beta)
        report = analyze(
            root,
            dirs=["."],
            include=["**/*.md"],
            exclude=(),
            jaccard_threshold=0.8,
            containment_threshold=0.95,
            min_shingles=50,
        )
        self.assertEqual(report.scanned, 2)
        self.assertEqual(report.skipped, 0)
        self.assertEqual(len(report.pairs), 1)
        self.assertEqual(report.pairs[0].jaccard, 1.0)


class CodeNormalizationTest(unittest.TestCase):
    def test_line_comments_and_imports_are_dropped(self):
        source = "\n".join(
            (
                "// dart comment",
                "# python comment",
                "-- sql comment",
                "; ini comment",
                "import os",
                "from pathlib import Path",
                "require('fs')",
                "#include <stdio.h>",
                "using System;",
                "package main",
                "#!/usr/bin/env bash",
                "value = compute(item)",
            )
        )
        self.assertEqual(normalize_code(source), "value = compute(item)")

    def test_block_comments_strip_across_lines(self):
        source = "keep_one\n/* start\nspans lines\nend */ keep_two\n/* open"
        self.assertEqual(normalize_code(source), "keep_one\nkeep_two")

    def test_generic_literals_map_to_placeholders(self):
        tokens = tokens_for(normalize_code('alpha(arg, 42, "tag");'), CODE)
        self.assertEqual(tokens, ["alpha", "arg", "N", "S"])


class PythonTokenizerTest(unittest.TestCase):
    def test_names_lowercase_numbers_and_strings_are_placeholders(self):
        tokens = tokens_for('Foo = bar(42) + "text"\n', CODE, "mod.py")
        self.assertEqual(tokens, ["foo", "bar", "N", "S"])

    def test_comments_are_dropped(self):
        tokens = tokens_for("x = 1  # note\n# whole line\n", CODE, "m.py")
        self.assertEqual(tokens, ["x", "N"])

    def test_import_and_from_import_lines_are_dropped(self):
        source = "import os\nx = 1\nfrom typing import (\n    Any,\n    Callable,\n)\ny = 2\n"
        tokens = tokens_for(source, CODE, "m.py")
        self.assertEqual(tokens, ["x", "N", "y", "N"])

    def test_fstring_is_one_opaque_placeholder(self):
        tokens = tokens_for('msg = f"hi {name}!"\n', CODE, "m.py")
        self.assertEqual(tokens, ["msg", "S"])

    def test_broken_python_falls_back_to_generic_engine(self):
        tokens = tokens_for("def broken(:\n", CODE, "m.py")
        self.assertEqual(tokens, ["def", "broken"])

    def test_py_literals_normalize_to_identical_streams(self):
        """Killing fixture for dropping literal placeholders (Python path):
        structurally identical files differing only in literals — Jaccard
        1.0 with placeholders, 0.0 without (4 tokens per line, 73
        shingles over the 20 distinct lines)."""
        alpha = "".join(f'func_{i}(arg_{i}, {i * 10 + 1}, "alpha_{i}")\n' for i in range(20))
        beta = "".join(f'func_{i}(arg_{i}, {i * 99 + 5}, "beta_{i}")\n' for i in range(20))
        with_placeholders = (shingles_for(alpha, CODE, "a.py"), shingles_for(beta, CODE, "b.py"))
        for shingles in with_placeholders:
            self.assertGreaterEqual(len(shingles), 40)
        self.assertEqual(with_placeholders[0], with_placeholders[1])
        root = Path(tempfile.mkdtemp())
        self.addCleanup(_cleanup, root)
        _write(root, "a.py", alpha)
        _write(root, "b.py", beta)
        report = analyze(
            root,
            dirs=["."],
            include=["**/*.py"],
            exclude=(),
            jaccard_threshold=0.8,
            containment_threshold=0.95,
            min_shingles=40,
        )
        self.assertEqual(report.scanned, 2)
        self.assertEqual(len(report.pairs), 1)
        self.assertEqual(report.pairs[0].jaccard, 1.0)


class ShingleTest(unittest.TestCase):
    def test_below_one_shingle_is_empty(self):
        self.assertEqual(shingles_for("only seven tokens here now ok", MARKDOWN), frozenset())

    def test_eight_tokens_make_exactly_one_shingle(self):
        self.assertEqual(len(shingles_for("one two three four five six seven eight", MARKDOWN)), 1)

    def test_nine_tokens_make_two_shingles(self):
        text = "one two three four five six seven eight nine"
        self.assertEqual(len(shingles_for(text, MARKDOWN)), 2)

    def test_identical_content_yields_equal_sets(self):
        content = _prose("same", 6)
        self.assertEqual(shingles_for(content, MARKDOWN), shingles_for(content, MARKDOWN))

    def test_word_tokens_are_lowercase_runs(self):
        self.assertEqual(tokens_for("Alpha BETA gamma42", RAW), ["alpha", "beta", "gamma42"])


class MetricTest(unittest.TestCase):
    def setUp(self):
        self.set_a = shingles_for(_prose("met", 6), MARKDOWN)
        self.set_b = shingles_for(_prose("met", 6), MARKDOWN)
        self.other = shingles_for(_prose("oth", 6), MARKDOWN)

    def test_identical_sets(self):
        self.assertEqual(jaccard(self.set_a, self.set_b), 1.0)
        self.assertEqual(containment(self.set_a, self.set_b), 1.0)
        self.assertEqual(intersection_count(self.set_a, self.set_b), 53)

    def test_disjoint_sets(self):
        self.assertEqual(jaccard(self.set_a, self.other), 0.0)
        self.assertEqual(containment(self.set_a, self.other), 0.0)

    def test_empty_sets(self):
        empty = frozenset()
        self.assertEqual(jaccard(empty, self.set_a), 0.0)
        self.assertEqual(containment(self.set_a, empty), 0.0)


class ClusteringTest(unittest.TestCase):
    def test_pairs_merge_transitively(self):
        """A ⊂ B and C ⊂ B (containment pairs) while A and C are disjoint:
        one cluster of three, two pairs. Killing fixture for breaking
        union-find transitivity — without union every file is its own
        cluster and the report is clean-ish (no cluster at all)."""
        small_a, small_c = _prose("tra", 6), _prose("trc", 6)
        big_b = _prose("tra", 6) + _prose("trc", 6)
        for shingles, floor in (
            (shingles_for(small_a, MARKDOWN), 40),
            (shingles_for(small_c, MARKDOWN), 40),
            (shingles_for(big_b, MARKDOWN), 90),
        ):
            self.assertGreaterEqual(len(shingles), floor)
        root = Path(tempfile.mkdtemp())
        self.addCleanup(_cleanup, root)
        _write(root, "a.md", small_a)
        _write(root, "b.md", big_b)
        _write(root, "c.md", small_c)
        report = analyze(
            root,
            dirs=["."],
            include=["**/*.md"],
            exclude=(),
            jaccard_threshold=0.8,
            containment_threshold=0.95,
            min_shingles=40,
        )
        self.assertEqual(report.scanned, 3)
        self.assertEqual(len(report.pairs), 2)
        self.assertEqual(len(report.clusters), 1)
        cluster = report.clusters[0]
        self.assertEqual(cluster.files, ("a.md", "b.md", "c.md"))
        self.assertEqual(
            {(pair.a, pair.b) for pair in cluster.pairs}, {("a.md", "b.md"), ("b.md", "c.md")}
        )
        # b contains both small files entirely; a and c are disjoint.
        # Jaccard(a, b) = 53 shared / 113 shingles; containment 53/53.
        self.assertEqual(report.metrics_for("a.md"), (53 / 113, 1.0))
        self.assertEqual(report.metrics_for("c.md"), (53 / 113, 1.0))
        self.assertEqual(report.metrics_for("b.md"), (53 / 113, 1.0))

    def test_containment_only_pair_crosses(self):
        """Small file fully inside a big one: Jaccard stays low (13/113),
        containment is 1.0 — the pair must still be reported. Killing
        fixture for swapping the jaccard/containment metrics or dropping
        the containment criterion."""
        big = _prose("big", 12)
        small = "\n".join(big.split("\n")[3:5]) + "\n"  # lines 4-5 of big
        self.assertEqual(len(shingles_for(small, MARKDOWN)), 13)
        root = Path(tempfile.mkdtemp())
        self.addCleanup(_cleanup, root)
        _write(root, "big.md", big)
        _write(root, "small.md", small)
        report = analyze(
            root,
            dirs=["."],
            include=["**/*.md"],
            exclude=(),
            jaccard_threshold=0.8,
            containment_threshold=0.95,
            min_shingles=10,
        )
        self.assertEqual(report.scanned, 2)
        self.assertEqual(len(report.pairs), 1)
        pair = report.pairs[0]
        self.assertEqual((pair.a, pair.b), ("big.md", "small.md"))
        self.assertLess(pair.jaccard, 0.2)
        self.assertEqual(pair.containment, 1.0)
        self.assertEqual(report.clusters[0].files, ("big.md", "small.md"))

    def test_below_both_thresholds_is_clean(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(_cleanup, root)
        _write(root, "a.md", _prose("one", 12))
        _write(root, "b.md", _prose("two", 12))
        report = analyze(
            root,
            dirs=["."],
            include=["**/*.md"],
            exclude=(),
            jaccard_threshold=0.8,
            containment_threshold=0.95,
            min_shingles=50,
        )
        self.assertTrue(report.clean)
        self.assertEqual(report.clusters, ())

    def test_disjoint_files_never_cluster(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(_cleanup, root)
        _write(root, "a.md", _prose("aaa", 12))
        _write(root, "b.md", _prose("bbb", 12))
        _write(root, "c.md", _prose("ccc", 12))
        report = analyze(
            root,
            dirs=["."],
            include=["**/*.md"],
            exclude=(),
            jaccard_threshold=0.8,
            containment_threshold=0.95,
            min_shingles=50,
        )
        self.assertEqual(report.scanned, 3)
        self.assertTrue(report.clean)


class AnalyzerTest(unittest.TestCase):
    def test_small_files_are_skipped_and_never_compared(self):
        big = _prose("skip", 12)
        tiny = " ".join(big.split()[:5]) + "\n"  # 5 tokens -> 0 shingles
        root = Path(tempfile.mkdtemp())
        self.addCleanup(_cleanup, root)
        _write(root, "big.md", big)
        _write(root, "tiny.md", tiny)
        report = analyze(
            root,
            dirs=["."],
            include=["**/*.md"],
            exclude=(),
            jaccard_threshold=0.8,
            containment_threshold=0.95,
            min_shingles=50,
        )
        self.assertEqual((report.scanned, report.skipped), (1, 1))
        self.assertTrue(report.clean)

    def test_include_and_exclude_globs(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(_cleanup, root)
        _write(root, "docs/a.md", _prose("inc", 12))
        _write(root, "b.txt", _prose("txt", 12))
        _write(root, "gen/c.md", _prose("gen", 12))
        kept = analyze(
            root,
            dirs=["."],
            include=["**/*.md"],
            exclude=["gen/**"],
            jaccard_threshold=0.8,
            containment_threshold=0.95,
            min_shingles=50,
        )
        self.assertEqual(kept.scanned, 1)
        self.assertEqual(kept.skipped, 0)
        texts = analyze(
            root,
            dirs=["."],
            include=["*.txt"],
            exclude=(),
            jaccard_threshold=0.8,
            containment_threshold=0.95,
            min_shingles=50,
        )
        self.assertEqual(texts.scanned, 1)

    def test_missing_dir_is_skipped_silently(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(_cleanup, root)
        _write(root, "a.md", _prose("mis", 12))
        report = analyze(
            root,
            dirs=["nope", "."],
            include=["**/*.md"],
            exclude=(),
            jaccard_threshold=0.8,
            containment_threshold=0.95,
            min_shingles=50,
        )
        self.assertEqual(report.scanned, 1)

    def test_pairs_sorted_strongest_jaccard_first(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(_cleanup, root)
        _write(root, "a.md", _prose("dup", 12))
        _write(root, "b.md", _prose("dup", 12))
        big = _prose("ord", 12)
        _write(root, "big.md", big)
        _write(root, "small.md", "\n".join(big.split("\n")[3:5]) + "\n")
        report = analyze(
            root,
            dirs=["."],
            include=["**/*.md"],
            exclude=(),
            jaccard_threshold=0.8,
            containment_threshold=0.95,
            min_shingles=10,
        )
        self.assertEqual(len(report.pairs), 2)
        self.assertEqual(report.pairs[0].jaccard, 1.0)
        self.assertLess(report.pairs[1].jaccard, 0.2)


class ConfigTest(unittest.TestCase):
    def test_defaults_match_upstream(self):
        config = SimilarityConfig()
        self.assertEqual(config.min_shingles, 50)
        self.assertEqual(config.jaccard, 0.8)
        self.assertEqual(config.containment, 0.95)
        self.assertEqual(config.dirs, (".",))
        self.assertEqual(config.include, ("**/*.md",))
        self.assertFalse(hasattr(config, "enabled"))  # opt-in by nature: no config file

    def test_out_of_range_thresholds_are_rejected(self):
        for kwargs in (
            {"jaccard": 1.5},
            {"jaccard": -0.1},
            {"containment": 1.01},
            {"containment": -1.0},
            {"min_shingles": 0},
            {"format": "yaml"},
        ):
            with self.assertRaises(ValueError):
                SimilarityConfig(**kwargs).validate()

    def test_valid_config_passes(self):
        SimilarityConfig(jaccard=0.5, containment=0.9, min_shingles=1).validate()


class RunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _write(self, name: str, content: str) -> None:
        _write(self.root, name, content)

    def _run(self, *argv: str) -> tuple[int, str]:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = run(list(argv), self.root)
        return code, stdout.getvalue()

    def _twin_files(self) -> None:
        self._write("a.md", _prose("twin", 12))
        self._write("b.md", _prose("twin", 12))

    def test_violations_still_exit_0(self):
        self._twin_files()
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("2 files in 1 clusters", out)

    def test_console_report_contents(self):
        self._twin_files()
        _, out = self._run()
        self.assertIn(
            "similarity: 2 files scanned (0 skipped below 50 shingles), "
            "thresholds jaccard >= 0.8 / containment >= 0.95",
            out,
        )
        self.assertIn("cluster 1 — 2 files:", out)
        self.assertIn("a.md (jaccard 1.00, containment 1.00)", out)
        self.assertTrue(out.rstrip().endswith("2 files in 1 clusters"))

    def test_clean_console_report(self):
        self._write("a.md", _prose("solo", 12))
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("1 files scanned (0 skipped below 50 shingles)", out)
        self.assertIn("no similar files", out)

    def test_json_report(self):
        self._twin_files()
        code, out = self._run("--format", "json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["scanned"], 2)
        self.assertEqual(payload["skipped"], 0)
        self.assertEqual(
            payload["thresholds"], {"jaccard": 0.8, "containment": 0.95, "min_shingles": 50}
        )
        self.assertEqual(payload["pairs"][0]["jaccard"], 1.0)
        self.assertEqual(payload["clusters"], [{"files": ["a.md", "b.md"]}])

    def test_threshold_overrides_are_echoed(self):
        self._twin_files()
        _, out = self._run(
            "--format", "json", "--jaccard", "0.5", "--containment", "0.9", "--min-shingles", "10"
        )
        payload = json.loads(out)
        self.assertEqual(
            payload["thresholds"], {"jaccard": 0.5, "containment": 0.9, "min_shingles": 10}
        )

    def test_include_exclude_and_dir_overrides(self):
        self._twin_files()
        self._write("other/c.md", _prose("twin", 12))
        _, out = self._run("--exclude", "other/**")
        self.assertIn("2 files scanned", out)
        _, out = self._run("other")
        self.assertIn("1 files scanned", out)
        _, out = self._run("--include", "*.md")
        self.assertIn("3 files scanned", out)

    def test_default_excludes_generated_and_vcs_dirs(self):
        self._write("a.md", _prose("exc", 12))
        self._write(".git/a.md", _prose("exc", 12))
        self._write(".venv/a.md", _prose("exc", 12))
        _, out = self._run()
        self.assertIn("1 files scanned", out)

    def test_invalid_values_are_usage_errors(self):
        self._twin_files()
        for argv in (
            ["--jaccard", "1.5"],
            ["--jaccard", "abc"],
            ["--containment", "-1"],
            ["--min-shingles", "0"],
            ["--min-shingles", "x"],
            ["--format", "yaml"],
        ):
            with self.assertRaises(SystemExit) as ctx:
                self._run(*argv)
            self.assertEqual(ctx.exception.code, 1)

    def test_subprocess_end_to_end_exits_0(self):
        self._twin_files()
        result = subprocess.run(
            [sys.executable, "-m", "crap4py", "similarity", str(self.root)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("2 files in 1 clusters", result.stdout)


def _cleanup(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        else:
            path.rmdir()
    root.rmdir()


if __name__ == "__main__":
    unittest.main()
