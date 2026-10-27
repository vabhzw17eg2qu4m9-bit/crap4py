"""Killing tests for the ignore-marker system (port of crap4dart 8abb64c).

Covers the contract shapes: granular single-id markers (three-violation
line), stacked whole-line markers above a violation, file scope in the
first 5 lines, and the grammar corners (comma inert, unknown id noop,
empty-id blanket, legacy parity, non-ignorable opt-in).
"""

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from crap4py.ignore_filter import IGNORABLE_GATES, filter_ignored
from crap4py.magic_constants import run as magic_run
from crap4py.scattered_constants import run as scattered_run
from crap4py.unused_code import run as unused_code_run
from crap4py.unused_files import run as unused_files_run

SCATTER_LINE = "_v = 0xFF00FF00"


@dataclass(frozen=True, slots=True)
class _FakeViolation:
    """Duck-violation for direct filter_ignored tests."""

    file: str
    line: int | None


@dataclass(frozen=True, slots=True)
class _FakeResult:
    violations: list


def _project(files: dict[str, str]):
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
    return tmp, root


def _stdout(gate_run, argv, root) -> tuple[str, int]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = gate_run(argv, root)
    return buffer.getvalue(), code


def _three_file_project():
    return {
        "a.py": f"{SCATTER_LINE}  # ignore:unused_code\n",
        "b.py": f"{SCATTER_LINE}\n",
        "c.py": f"{SCATTER_LINE}\n",
    }


class GranularLineScopeTest(unittest.TestCase):
    """Shape (a): one marker on a multi-gate line silences only its rule."""

    def test_named_gate_suppressed_others_fire(self):
        tmp, root = _project(_three_file_project())
        self.addCleanup(tmp.cleanup)
        unused, code = _stdout(unused_code_run, [], root)
        self.assertNotIn("a.py:", unused, "the named rule is suppressed")
        self.assertIn("b.py:", unused, "unmarked files of the same rule still fire")
        self.assertEqual(code, 2)
        magic, code = _stdout(magic_run, [], root)
        self.assertIn("a.py:1:", magic, "the unnamed rule on the same line still fires")
        self.assertEqual(code, 2)
        scattered, code = _stdout(scattered_run, [], root)
        self.assertIn("a.py:1:", scattered, "the third rule still fires too")
        self.assertEqual(code, 2)

    def test_trailing_marker_on_the_code_line_counts(self):
        tmp, root = _project({"a.py": f"{SCATTER_LINE}  # ignore:magic_constants\n"})
        self.addCleanup(tmp.cleanup)
        out, code = _stdout(magic_run, ["a.py"], root)
        self.assertEqual(code, 0)
        self.assertNotIn("a.py:1:", out)


class StackedMarkersTest(unittest.TestCase):
    """Shape (b): the run of whole-line markers above a violation applies."""

    def test_stacked_whole_line_markers_all_apply(self):
        tmp, root = _project(
            {"d.py": (f"# ignore:magic_constants\n# ignore:unused_code\n{SCATTER_LINE}\n")}
        )
        self.addCleanup(tmp.cleanup)
        magic, _ = _stdout(magic_run, ["d.py"], root)
        self.assertNotIn("d.py:3:", magic)
        unused, _ = _stdout(unused_code_run, [], root)
        self.assertNotIn("d.py:3:", unused)

    def test_non_marker_line_stops_the_walk(self):
        tmp, root = _project(
            {"d.py": (f"# ignore:magic_constants\n# a plain comment\n{SCATTER_LINE}\n")}
        )
        self.addCleanup(tmp.cleanup)
        magic, code = _stdout(magic_run, ["d.py"], root)
        self.assertIn("d.py:3:", magic)
        self.assertEqual(code, 2)

    def test_no_space_marker_directly_above_still_suppresses(self):
        # The granular regex allows "#ignore:" (upstream "//ignore:"): a
        # no-space marker names its gate on the line directly above.
        tmp, root = _project({"d.py": f"#ignore:magic_constants\n{SCATTER_LINE}\n"})
        self.addCleanup(tmp.cleanup)
        magic, _ = _stdout(magic_run, ["d.py"], root)
        self.assertNotIn("d.py:2:", magic)

    def test_no_space_marker_line_does_not_continue_the_run(self):
        # Whole-line continuation needs the exact "# ignore:" spelling
        # (upstream quirk kept verbatim): a "#ignore:" line naming another
        # gate ends the stacked run — the walk does not look past it.
        tmp, root = _project({"d.py": f"#ignore:unused_code\n{SCATTER_LINE}\n"})
        self.addCleanup(tmp.cleanup)
        magic, _ = _stdout(magic_run, ["d.py"], root)
        self.assertIn("d.py:2:", magic)


