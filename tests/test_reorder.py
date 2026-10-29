"""Killing tests for the ``reorder`` command (port of crap4dart 24a6f24f).

Covers the contract shapes: the canonical category table (stable partition —
same-kind members keep source order), comment/decorator attachment (one blank
keeps a comment block with its member, two detach it onto the preceding
member — nothing is ever dropped), the corner layout battery (single-line
classes, header/doc lines, CRLF, missing trailing newline, empty and
comment-only files), the never-touch rules (non-member statements, shared
lines), the enum member-section rules (member assignments frozen — value
order is semantics; the section after the last member assignment reorders
like a class body), and the invariants battery (idempotence + token
multiset + parseability over fixtures and the real package).
"""

import ast
import contextlib
import io
import tempfile
import tokenize
import unittest
from pathlib import Path

from crap4py.constants import NO_FILES_MESSAGE
from crap4py.reorder import reorder_source, run

MESSY = """\
class A:
    def m(self):
        pass

    TOP = 1
"""

CANONICAL = """\
class A:
    TOP = 1

    def m(self):
        pass
"""


def sorted_tokens(code: str) -> list[str]:
    """Sorted token stream — reorder moves tokens, never adds or drops them."""
    tokens = tokenize.generate_tokens(io.StringIO(code).readline)
    keep = (tokenize.NL, tokenize.NEWLINE)
    return sorted(f"{t.type}:{t.string}" for t in tokens if t.type not in keep)


def expect_consistent(src: str) -> str:
    """Reorder, assert idempotence and token-multiset preservation, return it."""
    once = reorder_source(src)
    assert reorder_source(once) == once, "must be idempotent"
    assert sorted_tokens(once) == sorted_tokens(src), "token multiset must hold"
    ast.parse(once)
    return once


class CategoryOrderTest(unittest.TestCase):
    def test_full_ladder(self):
        src = (
            "class K:\n"
            "    def _private(self): ...\n"
            "    def public(self): ...\n"
            "    @property\n"
            "    def prop(self): ...\n"
            "    @x.setter\n"
            "    def prop(self, value): ...\n"
            "    @classmethod\n"
            "    def cls_m(cls): ...\n"
            "    @staticmethod\n"
            "    def stat(): ...\n"
            "    def __eq__(self, other): ...\n"
            "    def __init__(self): ...\n"
            "    attr = 1\n"
            "    CONST = 2\n"
        )
        out = expect_consistent(src)
        marks = [
            "CONST = 2",
            "attr = 1",
            "__init__",
            "__eq__",
            "cls_m",
            "stat()",
            "def prop(self):",
            "def public",
            "_private",
        ]
        self.assertEqual([out.index(m) for m in marks], sorted(out.index(m) for m in marks))

    def test_stable_partition_keeps_same_kind_order(self):
        src = "class K:\n    def later(self): ...\n\n    def early(self): ...\n\n    X = 1\n"
        out = expect_consistent(src)
        self.assertLess(out.index("later"), out.index("early"))

    def test_init_leads_other_dunders(self):
        src = "class K:\n    def __repr__(self): ...\n\n    def __init__(self): ...\n"
        out = expect_consistent(src)
        self.assertLess(out.index("__init__"), out.index("__repr__"))

    def test_private_names_include_dunder_externals(self):
        src = "class K:\n    def _helper(self): ...\n\n    X = 1\n"
        out = expect_consistent(src)
        self.assertLess(out.index("X = 1"), out.index("_helper"))

    def test_docstring_stays_first(self):
        src = 'class K:\n    """Docs."""\n\n    def m(self): ...\n\n    X = 1\n'
        out = expect_consistent(src)
        self.assertTrue(out.splitlines()[1].strip().startswith('"""Docs."""'))
        self.assertLess(out.index('"""Docs."""'), out.index("X = 1"))

    def test_single_member_class_untouched(self):
        src = "class K:\n    def m(self): ...\n"
        self.assertEqual(reorder_source(src), src)


