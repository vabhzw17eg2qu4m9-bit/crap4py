"""``folder-structure`` subcommand: flags loose-file sprawl in package dirs.

Port of the crap4dart ``folder_structure`` gate: directories containing
more than ``--max`` (default 0) ``.py`` files *directly* — a flat-file
sprawl that should be organized into feature subpackages. Default dirs are
the importable package roots: direct children of the package root
(``src/`` when present, else the project root) that contain an
``__init__.py``. ``__init__.py`` and ``__main__.py`` are mandatory
package plumbing and never counted as loose files.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .analyzer import _relative_to_root
from .args import UsageErrorParser
from .imports import package_root

MAX_LOOSE_FILES = 0
_SETUP_NAMES = frozenset({"__init__.py", "__main__.py"})


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
    """Entry point for ``crap4py folder-structure [--dir DIR]... [--max N]``."""
    args = _build_parser().parse_args(argv)
    dirs = args.dirs or default_dirs(project_root)
    result = check_dirs(dirs, project_root, args.max)
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
    dirs: list[str], project_root: Path, max_loose_files: int = MAX_LOOSE_FILES
) -> StructureResult:
    """Flag each existing directory with more than ``max_loose_files`` direct files."""
    violations: list[StructureViolation] = []
    checked = 0
    for d in dirs:
        full = Path(project_root) / d
        if not full.is_dir():
            continue
        checked += 1
        loose = _loose_count(full)
        if loose > max_loose_files:
            violations.append(
                StructureViolation(
                    d,
                    f"{loose} loose .py files directly in {d} — "
                    f"group them into feature packages (max {max_loose_files})",
                )
            )
    return StructureResult(violations, checked)


def _loose_count(directory: Path) -> int:
    """Non-setup ``.py`` files directly inside ``directory`` (non-recursive)."""
    return sum(1 for p in directory.iterdir() if p.suffix == ".py" and p.name not in _SETUP_NAMES)


def summary(result: StructureResult) -> str:
    """One-line summary printed after the violations."""
    if result.violations:
        return f"{len(result.violations)} directory(ies) with loose-file sprawl"
    return f"{result.checked} directories organized into packages"


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
        help="project-relative directory to check (repeatable; default: package roots)",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=MAX_LOOSE_FILES,
        help=f"maximum loose files directly per directory (default: {MAX_LOOSE_FILES})",
    )
    return parser
