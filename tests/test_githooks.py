"""End-to-end tests for the checked-in pre-commit hook.

The hook is the only part of the project that guards a developer's commit, and
what it prints is as load-bearing as what it returns: a refusal with no report
leaves nothing to act on, and a skip announced only by a warning is easy to
read straight past in git's output. Each test therefore drives a real
`git commit` in a throwaway repository and asserts on both the outcome and the
text the developer sees.

`crap4py` itself is replaced by a stub interpreter on PATH so a chosen exit
status can be provoked without constructing source that scores it.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "githooks" / "pre-commit"

STUB = """\
#!/usr/bin/env bash
case "$*" in
  *--help*) exit {help_status} ;;
  *) echo "STUB REPORT BODY" ; exit {run_status} ;;
esac
"""


def _requirements_met() -> bool:
    return all(shutil.which(name) for name in ("bash", "git"))


@unittest.skipUnless(_requirements_met(), "bash and git are required")
class PreCommitHookTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        hooks = self.root / "githooks"
        hooks.mkdir()
        shutil.copy(HOOK, hooks / "pre-commit")
        (hooks / "pre-commit").chmod(0o755)
        self._git("init", "-q", ".")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test")
        self._git("config", "core.hooksPath", "githooks")
        (self.root / "sample.py").write_text("x = 1\n")
        self._git("add", "sample.py")

    def tearDown(self):
        self._tmp.cleanup()

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, text=True, check=False
        )

    def _install_stub(self, name: str, run_status: int, help_status: int = 0) -> None:
        stub = self.bin / name
        stub.write_text(STUB.format(run_status=run_status, help_status=help_status))
        stub.chmod(0o755)

    def _commit(self) -> subprocess.CompletedProcess:
        path = f"{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin"
        return subprocess.run(
            ["git", "commit", "-m", "test"],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
            env={"PATH": path, "HOME": str(self.root)},
        )

    def _commit_count(self) -> int:
        result = self._git("rev-list", "--count", "HEAD")
        return int(result.stdout.strip()) if result.returncode == 0 else 0

    def test_threshold_breach_blocks_and_shows_the_report(self):
        self._install_stub("python3", run_status=2)
        result = self._commit()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self._commit_count(), 0)
        self.assertIn("STUB REPORT BODY", result.stderr)
        self.assertIn("CRAP threshold exceeded", result.stderr)

    def test_tool_error_blocks_rather_than_passing_silently(self):
        self._install_stub("python3", run_status=1)
        result = self._commit()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self._commit_count(), 0)
        self.assertIn("STUB REPORT BODY", result.stderr)

    def test_clean_run_allows_the_commit(self):
        self._install_stub("python3", run_status=0)
        result = self._commit()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._commit_count(), 1)

    def test_python3_is_used_when_no_bare_python_exists(self):
        self._install_stub("python3", run_status=2)
        self.assertIsNone(shutil.which("python", path=str(self.bin)))
        result = self._commit()
        self.assertEqual(self._commit_count(), 0)
        self.assertIn("CRAP threshold exceeded", result.stderr)

    def test_bare_python_is_used_when_python3_lacks_the_package(self):
        self._install_stub("python3", run_status=0, help_status=1)
        self._install_stub("python", run_status=2)
        result = self._commit()
        self.assertEqual(self._commit_count(), 0)
        self.assertIn("CRAP threshold exceeded", result.stderr)

    def test_absent_tool_warns_and_lets_the_commit_through(self):
        # Shadow both names so no system interpreter that happens to import
        # crap4py can satisfy the guard and skip this branch.
        self._install_stub("python3", run_status=0, help_status=1)
        self._install_stub("python", run_status=0, help_status=1)
        result = self._commit()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._commit_count(), 1)
        self.assertIn("crap4py not available", result.stderr)


if __name__ == "__main__":
    unittest.main()
