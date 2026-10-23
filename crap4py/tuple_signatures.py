"""``tuple-signatures`` subcommand: flags inline tuple shapes in signatures.

Port of the crap4dart record gates (4d844a4: ``inline_record_types`` +
``record_size``), adapted to Python: Dart's anonymous record shapes map to
inline tuple annotations — ``tuple[int, str]`` / ``typing.Tuple[int, str]``
subscripts in function signatures (module functions, methods, nested
functions), including nested inside generic type arguments
(``dict[str, tuple[int, int]]``, ``Optional[...]``, ``X | None`` unions,
``Callable`` argument lists); only the outermost shape of a nested tuple
group is reported, mirroring upstream. The ``Tuple`` bare name from
``from typing import Tuple`` counts like ``typing.Tuple``. Upstream's two
annotation kinds (inline shape, unnamed fields) collapse here: every tuple
annotation is unnamed-by-nature.

Named shapes are the blessed form: a ``NamedTuple``/``dataclass``/
``TypedDict`` declaration referenced by name is untouched — only inline
subscripts are flagged, and class-body field annotations are declarations,
not signatures, so blessed forms fall out of the scan scope.

Upstream's third kind (``.$N`` positional access) has no honest Python
analog — ``t[0]`` fires on every sequence — and is skipped.

Variadic tuples (``tuple[int, ...]``) stay legal: homogeneous sequences,
not record shapes. ``--max-fields`` (default 5) caps the arity of an
inline tuple shape, mirroring upstream's record_size cap.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from .analyzer import _relative_to_root
from .args import UsageErrorParser
from .complexity import _FUNCTION_TYPES
from .files import PathLike, expand_paths, find_source_files, is_test_file, parse_file

MAX_FIELDS = 5


@dataclass(frozen=True, slots=True)
class TupleSigViolation:
    """A single inline-tuple-signature finding."""

    file: str
    line: int
    message: str


@dataclass(frozen=True, slots=True)
class TupleSigResult:
    """Violations found plus how many signatures were checked."""

    violations: list[TupleSigViolation]
    checked: int


class _SigFindings(NamedTuple):
    """Per-module scan outcome: violations in source order plus the signature count."""

    violations: list[TupleSigViolation]
    checked: int


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py tuple-signatures [paths...]``. Exit 2 iff violations."""
    args = _build_parser().parse_args(argv)
    files = (
        expand_paths(args.paths, project_root) if args.paths else find_source_files(project_root)
    )
    if not files:
        print("No Python files to check.")
        return 0
    result = check_files(files, project_root, args.max_fields)
    for violation in result.violations:
        print(f"{violation.file}:{violation.line}: {violation.message}")
    print(summary(result))
    return 2 if result.violations else 0


def check_files(
    files: Iterable[PathLike], project_root: PathLike, max_fields: int = MAX_FIELDS
) -> TupleSigResult:
    """Flag inline tuple shapes in every signature of every non-test file."""
    root = Path(project_root)
    violations: list[TupleSigViolation] = []
    checked = 0
    for file_path in files:
        p = Path(file_path)
        if is_test_file(p, root):
            continue
        tree = parse_file(p)
        if tree is None:
            continue
        file_violations, count = _signature_violations(tree, _relative_to_root(p, root), max_fields)
        violations += file_violations
        checked += count
    return TupleSigResult(violations, checked)


def _signature_violations(tree: ast.Module, rel: str, max_fields: int) -> _SigFindings:
    """Violations and signature count of one module, with the file path filled in."""
    violations: list[TupleSigViolation] = []
    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, _FUNCTION_TYPES):
            continue
        checked += 1
        violations += [_violation(shape, rel, max_fields) for shape in _tuple_shapes(node)]
    violations.sort(key=lambda v: v.line)
    return _SigFindings(violations, checked)


def _tuple_shapes(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Subscript]:
    """Inline tuple subscripts across a signature's parameter and return annotations."""
    a = func.args
    params = [
        p for p in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg] if p is not None
    ]
    annotations = [arg.annotation for arg in params if arg.annotation is not None]
    if func.returns is not None:
        annotations.append(func.returns)
    shapes = [shape for ann in annotations for shape in _tuple_group(ann)]
    return [shape for shape in shapes if not _is_variadic(shape)]


def _tuple_group(ann: ast.expr) -> list[ast.Subscript]:
    """Outermost inline tuple subscripts in an annotation subtree, in source order.

    An inner tuple inside an already-flagged shape is fixed by the same
    shape extraction, so a flagged shape is not descended into.
    """
    shape = _tuple_shape(ann)
    if shape is not None:
        return [shape]
    if isinstance(ann, ast.Subscript):
        return _tuple_group(ann.slice)
    if isinstance(ann, ast.BinOp):
        return _tuple_group(ann.left) + _tuple_group(ann.right)
    if isinstance(ann, (ast.Tuple, ast.List)):
        return [s for elt in ann.elts for s in _tuple_group(elt)]
    return []


def _tuple_shape(ann: ast.expr) -> ast.Subscript | None:
    """The subscript node of a ``tuple[...]``/``typing.Tuple[...]`` annotation, else None."""
    if not isinstance(ann, ast.Subscript) or not _is_tuple_name(ann.value):
        return None
    return ann if isinstance(ann.slice, ast.Tuple) else None


def _is_tuple_name(node: ast.expr) -> bool:
    """Whether a subscript base names the tuple type (``tuple``, ``Tuple``, ``typing.Tuple``)."""
    if isinstance(node, ast.Name):
        return node.id in ("tuple", "Tuple")
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "Tuple"
        and isinstance(node.value, ast.Name)
        and node.value.id == "typing"
    )


def _is_variadic(shape: ast.Subscript) -> bool:
    """``tuple[int, ...]`` — homogeneous variadic tuple, not a record shape."""
    return any(isinstance(elt, ast.Constant) and elt.value is Ellipsis for elt in shape.slice.elts)


def _violation(shape: ast.Subscript, rel: str, max_fields: int) -> TupleSigViolation:
    """Finding for one inline tuple shape; over-cap shapes carry the arity note."""
    message = (
        f"inline tuple type '{ast.unparse(shape)}' in signature — declare the shape "
        "as a NamedTuple/dataclass and reference it by name"
    )
    count = len(shape.slice.elts)
    if count > max_fields:
        message += f": {count} fields > max {max_fields} — the shape has outgrown a tuple"
    return TupleSigViolation(rel, shape.lineno, message)


def summary(result: TupleSigResult) -> str:
    """One-line summary printed after the violations."""
    if result.violations:
        return f"{len(result.violations)} inline tuple shapes in signatures"
    return f"{result.checked} signatures checked, no inline tuple shapes"


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py tuple-signatures",
        description="Flag inline tuple annotations in function signatures.",
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument(
        "paths", nargs="*", help="explicit files or directories (default: normal selection)"
    )
    parser.add_argument(
        "--max-fields",
        type=int,
        default=MAX_FIELDS,
        help=f"flag tuple shapes with more fields than this (default: {MAX_FIELDS})",
    )
    return parser