class CommentAttachTest(unittest.TestCase):
    def test_comments_move_with_member(self):
        src = "class K:\n    def m(self): ...\n\n    # note on state\n    counter = 0\n"
        out = expect_consistent(src)
        lines = out.splitlines()
        note = lines.index("    # note on state")
        self.assertLess(note, out.index("def m"))
        self.assertEqual(lines[note + 1].strip(), "counter = 0")

    def test_one_blank_line_still_attaches(self):
        src = "class K:\n    def z(self): ...\n\n    # note\n    a = 0\n"
        out = expect_consistent(src)
        self.assertEqual(
            out,
            "class K:\n    # note\n    a = 0\n\n    def z(self): ...\n",
        )

    def test_two_blanks_detach_onto_preceding_member(self):
        src = "class K:\n    def z(self): ...\n\n\n    # orphan section\n\n\n    A = 0\n"
        out = expect_consistent(src)
        # Detached blocks ride with the preceding member — never dropped.
        self.assertEqual(out.count("# orphan section"), 1)
        self.assertLess(out.index("A = 0"), out.index("def z"))
        self.assertGreater(out.index("# orphan section"), out.index("def z"))

    def test_comment_that_looks_like_code_is_carried(self):
        src = "class K:\n    def z(self): ...\n\n    # def fake(self): ...\n    a = 0\n"
        out = expect_consistent(src)
        self.assertIn("# def fake(self): ...\n    a = 0", out)

    def test_decorators_move_with_member(self):
        src = (
            "class K:\n"
            "    def z(self): ...\n"
            "\n"
            "    @staticmethod\n"
            "    def make(): ...\n"
            "\n"
            "    X = 1\n"
        )
        out = expect_consistent(src)
        self.assertIn("X = 1\n\n    @staticmethod\n    def make(): ...", out)

    def test_trailing_same_line_comment_rides_along(self):
        src = "class K:\n    def m(self): ...  # noqa\n\n    X = 1\n"
        out = expect_consistent(src)
        self.assertIn("# noqa", out)


class CornerLayoutTest(unittest.TestCase):
    def test_single_line_class_is_byte_identical(self):
        src = "class A: pass\n"
        self.assertEqual(reorder_source(src), src)

    def test_members_sharing_a_line_are_skipped(self):
        src = "class A:\n    x = 1; y = 2\n"
        self.assertEqual(reorder_source(src), src)

    def test_member_on_header_line_is_skipped(self):
        src = "class A: x = 1\n"
        self.assertEqual(reorder_source(src), src)

    def test_class_doc_above_opener_is_never_stolen(self):
        src = "# Doc for the class.\nclass A:  # trailing\n    def m(self): ...\n\n    X = 1\n"
        out = expect_consistent(src)
        self.assertTrue(out.startswith("# Doc for the class.\nclass A:  # trailing\n    X = 1"))

    def test_orphan_comment_after_body_stays(self):
        src = "class A:\n    X = 1\n\n    # trailing orphan note\n"
        self.assertEqual(reorder_source(src), src)

    def test_crf_line_endings_preserved(self):
        src = "class A:\r\n    def m(self):\r\n        pass\r\n\r\n    X = 1\r\n"
        out = expect_consistent(src)
        self.assertEqual(
            out,
            "class A:\r\n    X = 1\r\n\r\n    def m(self):\r\n        pass\r\n",
        )

    def test_missing_trailing_newline_preserved(self):
        src = "class A:\n    def m(self):\n        pass\n\n    X = 1"
        self.assertEqual(
            reorder_source(src),
            "class A:\n    X = 1\n\n    def m(self):\n        pass",
        )

    def test_empty_file_unchanged(self):
        self.assertEqual(reorder_source(""), "")

    def test_comment_only_file_unchanged(self):
        src = "# just a comment\n"
        self.assertEqual(reorder_source(src), src)

    def test_canonical_keeps_odd_spacing_byte_identical(self):
        src = "class A:\n    X = 1\n\n\n\n    def m(self):\n        pass\n"
        self.assertEqual(reorder_source(src), src)


