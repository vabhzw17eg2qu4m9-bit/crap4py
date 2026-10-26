"""``folder-structure`` subcommand: flags loose-file sprawl in package dirs.

Port of the crap4dart ``folder_structure`` gate: directories containing
more than their cap of logical ``.py`` units *directly* — a flat-file
sprawl that should be organized into feature subpackages. Two forms:

- classic: ``--dir DIR`` (repeatable — literal paths or glob patterns
  like ``pkg/*`` expanding to every matching directory) sharing one
  ``--max`` cap (default 0);
- ``--cap PATTERN=N`` (repeatable): per-pattern caps; when present it
  takes precedence and ``--dir``/``--max`` are not consulted. A
  directory matched by several patterns uses the first pattern's cap.

Counting rules: only a directory's OWN contents count — files in
subdirectories are the organized form and never reach the parent's
budget. Generated siblings of one source file collapse into one
logical unit — ``foo.py``, ``foo_pb2.py`` and ``foo_pb2_grpc.py``
share the stem ``foo`` and count once (the host-generic py generated
set; ``.pyi`` stubs are package metadata and never counted).
``--exclude`` globs exempt matching files from the count. Default dirs
are the importable package roots: direct children of the package root
(``src/`` when present, else the project root) that contain an
``__init__.py``. ``__init__.py`` and ``__main__.py`` are mandatory
package plumbing and never counted as loose files.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from .analyzer import _relative_to_root
from .args import UsageErrorParser
from .imports import package_root

MAX_LOOSE_FILES = 0
_SETUP_NAMES = frozenset({"__init__.py", "__main__.py"})
# Host-generic generated-sibling suffixes: codegen output of one source
# file collapses into its stem (see _stem).
_GEN_SUFFIXES = ("_pb2_grpc", "_pb2")
# Glob expansion never enumerates hidden or build noise (dot-prefixed
# dirs, caches, build/venv output).
_SKIP_DIRS = frozenset({"__pycache__", "build", "dist", "venv", "node_modules"})


@dataclass(frozen=True, slots=True)
class StructureViolation:
    """A single loose-file-sprawl finding."""

    directory: str
    message: str


@dataclass(frozen=True, slots=True)
class StructureResult:
    """Violations found plus how many directories were checked."""

    violations: list[StructureViolation]
    checked: int


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py folder-structure``."""
    args = _build_parser().parse_args(argv)
    dirs = args.dirs or default_dirs(project_root)
    result = check_dirs(dirs, project_root, args.max, caps=dict(args.caps), exclude=args.exclude)
    for violation in result.violations:
        print(f"{violation.directory}: {violation.message}")
    print(summary(result))
    return 2 if result.violations else 0


def default_dirs(project_root: Path) -> list[str]:
    """Importable package roots: package-root children with an ``__init__.py``."""
    root = Path(project_root).resolve()
    base = package_root(root)
    return sorted(
        _relative_to_root(d, root)
        for d in base.iterdir()
        if d.is_dir() and (d / "__init__.py").is_file()
    )


def check_dirs(
    dirs: list[str],
    project_root: Path,
    max_loose_files: int = MAX_LOOSE_FILES,
    caps: dict[str, int] | None = None,
    exclude: list[str] | None = None,
) -> StructureResult:
    """Flag each existing directory holding more than its cap of logical units."""
    root = Path(project_root)
    violations: list[StructureViolation] = []
    checks = _checks(dirs, root, max_loose_files, caps or {})
    for directory, cap in checks:
        units = _unit_count(root / directory, root, exclude or [])
        if units > cap:
            violations.append(
                StructureViolation(
                    directory,
                    f"{units} loose .py files directly in {directory} — "
                    f"group them into feature packages (max {cap})",
                )
            )
    return StructureResult(violations, len(checks))


def _checks(
    dirs: list[str], root: Path, max_loose_files: int, caps: dict[str, int]
) -> list[tuple[str, int]]:
    """Every directory to check with its effective cap: the caps form
    gives each pattern's directory its own cap (first pattern wins);
    otherwise every expanded ``dirs`` entry shares ``max_loose_files``."""
    by_dir: dict[str, int] = {}
    if caps:
        for pattern, cap in caps.items():
            for directory in _expand(root, pattern):
                by_dir.setdefault(directory, cap)
        return list(by_dir.items())
    for entry in dirs:
        for directory in _expand(root, entry):
            by_dir.setdefault(directory, max_loose_files)
    return sorted(by_dir.items())


def _expand(root: Path, entry: str) -> list[str]:
    """One entry resolved to existing project-relative directories: a
    literal path passes through; a glob pattern expands to every
    matching directory under the project root."""
    if (root / entry).is_dir():
        return [entry]
    return sorted(
        _relative_to_root(p, root) for p in root.glob(entry) if p.is_dir() and not _skipped(p, root)
    )


def _skipped(path: Path, root: Path) -> bool:
    """A directory under hidden or build noise — structure gates never
    enumerate ``.git``, caches or build output."""
    return any(
        part.startswith(".") or part in _SKIP_DIRS or part.endswith(".egg-info")
        for part in path.relative_to(root).parts
    )


def _unit_count(directory: Path, root: Path, exclude: list[str]) -> int:
    """Distinct logical ``.py`` units directly inside ``directory``:
    files grouped by stem (see ``_stem``), so generated siblings do not
    inflate the count."""
    stems: set[str] = set()
    for p in directory.iterdir():
        if p.suffix != ".py" or p.name in _SETUP_NAMES:
            continue
        if any(fnmatch(_relative_to_root(p, root), pattern) for pattern in exclude):
            continue
        stems.add(_stem(p.name))
    return len(stems)


def _stem(name: str) -> str:
    """The logical unit of a ``.py`` file: the basename with generated
    sibling suffixes stripped — ``foo.py``, ``foo_pb2.py`` and
    ``foo_pb2_grpc.py`` share the stem ``foo``."""
    base = name[:-3]
    for suffix in _GEN_SUFFIXES:
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


def summary(result: StructureResult) -> str:
    """One-line summary printed after the violations."""
    if result.violations:
        return f"{len(result.violations)} directory(ies) with loose-file sprawl"
    return f"{result.checked} directories organized into packages"


def _parse_cap(spec: str) -> tuple[str, int]:
    """Parse a ``--cap PATTERN=CAP`` argument."""
    pattern, sep, raw = spec.rpartition("=")
    if not sep or not pattern:
        raise argparse.ArgumentTypeError(f"expected PATTERN=CAP, got {spec!r}")
    try:
        return pattern, int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"cap for {pattern!r} must be an integer") from None


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py folder-structure",
        description="Flag directories with too many loose .py files directly.",
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument(
        "--dir",
        dest="dirs",
        action="append",
        default=[],
        metavar="DIR",
        help="project-relative directory to check, a literal path or glob "
        "pattern (repeatable; default: package roots)",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=MAX_LOOSE_FILES,
        help=f"maximum logical .py units directly per directory (default: {MAX_LOOSE_FILES})",
    )
    parser.add_argument(
        "--cap",
        dest="caps",
        action="append",
        default=[],
        type=_parse_cap,
        metavar="PATTERN=CAP",
        help="per-pattern cap; PATTERN expands like --dir and gets its own "
        "cap (repeatable; first pattern wins; takes precedence over --dir/--max)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="skip files whose project-relative path matches this fnmatch glob (repeatable)",
    )
    return parser
