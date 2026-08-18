"""Source-file discovery, git-changed detection, and path expansion."""

from __future__ import annotations

import ast
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

PathLike = str | Path

_EXCLUDE_DIRS = frozenset(
    {"__pycache__", ".venv", "venv", "build", "dist", "site-packages", ".git"}
)
_TEST_DIRS = frozenset({"test", "tests"})


def find_source_files(project_root: PathLike) -> list[Path]:
    """Walk ``src/`` if present else ``.``, collecting analyzable ``.py`` files."""
    root = Path(project_root)
    base = root / "src" if (root / "src").is_dir() else root
    return sorted(_collect_py_files(base, root))


def changed_files(project_root: PathLike) -> list[Path]:
    """Return git-changed ``.py`` files under ``project_root`` (``git status --porcelain``)."""
    root = Path(project_root)
    result = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git status failed: {result.stderr.strip()}")
    files: list[Path] = []
    for line in result.stdout.splitlines():
        path_part = _parse_status_line(line)
        if path_part is None or not path_part.endswith(".py"):
            continue
        candidate = root / path_part
        if candidate.exists():
            files.append(candidate)
    return sorted(files)


def expand_paths(args: Iterable[PathLike], project_root: PathLike) -> list[Path]:
    """Expand file/dir args into a deduped, sorted list of paths.

    Files are kept as-is; directories are walked for ``.py`` source files.
    """
    root = Path(project_root)
    seen: set[Path] = set()
    for arg in args:
        p = Path(arg)
        if not p.is_absolute():
            p = root / arg
        p = p.resolve()
        if p.is_dir():
            seen.update(_collect_py_files(p, root))
        elif p.is_file():
            seen.add(p)
    return sorted(seen)


def _collect_py_files(base: Path, project_root: Path) -> Iterable[Path]:
    for path in sorted(base.rglob("*.py")):
        if _is_excluded(path, project_root):
            continue
        yield path


def _is_excluded(path: Path, project_root: Path) -> bool:
    return _in_excluded_dir(path, project_root) or _is_test_name(path.name)


def _in_excluded_dir(path: Path, project_root: Path) -> bool:
    try:
        rel_parts = path.resolve().relative_to(project_root.resolve()).parts
    except ValueError:
        rel_parts = path.parts
    return any(part in _EXCLUDE_DIRS for part in rel_parts)


def _is_test_name(name: str) -> bool:
    return name == "conftest.py" or name.startswith("test_") or name.endswith("_test.py")


def find_test_files(project_root: PathLike) -> list[Path]:
    """Walk ``src/`` if present else ``.``, collecting test ``.py`` files."""
    root = Path(project_root)
    base = root / "src" if (root / "src").is_dir() else root
    return sorted(
        path
        for path in base.rglob("*.py")
        if is_test_file(path, root) and not _in_excluded_dir(path, root)
    )


def is_test_file(path: PathLike, project_root: PathLike) -> bool:
    """Whether ``path`` is a test file or lives under a test directory."""
    name = Path(path).name
    if name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py":
        return True
    try:
        parts = Path(path).resolve().relative_to(Path(project_root).resolve()).parts
    except ValueError:
        return False
    return any(part in _TEST_DIRS for part in parts[:-1])


def parse_file(path: PathLike) -> ast.Module | None:
    """Parse a Python source file; warn on stderr and return None when broken."""
    try:
        return ast.parse(Path(path).read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        print(f"Warning: could not parse {path}: {exc}", file=sys.stderr)
        return None


def _parse_status_line(line: str) -> str | None:
    """Extract the file path from a ``git status --porcelain`` line."""
    if len(line) < 4:
        return None
    path_part = line[3:].strip()
    if " -> " in path_part:  # rename: take the destination
        path_part = path_part.split(" -> ", 1)[1]
    # Strip surrounding quotes git uses for paths with special chars.
    if path_part.startswith('"') and path_part.endswith('"'):
        path_part = path_part[1:-1]
    return path_part