class SafetyTest(unittest.TestCase):
    def test_memberless_enum_subclasses_untouched(self):
        # No member assignments → nothing to freeze, no member section.
        for base in ("Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"):
            src = f"from enum import {base}\n\nclass C({base}):\n    B = 1\n    A = 2\n"
            self.assertEqual(reorder_source(src), src, base)
        src = "import enum\n\nclass C(enum.IntFlag):\n    B = 1\n    A = 2\n"
        self.assertEqual(reorder_source(src), src)

    def test_non_member_statements_skip_the_class(self):
        src = "class A:\n    if True:\n        x = 1\n\n    y = 2\n"
        self.assertEqual(reorder_source(src), src)

    def test_class_level_reference_skips_the_class(self):
        src = "class A:\n    x = 1\n\n    class B: ...\n\n    y = x\n"
        self.assertEqual(reorder_source(src), src)

    def test_nested_class_inner_fixed_outer_untouched(self):
        src = (
            "class Outer:\n"
            "    class Inner:\n"
            "        def m(self): ...\n"
            "\n"
            "        X = 1\n"
            "\n"
            "    def run(self): ...\n"
            "\n"
            "    N = 0\n"
        )
        out = expect_consistent(src)
        self.assertLess(out.index("X = 1"), out.index("def m(self): ..."))
        self.assertGreater(out.index("N = 0"), out.index("def run(self): ..."))

    def test_method_local_class_fixed_and_outer_reordered(self):
        src = (
            "class A:\n"
            "    def make(self):\n"
            "        class B:\n"
            "            def m(self): ...\n"
            "\n"
            "            Y = 2\n"
            "        return B\n"
            "\n"
            "    Z = 1\n"
        )
        out = expect_consistent(src)
        self.assertLess(out.index("Z = 1"), out.index("def make"))
        self.assertLess(out.index("Y = 2"), out.index("def m(self): ..."))

    def test_multiple_classes_each_reordered(self):
        src = (
            "class A:\n    def m(self): ...\n\n    X = 1\n\n"
            "class B:\n    def m(self): ...\n\n    Y = 2\n"
        )
        out = expect_consistent(src)
        head, tail = out.split("class B:")
        self.assertIn("X = 1", head)
        self.assertLess(head.index("X = 1"), head.index("def m"))
        self.assertLess(tail.index("Y = 2"), tail.index("def m"))

    def test_is_a_pure_function(self):
        src = "class A:\n    def m(self): ...\n\n    X = 1\n"
        self.assertEqual(reorder_source(src), reorder_source(src))


def member_names(code: str) -> list[str]:
    """The enum member names of the first class in [code], source order —
    the iteration order the transform must never change."""
    cls = next(n for n in ast.parse(code).body if isinstance(n, ast.ClassDef))
    names: list[str] = []
    for stmt in cls.body:
        if isinstance(stmt, ast.Assign):
            names.extend(t.id for t in stmt.targets if isinstance(t, ast.Name))
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            names.append(stmt.target.id)
    return names