class FileScopeTest(unittest.TestCase):
    """Shape (c): first-5-lines file markers, union, blanket."""

    def test_ignore_file_suppresses_named_gate_file_wide(self):
        tmp, root = _project({"f.py": "# ignore-file:unused_code\n\n\n\n" + SCATTER_LINE + "\n"})
        self.addCleanup(tmp.cleanup)
        unused, code = _stdout(unused_code_run, ["f.py"], root)
        self.assertNotIn("f.py:5:", unused)
        self.assertEqual(code, 0)
        magic, _ = _stdout(magic_run, ["f.py"], root)
        self.assertIn("f.py:5:", magic)

    def test_ignore_file_markers_on_separate_lines_union(self):
        tmp, root = _project(
            {
                "f.py": (
                    f"# ignore-file:magic_constants\n# ignore-file:unused_code\n{SCATTER_LINE}\n"
                )
            }
        )
        self.addCleanup(tmp.cleanup)
        magic, _ = _stdout(magic_run, ["f.py"], root)
        self.assertNotIn("f.py:3:", magic)
        unused, _ = _stdout(unused_code_run, [], root)
        self.assertNotIn("f.py:3:", unused)

    def test_bare_ignore_file_is_a_blanket(self):
        tmp, root = _project({"f.py": "# ignore-file:\n" + SCATTER_LINE + "\n"})
        self.addCleanup(tmp.cleanup)
        magic, _ = _stdout(magic_run, ["f.py"], root)
        self.assertNotIn("f.py:2:", magic)
        unused, _ = _stdout(unused_code_run, [], root)
        self.assertNotIn("f.py:2:", unused)

    def test_file_marker_on_line_six_is_inert(self):
        # The scan reads lines 1-5 only; a marker ON line 6 does nothing.
        tmp, root = _project(
            {"f.py": "\n" * 5 + "# ignore-file:magic_constants\n" + SCATTER_LINE + "\n"}
        )
        self.addCleanup(tmp.cleanup)
        magic, code = _stdout(magic_run, ["f.py"], root)
        self.assertIn("f.py:7:", magic)
        self.assertEqual(code, 2)

    def test_legacy_file_marker_keeps_blanket_scope(self):
        tmp, root = _project({"f.py": "# crap:ignore-file\n" + SCATTER_LINE + "\n"})
        self.addCleanup(tmp.cleanup)
        magic, _ = _stdout(magic_run, ["f.py"], root)
        self.assertNotIn("f.py:2:", magic)
        unused, _ = _stdout(unused_code_run, [], root)
        self.assertNotIn("f.py:2:", unused)

    def test_no_line_violations_suppressed_by_file_marker_only(self):
        # unused-files violations are bare paths (no line number): only the
        # file-scoped markers can silence them.
        marked, root_m = _project({"lone.py": "# ignore-file:unused_files\nx = 1\n"})
        self.addCleanup(marked.cleanup)
        out, code = _stdout(unused_files_run, [], root_m)
        self.assertEqual(code, 0)
        self.assertNotIn("lone.py", out)
        plain, root_p = _project({"lone.py": "x = 1\n"})
        self.addCleanup(plain.cleanup)
        out, code = _stdout(unused_files_run, [], root_p)
        self.assertEqual(code, 2)
        self.assertIn("lone.py", out)


