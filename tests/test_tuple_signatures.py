"""Unit + CLI tests for the `tuple-signatures` subcommand."""

import ast
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from crap4py.tuple_signatures import (
    MAX_FIELDS,
    _signature_violations,
    check_files,
    run,
    summary,
)


def _parse(source: str):
    return ast.parse(textwrap.dedent(source))


def _violations(source: str):
    return _signature_violations(_parse(source), "mod.py", MAX_FIELDS)[0]


def _messages(source: str):
    return [v.message for v in _violations(source)]


class SignatureViolationTest(unittest.TestCase):
    def test_tuple_param_and_return_flagged(self):
        vs = _violations(
            """
            def f(x: tuple[int, str]) -> tuple[int, int]:
                return x
            """
        )
        self.assertEqual(len(vs), 2)
        self.assertEqual(
            [v.message for v in vs],
            [
                "inline tuple type 'tuple[int, str]' in signature — declare the shape "
                "as a NamedTuple/dataclass and reference it by name",
                "inline tuple type 'tuple[int, int]' in signature — declare the shape "
                "as a NamedTuple/dataclass and reference it by name",
            ],
        )

    def test_typing_tuple_flagged(self):
        msgs = _messages(
            """
            import typing

            def f() -> typing.Tuple[int, int]:
                return (1, 2)
            """
        )
        self.assertEqual(len(msgs), 1)
        self.assertIn("typing.Tuple[int, int]", msgs[0])

    def test_bare_imported_tuple_flagged(self):
        msgs = _messages(
            """
            from typing import Tuple

            def f() -> Tuple[int, int]:
                return (1, 2)
            """
        )
        self.assertEqual(len(msgs), 1)
        self.assertIn("Tuple[int, int]", msgs[0])

    def test_tuple_nested_in_type_args_flagged(self):
        # Killing test: dropping the nested type-arg descent makes this fail.
        vs = _violations(
            """
            def f(
                d: dict[str, tuple[int, int]],
                o: Optional[tuple[str, str]],
                xs: list[tuple[int, int]],
                u: tuple[int, int] | None,
                c: Callable[[tuple[int, int]], str],
            ) -> dict[str, tuple[int, int]]:
                return d
            """
        )
        self.assertEqual(len(vs), 6)

    def test_outermost_shape_of_nested_group_reported_once(self):
        msgs = _messages(
            """
            def f(x: tuple[tuple[int, int], tuple[str, str]]) -> None:
                pass
            """
        )
        self.assertEqual(len(msgs), 1)
        self.assertIn("tuple[tuple[int, int], tuple[str, str]]", msgs[0])

    def test_variadic_nested_in_type_args_not_flagged(self):
        vs = _violations(
            """
            def f(xs: list[tuple[int, ...]], m: dict[str, tuple[str, ...]]) -> None:
                pass
            """
        )
        self.assertEqual(vs, [])

    def test_variadic_tuple_not_flagged(self):
        # Killing test: dropping the Ellipsis exclusion makes this fail.
        vs = _violations(
            """
            def f(xs: tuple[int, ...]) -> tuple[str, ...]:
                return xs
            """
        )
        self.assertEqual(vs, [])

    def test_tuple_single_element_not_flagged(self):
        # `tuple[int]` has a plain-name slice, not a tuple shape.
        vs = _violations(
            """
            def f(x: tuple[int]) -> tuple[int]:
                return x
            """
        )
        self.assertEqual(vs, [])

    def test_named_types_not_flagged(self):
        # Killing test (blessed pin): named shapes are the blessed form.
        vs = _violations(
            """
            class Point(NamedTuple):
                x: float
                y: float

            def f(p: Point) -> Point:
                return p
            """
        )
        self.assertEqual(vs, [])

    def test_namedtuple_and_typeddict_body_annotations_not_flagged(self):
        vs = _violations(
            """
            class Pair(NamedTuple):
                point: tuple[float, float]

            class Row(TypedDict):
                cells: tuple[int, int]

            @dataclass
            class Box:
                edges: tuple[int, int]
            """
        )
        self.assertEqual(vs, [])

    def test_unannotated_signature_not_flagged(self):
        vs = _violations(
            """
            def f(x, y):
                return x, y
            """
        )
        self.assertEqual(vs, [])

    def test_method_signature_flagged(self):
        vs = _violations(
            """
            class Grid:
                def cell(self) -> tuple[int, int]:
                    return 0, 0
            """
        )
        self.assertEqual(len(vs), 1)
        self.assertEqual(vs[0].line, 3)

    def test_async_and_nested_functions_flagged(self):
        vs = _violations(
            """
            async def f() -> tuple[int, int]:
                def g(v: tuple[int, int]) -> tuple[int, int]:
                    return v
                return g((1, 2))
            """
        )
        self.assertEqual(len(vs), 3)

    def test_vararg_and_kwonly_annotations_flagged(self):
        vs = _violations(
            """
            def f(*rest: tuple[int, int], kw: tuple[int, int] = (0, 0)) -> None:
                pass
            """
        )
        self.assertEqual(len(vs), 2)

    def test_max_fields_note_on_over_cap_shape(self):
        # Killing test: `>` → `>=` off-by-one makes this fail.
        six = "def f() -> tuple[int, int, int, int, int, int]: ..."
        msgs = _messages(six)
        self.assertEqual(len(msgs), 1)
        self.assertIn("6 fields > max 5", msgs[0])

        five = "def f() -> tuple[int, int, int, int, int]: ..."
        self.assertNotIn("5 fields > max 5", _messages(five)[0])

    def test_max_fields_option_moves_the_cap(self):
        six = "def f() -> tuple[int, int, int, int, int, int]: ..."
        vs = _violations(six)
        relaxed = _signature_violations(_parse(six), "mod.py", 6)[0]
        self.assertEqual(len(vs), 1)
        self.assertEqual(len(relaxed), 1)
        self.assertNotIn("> max", relaxed[0].message)
        self.assertIn("> max 5", vs[0].message)


class CheckFilesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _write(self, rel, source):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(source), encoding="utf-8")
        return p

    def test_violation_and_summary(self):
        self._write(
            "shapes.py",
            """
            def f(x: tuple[int, int]) -> tuple[int, int]:
                return x
            """,
        )
        result = check_files([self.root / "shapes.py"], self.root)
        self.assertEqual(len(result.violations), 2)
        self.assertEqual(result.violations[0].file, "shapes.py")
        self.assertEqual(result.checked, 1)
        self.assertEqual(summary(result), "2 inline tuple shapes in signatures")

    def test_test_files_skipped(self):
        self._write(
            "test_shapes.py",
            """
            def f(x: tuple[int, int]) -> None:
                pass
            """,
        )
        result = check_files([self.root / "test_shapes.py"], self.root)
        self.assertEqual((result.violations, result.checked), ([], 0))

    def test_broken_file_skipped(self):
        p = self.root / "broken.py"
        p.write_text("def f(:\n", encoding="utf-8")
        result = check_files([p], self.root)
        self.assertEqual((result.violations, result.checked), ([], 0))

    def test_clean_summary(self):
        result = check_files([], self.root)
        self.assertEqual(summary(result), "0 signatures checked, no inline tuple shapes")


class RunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_no_files_exits_0(self):
        self.assertEqual(run([], self.root), 0)

    def test_violations_exit_2(self):
        (self.root / "shapes.py").write_text(
            "def f(x: tuple[int, int]) -> tuple[int, int]:\n    return x\n",
            encoding="utf-8",
        )
        self.assertEqual(run([], self.root), 2)

    def test_explicit_path_and_max_fields_flag(self):
        (self.root / "shapes.py").write_text(
            "def f() -> tuple[int, int, int, int, int, int]: ...\n",
            encoding="utf-8",
        )
        self.assertEqual(run(["shapes.py"], self.root), 2)
        self.assertEqual(run(["shapes.py", "--max-fields", "6"], self.root), 2)


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
        r = self._run("tuple-signatures", "--bogus")
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_clean_file_exits_0(self):
        (self.root / "ok.py").write_text(
            "def f(p: Point) -> Point:\n    return p\n",
            encoding="utf-8",
        )
        r = self._run("tuple-signatures")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no inline tuple shapes", r.stdout)

    def test_tuple_shape_exits_2(self):
        (self.root / "shapes.py").write_text(
            "def f(x: tuple[int, int]) -> None:\n    pass\n",
            encoding="utf-8",
        )
        r = self._run("tuple-signatures")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("tuple[int, int]", r.stdout)
        self.assertIn("shapes.py:1", r.stdout)


if __name__ == "__main__":
    unittest.main()