class EnumMemberSectionTest(unittest.TestCase):
    """Enums: member assignments stay frozen (value order is semantics);
    the section after the LAST member assignment reorders like a class body
    (adapted from crap4dart f323c0b, spec 11.24)."""

    MESSY = (
        "from enum import Enum\n"
        "\n"
        "\n"
        "class Color(Enum):\n"
        "    RED = 1\n"
        "    GREEN = 2\n"
        "\n"
        "    def z_plain(self):\n"
        "        return list(Color)\n"
        "\n"
        "    @staticmethod\n"
        "    def parse(raw):\n"
        "        return Color(int(raw))\n"
        "\n"
        "    label: str\n"
        "\n"
        "    def __new__(cls, value):\n"
        "        obj = object.__new__(cls)\n"
        "        obj._value_ = value\n"
        "        return obj\n"
        "\n"
        "    @property\n"
        "    def upper(self):\n"
        "        return self.name.upper()\n"
    )

    def test_member_section_reorders_into_canonical_order(self):
        out = expect_consistent(self.MESSY)
        self.assertLess(out.index("label: str"), out.index("__new__"))
        self.assertLess(out.index("__new__"), out.index("def parse"))
        self.assertLess(out.index("def parse"), out.index("def upper"))
        self.assertLess(out.index("def upper"), out.index("def z_plain"))

    def test_constants_block_is_byte_exact_across_reorders(self):
        out = reorder_source(self.MESSY)
        self.assertIn("    RED = 1\n    GREEN = 2\n", out)
        head, _, _ = out.partition("    label: str")
        self.assertTrue(head.rstrip().endswith("GREEN = 2"))

    def test_constants_order_is_preserved_exactly(self):
        self.assertEqual(member_names(self.MESSY), member_names(expect_consistent(self.MESSY)))

    def test_iteration_semantics_survive(self):
        before, after = {}, {}
        exec(self.MESSY, before)
        exec(reorder_source(self.MESSY), after)
        original, reordered = before["Color"], after["Color"]
        # value order (the constants block) is preserved exactly
        self.assertEqual([m.name for m in reordered], [m.name for m in original])
        self.assertEqual([m.name for m in reordered], ["RED", "GREEN"])
        # the moved member section still behaves: value lookup, static
        # method, property, and method bodies are all verbatim moves.
        self.assertEqual(reordered(2).name, "GREEN")
        self.assertEqual(reordered.parse("1").name, "RED")
        self.assertEqual(reordered.GREEN.upper, "GREEN")
        self.assertEqual([m.name for m in reordered.GREEN.z_plain()], ["RED", "GREEN"])

    def test_comment_above_enum_member_moves_with_it(self):
        src = (
            "class C(Enum):\n"
            "    A = 1\n"
            "\n"
            "    def z(self): ...\n"
            "\n"
            "    # the label\n"
            "    @property\n"
            "    def upper(self): ...\n"
        )
        self.assertEqual(
            expect_consistent(src),
            "class C(Enum):\n"
            "    A = 1\n"
            "\n"
            "    # the label\n"
            "    @property\n"
            "    def upper(self): ...\n"
            "\n"
            "    def z(self): ...\n",
        )

    def test_docstring_stays_first_and_frozen(self):
        src = (
            "class C(Enum):\n"
            '    """Docs."""\n'
            "\n"
            "    A = 1\n"
            "\n"
            "    def z(self): ...\n"
            "\n"
            "    label: str\n"
            "\n"
            "    def a(self): ...\n"
        )
        self.assertEqual(
            expect_consistent(src),
            "class C(Enum):\n"
            '    """Docs."""\n'
            "\n"
            "    A = 1\n"
            "\n"
            "    label: str\n"
            "\n"
            "    def z(self): ...\n"
            "\n"
            "    def a(self): ...\n",
        )

    def test_canonical_member_section_byte_identical(self):
        src = (
            "class C(Enum):\n"
            "    RED = 1\n"
            "\n"
            "    label: str\n"
            "\n"
            "    def __new__(cls, value):\n"
            "        return object.__new__(cls)\n"
        )
        self.assertEqual(reorder_source(src), src)

    def test_frozen_block_keeps_odd_spacing(self):
        src = "class C(Enum):\n    B = 1\n\n\n\n    A = 2\n"
        self.assertEqual(reorder_source(src), src)

    def test_interleaved_member_freezes_through_last_assignment(self):
        # A member after code freezes everything up to and including it;
        # a single-statement tail leaves the class untouched.
        src = (
            "class C(Enum):\n"
            "    A = 1\n"
            "\n"
            "    def z(self): ...\n"
            "\n"
            "    B = 2\n"
            "\n"
            "    def a(self): ...\n"
        )
        self.assertEqual(reorder_source(src), src)

    def test_interleaved_member_bounds_the_reorder_span(self):
        src = (
            "class C(Enum):\n"
            "    A = 1\n"
            "\n"
            "    def z(self): ...\n"
            "\n"
            "    B = 2\n"
            "\n"
            "    def tail_public(self): ...\n"
            "\n"
            "    label: str\n"
        )
        out = expect_consistent(src)
        # Frozen prefix untouched (z before B — never swapped); tail reorders.
        self.assertLess(out.index("def z"), out.index("B = 2"))
        self.assertLess(out.index("label: str"), out.index("def tail_public"))
        self.assertIn("    A = 1\n", out)
        self.assertIn("    B = 2\n", out)

    def test_methods_only_enum_is_an_ordinary_member_section(self):
        src = "class Mix(Enum):\n    def _private(self): ...\n\n    def public(self): ...\n"
        self.assertEqual(
            expect_consistent(src),
            "class Mix(Enum):\n    def public(self): ...\n\n    def _private(self): ...\n",
        )

    def test_dotted_enum_base_reorders_member_section(self):
        src = (
            "import enum\n"
            "\n"
            "class C(enum.IntFlag):\n"
            "    B = 1\n"
            "    A = 2\n"
            "\n"
            "    def z(self): ...\n"
            "\n"
            "    def _hidden(self): ...\n"
        )
        out = expect_consistent(src)
        self.assertLess(out.index("def z"), out.index("def _hidden"))
        self.assertEqual(member_names(src), ["B", "A"])
        self.assertEqual(member_names(out), ["B", "A"])

    def test_non_member_tail_skips_the_class(self):
        src = (
            "class C(Enum):\n"
            "    A = 1\n"
            "\n"
            "    if True:\n"
            "        x = 1\n"
            "\n"
            "    def z(self): ...\n"
            "\n"
            "    def a(self): ...\n"
        )
        self.assertEqual(reorder_source(src), src)


