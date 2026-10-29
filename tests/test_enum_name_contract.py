"""Unit + CLI tests for the `enum-name-contract` subcommand.

Killing tests per receiver class (self, implicit self, constants, loop
variables, enum-annotated parameters) plus the guards (own-``name``
shadowing, locals/params shadowing, static/classmethod bodies, non-read
positions, outside-enum-body exclusion, ``.value`` exemption) and the
severity/ignore wiring.
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

from crap4py.enum_name_contract import check_files, file_violations, run, summary


def _violations(source: str) -> list[tuple[int, str]]:
    source = textwrap.dedent(source)
    tree = ast.parse(source)
    return [(v.line, v.message) for v in file_violations(tree, "x.py")]


class SelfReceiverTest(unittest.TestCase):
    def test_self_name_flagged(self):
        found = _violations(
            """
            from enum import Enum

            class Color(Enum):
                RED = 1

                def label(self):
                    return self.name
            """
        )
        self.assertEqual([line for line, _ in found], [8])
        self.assertIn("'Color'", found[0][1])

    def test_self_value_never_flagged(self):
        self.assertEqual(
            _violations(
                """
                from enum import Enum

                class Color(Enum):
                    RED = 1

                    def label(self):
                        return self.value
                """
            ),
            [],
        )


class ImplicitSelfTest(unittest.TestCase):
    def test_bare_name_read_flagged(self):
        found = _violations(
            """
            from enum import Enum

            class Color(Enum):
                RED = 1

                def label(self):
                    return name
            """
        )
        self.assertEqual([line for line, _ in found], [8])

    def test_property_getter_bare_name_flagged(self):
        found = _violations(
            """
            from enum import Enum

            class Color(Enum):
                RED = 1

                @property
                def label(self):
                    return name
            """
        )
        self.assertEqual([line for line, _ in found], [9])

    def test_hint_names_explicit_value_field(self):
        ((_, message),) = _violations(
            """
            from enum import Enum

            class Color(Enum):
                RED = 1

                def label(self):
                    return name
            """
        )
        self.assertIn("'.value'", message)


class ConstantReceiverTest(unittest.TestCase):
    def test_bare_and_qualified_constants_in_static_methods(self):
        found = _violations(
            """
            from enum import Enum

            class Color(Enum):
                RED = 1
                BLUE = 2

                @staticmethod
                def of_red():
                    return RED.name

                @staticmethod
                def of_blue():
                    return Color.BLUE.name
            """
        )
        self.assertEqual([line for line, _ in found], [10, 14])

    def test_constants_inside_instance_methods(self):
        found = _violations(
            """
            from enum import Enum

            class Color(Enum):
                RED = 1

                def pick(self):
                    return RED.name
            """
        )
        self.assertEqual([line for line, _ in found], [8])

    def test_qualified_constant_on_value_not_flagged(self):
        self.assertEqual(
            _violations(
                """
                from enum import Enum

                class Color(Enum):
                    RED = 1

                    @staticmethod
                    def of_red():
                        return Color.RED.value
                """
            ),
            [],
        )


class LoopVariableTest(unittest.TestCase):
    def test_iteration_forms_flagged(self):
        found = _violations(
            """
            from enum import Enum

            class Color(Enum):
                RED = 1
                BLUE = 2

                def loop(self):
                    out = []
                    for c in Color:
                        out.append(c.name)
                    for k in Color.__members__:
                        out.append(k.name)
                    for c in list(Color):
                        out.append(c.name)
                    return out

                def comp(self):
                    return [c.name for c in Color]
            """
        )
        self.assertEqual([line for line, _ in found], [11, 13, 15, 19])

    def test_non_enum_loop_var_not_flagged(self):
        self.assertEqual(
            _violations(
                """
                from enum import Enum

                class Color(Enum):
                    RED = 1

                    def loop(self, rows):
                        out = []
                        for row in rows:
                            out.append(row.name)
                        return out
                """
            ),
            [],
        )


class AnnotatedParameterTest(unittest.TestCase):
    def test_enum_annotated_parameter_flagged(self):
        found = _violations(
            """
            from enum import Enum

            class Color(Enum):
                RED = 1

                @classmethod
                def parse(cls, c: Color):
                    return c.name
            """
        )
        self.assertEqual([line for line, _ in found], [9])

    def test_string_annotation_flagged(self):
        found = _violations(
            """
            from enum import Enum

            class Color(Enum):
                RED = 1

                @classmethod
                def parse(cls, c: "Color"):
                    return c.name
            """
        )
        self.assertEqual([line for line, _ in found], [9])

    def test_qualified_annotation_flagged(self):
        found = _violations(
            """
            from enum import Enum

            class Color(Enum):
                RED = 1

                @classmethod
                def parse(cls, c: enum.Color):
                    return c.name
            """
        )
        self.assertEqual([line for line, _ in found], [9])

    def test_nested_def_enum_annotated_param_flagged(self):
        found = _violations(
            """
            from enum import Enum

            class Color(Enum):
                RED = 1

                def outer(self):
                    def inner(c: Color):
                        return c.name
                    return inner
            """
        )
        self.assertEqual([line for line, _ in found], [9])

    def test_other_annotation_not_flagged(self):
        self.assertEqual(
            _violations(
                """
                from enum import Enum

                class Color(Enum):
                    RED = 1

                    @classmethod
                    def parse(cls, c: str):
                        return c.name
                """
            ),
            [],
        )

    def test_optional_annotation_not_flagged(self):
        self.assertEqual(
            _violations(
                """
                from enum import Enum

                class Color(Enum):
                    RED = 1

                    @classmethod
                    def parse(cls, c: Color | None):
                        return c.name
                """
            ),
            [],
        )


class ShadowGuardTest(unittest.TestCase):
    def test_own_name_assignment_shadows_builtin(self):
        self.assertEqual(
            _violations(
                """
                from enum import Enum

                class Color(Enum):
                    RED = 1
                    name = "shadow"

                    def label(self):
                        return self.name

                    @staticmethod
                    def of_red():
                        return RED.name

                    def bare(self):
                        return name
                """
            ),
            [],
        )

    def test_own_name_property_shadows_builtin(self):
        self.assertEqual(
            _violations(
                """
                from enum import Enum

                class Color(Enum):
                    RED = 1

                    @property
                    def name(self):
                        return "x"

                    def label(self):
                        return self.name
                """
            ),
            [],
        )

    def test_own_name_annotated_assignment_shadows_builtin(self):
        self.assertEqual(
            _violations(
                """
                from enum import Enum

                class Color(Enum):
                    RED = 1
                    name: str = "shadow"

                    def label(self):
                        return self.name
                """
            ),
            [],
        )

    def test_dotted_enum_base_audited(self):
        found = _violations(
            """
            import enum

            class Color(enum.Enum):
                RED = 1

                def label(self):
                    return self.name
            """
        )
        self.assertEqual([line for line, _ in found], [8])

    def test_non_name_receiver_not_flagged(self):
        self.assertEqual(
            _violations(
                """
                from enum import Enum

                class Color(Enum):
                    RED = 1

                    def label(self):
                        return f().name
                """
            ),
            [],
        )

    def test_non_enum_iterables_make_no_enum_locals(self):
        self.assertEqual(
            _violations(
                """
                from enum import Enum

                class Color(Enum):
                    RED = 1

                    def gather(self, rows):
                        out = []
                        for row in rows:
                            out.append(row.name)
                        for row in list(rows):
                            out.append(row.name)
                        for part in self.parts:
                            out.append(part.name)
                        return out
                """
            ),
            [],
        )

    def test_local_shadow_beats_implicit_self(self):
        self.assertEqual(
            _violations(
                """
                from enum import Enum

                class Color(Enum):
                    RED = 1

                    def label(self):
                        name = "x"
                        return name
                """
            ),
            [],
        )

    def test_parameter_shadow_beats_implicit_self(self):
        self.assertEqual(
            _violations(
                """
                from enum import Enum

                class Color(Enum):
                    RED = 1

                    def echo(self, name):
                        return name
                """
            ),
            [],
        )

    def test_comprehension_var_shadow(self):
        self.assertEqual(
            _violations(
                """
                from enum import Enum

                class Color(Enum):
                    RED = 1

                    def gather(self):
                        return [name for name in ("x",)]
                """
            ),
            [],
        )


class StaticScopeTest(unittest.TestCase):
    def test_staticmethod_bare_name_not_implicit_self(self):
        self.assertEqual(
            _violations(
                """
                name = "global"

                from enum import Enum

                class Color(Enum):
                    RED = 1

                    @staticmethod
                    def top():
                        return name
                """
            ),
            [],
        )

    def test_classmethod_bare_name_not_implicit_self(self):
        self.assertEqual(
            _violations(
                """
                from enum import Enum

                class Color(Enum):
                    RED = 1

                    @classmethod
                    def top(cls):
                        return name
                """
            ),
            [],
        )

    def test_staticmethod_self_receiver_not_flagged(self):
        self.assertEqual(
            _violations(
                """
                from enum import Enum

                class Color(Enum):
                    RED = 1

                    @staticmethod
                    def top():
                        return self.name
                """
            ),
            [],
        )


class NonReadPositionTest(unittest.TestCase):
    def test_invocation_position_not_a_read(self):
        self.assertEqual(
            _violations(
                """
                from enum import Enum

                class Color(Enum):
                    RED = 1

                    def set_label(self):
                        self.name = "x"

                    def call_positions(self):
                        self.name()
                        return Color.RED.name()
                """
            ),
            [],
        )

    def test_bare_and_static_invocations_not_reads(self):
        self.assertEqual(
            _violations(
                """
                from enum import Enum

                class Color(Enum):
                    RED = 1

                    @staticmethod
                    def call_positions():
                        return (RED.name(), name())
                """
            ),
            [],
        )

    def test_assignment_position_not_a_read(self):
        self.assertEqual(
            _violations(
                """
                from enum import Enum

                class Color(Enum):
                    RED = 1

                    def set_label(self):
                        self.name = "x"

                    def call_position(self):
                        return name()
                """
            ),
            [],
        )

    def test_store_context_bare_name_creates_local(self):
        self.assertEqual(
            _violations(
                """
                from enum import Enum

                class Color(Enum):
                    RED = 1

                    def bind(self):
                        name = "x"
                        return name
                """
            ),
            [],
        )


class ScopeExclusionTest(unittest.TestCase):
    def test_usage_outside_enum_body_not_flagged(self):
        self.assertEqual(
            _violations(
                """
                from enum import Enum

                class Color(Enum):
                    RED = 1

                def outside(c):
                    return Color.RED.name
                """
            ),
            [],
        )

    def test_non_enum_class_body_not_flagged(self):
        self.assertEqual(
            _violations(
                """
                class Plain:
                    def label(self):
                        return self.name
                """
            ),
            [],
        )

    def test_transitive_in_file_enum_subclass_audited(self):
        found = _violations(
            """
            from enum import Enum

            class Color(Enum):
                RED = 1

            class Extended(Color):
                PINK = 2

                def label(self):
                    return self.name
            """
        )
        self.assertEqual([line for line, _ in found], [11])

    def test_enum_subclass_of_str_enum_audited(self):
        found = _violations(
            """
            from enum import StrEnum

            class Color(StrEnum):
                RED = "red"

                def label(self):
                    return self.name
            """
        )
        self.assertEqual([line for line, _ in found], [8])

    def test_nested_enum_class_audited(self):
        found = _violations(
            """
            from enum import Enum

            class Outer:
                class Color(Enum):
                    RED = 1

                    def label(self):
                        return self.name
            """
        )
        self.assertEqual([line for line, _ in found], [9])


class CheckFilesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_broken_file_skipped_and_test_file_excluded(self):
        (self.root / "broken.py").write_text("def f(:\n", encoding="utf-8")
        (self.root / "test_x.py").write_text(
            "from enum import Enum\n"
            "class C(Enum):\n"
            "    R = 1\n"
            "    def f(self):\n"
            "        return self.name\n",
            encoding="utf-8",
        )
        (self.root / "app.py").write_text(
            "from enum import Enum\n"
            "class C(Enum):\n"
            "    R = 1\n"
            "    def f(self):\n"
            "        return self.name\n",
            encoding="utf-8",
        )
        result = check_files(
            [self.root / n for n in ("broken.py", "test_x.py", "app.py")], self.root
        )
        self.assertEqual(result.checked, 1)
        self.assertEqual([v.line for v in result.violations], [5])
        self.assertEqual(result.violations[0].file, "app.py")


class RunTest(unittest.TestCase):
    ENUM = (
        "from enum import Enum\n"
        "class Color(Enum):\n"
        "    RED = 1\n"
        "    def label(self):\n"
        "        return self.name\n"
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_no_files_exits_0(self):
        self.assertEqual(run([], self.root), 0)

    def test_warning_severity_default_reports_without_failing(self):
        (self.root / "app.py").write_text(self.ENUM, encoding="utf-8")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = run([], self.root)
        self.assertEqual(code, 0)
        self.assertIn("[WARN]", buffer.getvalue())
        self.assertIn("1 enum name contract(s) in 1 files", buffer.getvalue())

    def test_error_severity_exits_2(self):
        (self.root / "app.py").write_text(self.ENUM, encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            code = run(["--severity", "error"], self.root)
        self.assertEqual(code, 2)

    def test_error_severity_clean_file_exits_0(self):
        (self.root / "app.py").write_text(
            "from enum import Enum\n"
            "class Color(Enum):\n"
            "    RED = 1\n"
            "    def label(self):\n"
            "        return self.value\n",
            encoding="utf-8",
        )
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = run(["--severity", "error"], self.root)
        self.assertEqual(code, 0)
        self.assertIn("1 files free of enum name contracts", buffer.getvalue())

    def test_ignore_marker_suppresses(self):
        (self.root / "app.py").write_text(
            self.ENUM.replace(
                "        return self.name", "        return self.name  # ignore:enum_name_contract"
            ),
            encoding="utf-8",
        )
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = run([], self.root)
        self.assertEqual(code, 0)
        self.assertIn("1 files free of enum name contracts", buffer.getvalue())


class SummaryTest(unittest.TestCase):
    def test_summary(self):
        from crap4py.enum_name_contract import EnumNameContractHazard, EnumNameContractsResult

        self.assertEqual(
            summary(EnumNameContractsResult([], 3)), "3 files free of enum name contracts"
        )
        found = EnumNameContractsResult([EnumNameContractHazard("a.py", 1, "m")], 3)
        self.assertEqual(summary(found), "1 enum name contract(s) in 3 files")


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
        r = self._run("enum-name-contract", "--bogus")
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_warning_output_and_exit_0(self):
        (self.root / "app.py").write_text(
            "from enum import Enum\n"
            "class Color(Enum):\n"
            "    RED = 1\n"
            "    def label(self):\n"
            "        return self.name\n",
            encoding="utf-8",
        )
        r = self._run("enum-name-contract")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[WARN] app.py:5: enum 'Color' contracts on the built-in '.name'", r.stdout)

    def test_dispatched_from_main_cli(self):
        r = self._run("enum-name-contract", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("crap4py enum-name-contract", r.stdout)


if __name__ == "__main__":
    unittest.main()
