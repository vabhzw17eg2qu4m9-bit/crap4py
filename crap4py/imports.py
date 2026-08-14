"""Import → project-file resolution shared by ``unused-files`` and ``banned-imports``.

Imports are resolved on unresolved ASTs: a dotted name hits the project only
when the corresponding file exists on disk, so stdlib and external imports
never count as project references.
"""

from __future__ import annotations

import ast
from pathlib import Path


def package_root(root: Path) -> Path:
    """Directory absolute/dotted imports resolve against (``src/`` layout aware)."""
    return root / "src" if (root / "src").is_dir() else root


def resolve_dotted(dotted: str, base: Path) -> set[Path]:
    """Project files a dotted name imported from ``base`` could target."""
    rel = dotted.replace(".", "/")
    hits = set()
    for candidate in (base / f"{rel}.py", base / rel / "__init__.py"):
        if candidate.exists():
            hits.add(candidate.resolve())
    return hits


def imported_paths(tree: ast.Module, importer: Path, pkg_root: Path) -> set[Path]:
    """Project files imported by ``tree``'s module; external imports don't resolve."""
    hits = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                hits |= resolve_dotted(alias.name, pkg_root)
        elif isinstance(node, ast.ImportFrom):
            hits |= from_import_paths(node, importer, pkg_root)
    return hits


def from_import_paths(node: ast.ImportFrom, importer: Path, pkg_root: Path) -> set[Path]:
    """Project files targeted by one ``from ... import ...`` directive.

    ``from pkg import mod`` imports ``pkg/mod.py`` when it exists, so each
    alias is also resolved as ``<module>.<alias>``.
    """
    base = _directive_base(node, importer, pkg_root)
    if not node.module:
        return _resolve_aliases(node.names, base)
    hits = resolve_dotted(node.module, base)
    for alias in node.names:
        hits |= resolve_dotted(f"{node.module}.{alias.name}", base)
    return hits


def _resolve_aliases(names: list[ast.alias], base: Path) -> set[Path]:
    hits = set()
    for alias in names:
        hits |= resolve_dotted(alias.name, base)
    return hits


def _directive_base(node: ast.ImportFrom, importer: Path, pkg_root: Path) -> Path:
    """Directory a ``from`` directive resolves against: relative to the importer
    for ``from .``/``from ..``, the package root for absolute ``from pkg.``."""
    base = pkg_root if node.level == 0 else importer.parent
    for _ in range((node.level or 0) - 1):
        base = base.parent
    return base