FIXTURES = {
    "kitchen-sink": (
        '"""A documented module."""\n'
        "\n"
        "\n"
        "class Kitchen:\n"
        '    """The kitchen."""\n'
        "\n"
        "    # note on state\n"
        "    counter = 0\n"
        "\n"
        "    ANSWER = 42\n"
        "\n"
        "    width: int = 0\n"
        "\n"
        "    def _draw_quietly(self): ...\n"
        "\n"
        "    def draw(self): ...\n"
        "\n"
        "    @staticmethod\n"
        "    def build(): ...\n"
        "\n"
        "    @property\n"
        "    def area(self): ...\n"
        "\n"
        "    def __repr__(self): ...\n"
        "\n"
        "    def __init__(self):\n"
        "        self.width = 0\n"
    ),
    "comments-everywhere": (
        "# leading file comment\n"
        "class A:\n"
        "    # one\n"
        "    X = 0\n"
        "\n"
        "    def b(self): ...\n"
        "\n"
        "    def c(self): ...\n"
        "\n"
        "    # before d\n"
        "    def d(self): ...\n"
        "\n"
        "\n"
        "# between classes\n"
        "\n"
        "class B:\n"
        "    def x(self): ...\n"
        "\n"
        "    Y = 0\n"
    ),
    "mixed-order-repeat": (
        "class C:\n"
        "    def later(self): ...\n"
        "\n"
        "    middle = 0\n"
        "\n"
        "    FIRST = 1\n"
        "\n"
        "    def _last(self): ...\n"
        "\n"
        "    middle2 = 'two'\n"
        "\n"
        "    SECOND = 2\n"
    ),
    "enum-member-section": (
        "from enum import Enum\n"
        "\n"
        "\n"
        "class Color(Enum):\n"
        "    RED = 1\n"
        "    GREEN = 2\n"
        "\n"
        "    def z_plain(self): ...\n"
        "\n"
        "    @staticmethod\n"
        "    def parse(raw): ...\n"
        "\n"
        "    label: str\n"
    ),
}