class GrammarTest(unittest.TestCase):
    """Shape (d): comma inert, unknown noop, legacy parity, opt-in."""

    def test_comma_list_is_not_a_list_it_names_nothing(self):
        tmp, root = _project({"g.py": f"{SCATTER_LINE}  # ignore:magic_constants,unused_code\n"})
        self.addCleanup(tmp.cleanup)
        magic, _ = _stdout(magic_run, ["g.py"], root)
        self.assertIn("g.py:1:", magic)
        unused, _ = _stdout(unused_code_run, [], root)
        self.assertIn("g.py:1:", unused)

    def test_unknown_gate_id_matches_nothing(self):
        tmp, root = _project({"g.py": f"{SCATTER_LINE}  # ignore:magic_constants_typo\n"})
        self.addCleanup(tmp.cleanup)
        magic, code = _stdout(magic_run, ["g.py"], root)
        self.assertIn("g.py:1:", magic)
        self.assertEqual(code, 2)

    def test_bare_ignore_line_suppresses_every_ignorable_gate(self):
        tmp, root = _project({"g.py": f"{SCATTER_LINE}  # ignore:\n"})
        self.addCleanup(tmp.cleanup)
        magic, _ = _stdout(magic_run, ["g.py"], root)
        self.assertNotIn("g.py:1:", magic)
        unused, _ = _stdout(unused_code_run, [], root)
        self.assertNotIn("g.py:1:", unused)

    def test_legacy_line_marker_keeps_blanket_scope(self):
        tmp, root = _project({"g.py": f"{SCATTER_LINE}  # crap:ignore\n"})
        self.addCleanup(tmp.cleanup)
        magic, _ = _stdout(magic_run, ["g.py"], root)
        self.assertNotIn("g.py:1:", magic)
        unused, _ = _stdout(unused_code_run, [], root)
        self.assertNotIn("g.py:1:", unused)

    def test_legacy_file_marker_line_also_carries_line_scope(self):
        # Upstream substring quirk: "crap:ignore-file" contains the line
        # marker "crap:ignore", so it suppresses the following line too.
        tmp, root = _project({"g.py": f"# crap:ignore-file\n{SCATTER_LINE}\n"})
        self.addCleanup(tmp.cleanup)
        magic, _ = _stdout(magic_run, ["g.py"], root)
        self.assertNotIn("g.py:2:", magic)


class SharedPathUnitTest(unittest.TestCase):
    """Direct filter_ignored tests for the shared violation path."""

    def test_non_ignorable_gate_is_never_suppressed(self):
        tmp, root = _project({"h.py": "# crap:ignore-file\n# ignore-file:\nx = 1\n"})
        self.addCleanup(tmp.cleanup)
        result = _FakeResult([_FakeViolation("h.py", 3)])
        filtered = filter_ignored("folder_structure", result, root)
        self.assertEqual(filtered.violations, result.violations)

    def test_filter_returns_same_object_when_nothing_matched(self):
        tmp, root = _project({"h.py": "x = 1\n"})
        self.addCleanup(tmp.cleanup)
        result = _FakeResult([_FakeViolation("h.py", 1)])
        self.assertIs(filter_ignored("magic_constants", result, root), result)

    def test_filter_drops_only_the_matching_gate(self):
        tmp, root = _project({"h.py": f"{SCATTER_LINE}  # ignore:magic_constants\nx = 1\ny = 2\n"})
        self.addCleanup(tmp.cleanup)
        result = _FakeResult(
            [
                _FakeViolation("h.py", 1),  # marked line
                _FakeViolation("h.py", 3),  # past the walk (line 2 unmarked)
            ]
        )
        filtered = filter_ignored("magic_constants", result, root)
        self.assertEqual(len(filtered.violations), 1)
        self.assertEqual(filtered.violations[0].line, 3)
        self.assertIn("magic_constants", IGNORABLE_GATES)
        self.assertNotIn("folder_structure", IGNORABLE_GATES)

    def test_line_beyond_eof_and_unreadable_files_never_suppress(self):
        tmp, root = _project({"h.py": "# ignore:magic_constants\n"})
        self.addCleanup(tmp.cleanup)
        result = _FakeResult([_FakeViolation("h.py", 99)])
        filtered = filter_ignored("magic_constants", result, root)
        self.assertEqual(len(filtered.violations), 1)
        missing = _FakeResult([_FakeViolation("nope.py", 1)])
        filtered = filter_ignored("magic_constants", missing, root)
        self.assertEqual(len(filtered.violations), 1)

    def test_empty_result_returns_same_object(self):
        tmp, root = _project({"h.py": "x = 1\n"})
        self.addCleanup(tmp.cleanup)
        result = _FakeResult([])
        self.assertIs(filter_ignored("magic_constants", result, root), result)


class CliSubprocessTest(unittest.TestCase):
    """End-to-end: the marker flips the CLI exit code."""

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

    def test_marker_suppression_flips_exit_code(self):
        source = f"{SCATTER_LINE}\n"
        (self.root / "m.py").write_text(source, encoding="utf-8")
        self.assertEqual(self._run("magic-constants").returncode, 2)
        (self.root / "m.py").write_text(
            f"{SCATTER_LINE}  # ignore:magic_constants\n", encoding="utf-8"
        )
        r = self._run("magic-constants")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("m.py:1:", r.stdout)


if __name__ == "__main__":
    unittest.main()
