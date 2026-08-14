"""Tests for the `skill` subcommand (in-process + CLI dispatch)."""

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from crap4py import skill


class SkillRunTest(unittest.TestCase):
    def test_prints_skill_and_install_hint(self):
        buf = io.StringIO()
        with tempfile.TemporaryDirectory():
            with contextlib.redirect_stdout(buf):
                code = skill.run([], Path("."))
        self.assertEqual(code, 0)
        out = buf.getvalue()
        self.assertTrue(out.startswith("# crap4py Profiling Skill"))
        self.assertIn("pytest", out)
        self.assertIn("@60fps", out)
        self.assertIn("Install as an agent skill", out.splitlines()[-1])
        self.assertLess(len(out.splitlines()), 90)


class SkillCliTest(unittest.TestCase):
    def test_skill_subcommand_exits_0(self):
        r = subprocess.run(
            [sys.executable, "-m", "crap4py", "skill"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("# crap4py Profiling Skill"))


if __name__ == "__main__":
    unittest.main()