class InvariantsTest(unittest.TestCase):
    def test_fixture_battery(self):
        for name, src in FIXTURES.items():
            with self.subTest(fixture=name):
                expect_consistent(src)

    def test_real_package_files(self):
        package = Path(__file__).resolve().parents[1] / "crap4py"
        files = sorted(package.glob("*.py"))
        self.assertGreater(len(files), 10)
        for path in files:
            with self.subTest(file=path.name):
                src = path.read_text(encoding="utf-8")
                once = reorder_source(src)
                twice = reorder_source(once)
                self.assertEqual(twice, once, "repo-scale idempotence")
                ast.parse(once)
                self.assertEqual(
                    sorted_tokens(once), sorted_tokens(src), "token multiset must hold"
                )


class CliTest(unittest.TestCase):
    def _project(self, files: dict[str, str]) -> tempfile.TemporaryDirectory:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for name, text in files.items():
            path = Path(tmp.name, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return tmp

    def _run(self, argv: list[str], root: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = run(argv, Path(root))
        return code, buffer.getvalue()

    def test_writes_reordered_files_and_exits_0(self):
        with self._project({"pkg/a.py": MESSY}) as tmp:
            code, out = self._run(["pkg"], tmp)
            self.assertEqual(code, 0, out)
            self.assertEqual(Path(tmp, "pkg/a.py").read_text(encoding="utf-8"), CANONICAL)
            self.assertIn("reordered pkg/a.py", out)
            self.assertIn("1 of 1 files reordered.", out)

    def test_dry_run_reports_and_exits_2_without_writing(self):
        with self._project({"pkg/a.py": MESSY}) as tmp:
            code, out = self._run(["--dry-run", "pkg"], tmp)
            self.assertEqual(code, 2, out)
            self.assertEqual(Path(tmp, "pkg/a.py").read_text(encoding="utf-8"), MESSY)
            self.assertIn("would reorder pkg/a.py", out)
            self.assertIn("would be reordered.", out)

    def test_canonical_files_exit_0_in_both_modes(self):
        with self._project({"pkg/a.py": CANONICAL}) as tmp:
            code, out = self._run(["pkg"], tmp)
            self.assertEqual(code, 0, out)
            code, out = self._run(["--dry-run", "pkg"], tmp)
            self.assertEqual(code, 0, out)
            self.assertIn("All 1 files already canonical.", out)

    def test_parse_errors_skipped_not_fatal(self):
        with self._project({"pkg/broken.py": "class Broken {", "pkg/a.py": MESSY}) as tmp:
            code, out = self._run(["pkg"], tmp)
            self.assertEqual(code, 0, out)
            self.assertIn("skipped (parse error): pkg/broken.py", out)
            self.assertEqual(Path(tmp, "pkg/a.py").read_text(encoding="utf-8"), CANONICAL)

    def test_explicit_path_targets_only_that_subtree(self):
        with self._project({"lib/a.py": MESSY, "pkg/b.py": MESSY}) as tmp:
            code, out = self._run(["pkg"], tmp)
            self.assertEqual(code, 0, out)
            self.assertEqual(Path(tmp, "pkg/b.py").read_text(encoding="utf-8"), CANONICAL)
            self.assertEqual(Path(tmp, "lib/a.py").read_text(encoding="utf-8"), MESSY)

    def test_empty_selection_prints_message(self):
        with self._project({}) as tmp:
            code, out = self._run([], tmp)
            self.assertEqual(code, 0)
            self.assertEqual(out.strip(), NO_FILES_MESSAGE)


if __name__ == "__main__":
    unittest.main()
