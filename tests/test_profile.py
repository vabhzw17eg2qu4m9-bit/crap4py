"""Unit + end-to-end tests for the `profile` subcommand."""

import ast
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from crap4py import profile
from crap4py.profile import (
    COLLECTOR_SOURCE,
    attribute,
    collect_timings,
    format_report,
    instrument_source,
)


class InstrumentSourceTest(unittest.TestCase):
    def test_wraps_function_body(self):
        src = "def add(a, b):\n    return a + b\n"
        out = instrument_source(src)
        self.assertIn("__crap_t0 = __crap_pc()", out)
        self.assertIn("finally:", out)
        self.assertIn("__crap_cc.record('add'", out)
        self.assertIn("import _crap_collector", out)
        compile(out, "<instrumented>", "exec")  # must be valid Python

    def test_preserves_plain_source_without_functions(self):
        src = "X = 1\n"
        self.assertEqual(instrument_source(src), src)

    def test_wraps_methods_nested_and_async(self):
        src = (
            "class Foo:\n"
            "    def bar(self):\n"
            "        return 1\n"
            "\n"
            "\n"
            "async def top():\n"
            "    def inner():\n"
            "        return 2\n"
            "    return inner\n"
        )
        out = instrument_source(src)
        compile(out, "<instrumented>", "exec")
        self.assertIn("__crap_cc.record('Foo.bar'", out)
        self.assertIn("__crap_cc.record('top'", out)
        self.assertIn("__crap_cc.record('top.inner'", out)

    def test_yield_and_raise_bodies_still_valid(self):
        src = "def gen():\n    yield 1\n\n\ndef boom():\n    raise ValueError('x')\n"
        compile(instrument_source(src), "<instrumented>", "exec")

    def test_docstring_preserved(self):
        src = 'def f():\n    """Doc."""\n    return 1\n'
        out = instrument_source(src)
        self.assertIn("Doc.", out)
        self.assertLess(out.index("Doc."), out.index("return 1"))

    def test_keys_match_method_inventory(self):
        src = "class Foo:\n    def bar(self):\n        return 1\n"
        instrumented = instrument_source(src)
        keys = [
            node.args[0].value
            for node in ast.walk(ast.parse(instrumented))
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "record"
        ]
        self.assertEqual(keys, ["Foo.bar"])

    def test_imports_after_docstring_and_future(self):
        src = (
            '"""Module doc."""\n\nfrom __future__ import annotations\n\n\ndef f():\n    return 1\n'
        )
        out = instrument_source(src)
        compile(out, "<instrumented>", "exec")
        self.assertIn("from __future__ import annotations", out)
        self.assertLess(out.index('"""Module doc."""'), out.index("__future__"))
        self.assertLess(out.index("__future__"), out.index("_crap_collector"))


class CollectorTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.out = self.root / "timings.json"

    def _collector(self):
        namespace: dict = {"__name__": "_crap_collector_test"}
        exec(COLLECTOR_SOURCE, namespace)  # noqa: S102 - testing generated code
        return namespace

    def test_record_and_flush_aggregate(self):
        os.environ["CRAP_PROFILE_OUTPUT"] = str(self.out)
        self.addCleanup(os.environ.pop, "CRAP_PROFILE_OUTPUT", None)
        ns = self._collector()
        ns["record"]("a", 100.0)
        ns["record"]("a", 300.0)
        ns["record"]("b", 50.0)
        ns["flush"]()
        data = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(data["a"]["calls"], 2)
        self.assertEqual(data["a"]["totalMicros"], 400.0)
        self.assertEqual(data["a"]["minMicros"], 100.0)
        self.assertEqual(data["a"]["maxMicros"], 300.0)
        self.assertEqual(data["b"]["calls"], 1)

    def test_flush_merges_across_instances(self):
        os.environ["CRAP_PROFILE_OUTPUT"] = str(self.out)
        self.addCleanup(os.environ.pop, "CRAP_PROFILE_OUTPUT", None)
        first, second = self._collector(), self._collector()
        first["record"]("a", 100.0)
        first["flush"]()
        second["record"]("a", 400.0)
        second["flush"]()
        data = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(data["a"]["calls"], 2)
        self.assertEqual(data["a"]["totalMicros"], 500.0)
        self.assertEqual(data["a"]["minMicros"], 100.0)
        self.assertEqual(data["a"]["maxMicros"], 400.0)


class AttributeAndReportTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _entry(self, **overrides):
        base = dict(
            method="m",
            file="m.py",
            line=1,
            calls=2,
            total_micros=3000.0,
            min_micros=1000.0,
            max_micros=2000.0,
        )
        base.update(overrides)
        return profile.ProfileEntry(**base)

    def test_attribute_matches_inventory_and_ignores_unknown(self):
        src = Path(self.root) / "calc.py"
        src.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        timings = {"add": {"calls": 3, "totalMicros": 30.0}, "ghost": {"calls": 1}}
        entries = attribute(timings, [src], self.root)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].method, "add")
        self.assertEqual(entries[0].file, "calc.py")
        self.assertEqual(entries[0].line, 1)
        self.assertEqual(entries[0].calls, 3)

    def test_format_report_sorted_with_columns(self):
        entries = [
            self._entry(method="slow", total_micros=9000.0, calls=3),
            self._entry(method="fast", total_micros=1000.0, calls=1),
        ]
        report = format_report(entries, top=20, threshold_ms=None)
        self.assertIn("TOTAL(ms)", report)
        self.assertIn("@60fps(ms)", report)
        self.assertIn("FILE:LINE", report)
        self.assertLess(report.index("slow"), report.index("fast"))
        self.assertIn("2 methods, total 10.00ms", report)

    def test_threshold_lines(self):
        entries = [self._entry(total_micros=5000.0)]
        self.assertIn("1 method exceed", format_report(entries, 20, 1.0))
        self.assertIn("all methods OK", format_report(entries, 20, 10.0))
        self.assertTrue(profile._exceeds_threshold(entries, 1.0))
        self.assertEqual(profile._worst_ms(entries), 5.0)

    def test_top_truncates_console_only(self):
        entries = [self._entry(method=f"m{i}", total_micros=float(i)) for i in range(5)]
        report = format_report(entries, top=2, threshold_ms=None)
        self.assertIn("m4", report)
        self.assertIn("m3", report)
        self.assertNotIn("m2 ", report)


class LoadTimingsTest(unittest.TestCase):
    def test_missing_file_warns_and_returns_empty(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(profile._load_timings(Path("/nonexistent/x.json")), {})
        self.assertIn("no profiling data", err.getvalue())


class PythonpathTest(unittest.TestCase):
    def test_src_layout_appended(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            pp = profile._pythonpath(root)
            self.assertEqual(pp, os.pathsep.join([str(root), str(root / "src")]))


class EndToEndTest(unittest.TestCase):
    """Runs a real instrumented pytest pass over a tiny fixture project."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        test_file = self.root / "tests" / "test_calc.py"
        test_file.parent.mkdir(exist_ok=True)
        test_file.write_text(
            "import unittest\n\nimport calc\n\n\nclass AddTest(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(calc.add(1, 2), 3)\n",
            encoding="utf-8",
        )
        self.addCleanup(self._tmp.cleanup)

    def _run(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = profile.run(list(argv), self.root)
        return code, buf.getvalue()

    def test_profile_run_end_to_end(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("add", out)
        self.assertIn("calc.py:1", out)
        reports = sorted((self.root / "profile-reports").glob("profile-*"))
        self.assertEqual(len(reports), 2)  # .txt + .json
        json_report = next(p for p in reports if p.suffix == ".json")
        data = json.loads(json_report.read_text(encoding="utf-8"))
        self.assertEqual(data["methods"][0]["method"], "add")
        self.assertFalse((self.root / ".crap_profile_temp").exists())

    def test_threshold_exceeded_exits_2(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code, _ = self._run("--threshold", "0.000001")
        self.assertEqual(code, 2)
        self.assertIn("Profile threshold exceeded", err.getvalue())

    def test_no_files_exits_0(self):
        (self.root / "calc.py").unlink()
        (self.root / "tests" / "test_calc.py").unlink()
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("No Python files to profile", out)


class FailingTestsTest(unittest.TestCase):
    """Covers the warning path when the test suite fails in the temp copy."""

    def test_failed_run_warns_and_returns_empty_timings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
            (root / "test_mod.py").write_text(
                "import unittest\n\nimport mod\n\n\nclass T(unittest.TestCase):\n"
                "    def test_fails(self):\n        self.assertEqual(mod.f(), 2)\n",
                encoding="utf-8",
            )
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                timings = collect_timings([root / "mod.py"], root, name=None)
            self.assertIn("Warning:", err.getvalue())
            # The failing test still executed instrumented code and flushed.
            self.assertIn("f", timings)


if __name__ == "__main__":
    unittest.main()
