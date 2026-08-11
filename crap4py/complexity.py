"""Cyclomatic complexity + method extraction via the stdlib ``ast`` module.

Counting rules (base value 1, +1 per decision point):
  ``ast.If``, ``ast.For``, ``ast.AsyncFor``, ``ast.While``, ``ast.ExceptHandler``,
  ``ast.IfExp`` (ternary), ``ast.match_case`` (3.10+).
  ``ast.BoolOp`` contributes ``len(values) - 1``.
  ``ast.ListComp``/``SetComp``/``DictComp``/``GeneratorExp`` contribute
  ``len(generators)`` (one per ``for`` clause).

Lambda bodies count toward the enclosing method. Nested named function defs
are reported as their own methods and do NOT count toward the parent.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator

from .crap import MethodDescriptor

_FUNCTION_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)
_COMPREHENSION_TYPES = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def extract_methods(source: str, filename: str = "<source>") -> list[MethodDescriptor]:
    """Parse ``source`` and return every function/method with its complexity.

    Functions at any nesting depth are collected. A function's complexity is
    computed from its own body only — nested named defs are skipped so they
    don't inflate the parent (they're reported separately).
    """
    tree = ast.parse(source, filename=filename)
    methods: list[MethodDescriptor] = []
    _collect(tree, prefix=None, methods=methods)
    return methods


def _qualified(prefix: str | None, name: str) -> str:
    return f"{prefix}.{name}" if prefix else name


def _collect(node: ast.AST, prefix: str | None, methods: list[MethodDescriptor]) -> None:
    """Recurse the AST, recording each function def with its qualified name."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            _collect(child, prefix=_qualified(prefix, child.name), methods=methods)
        elif isinstance(child, _FUNCTION_TYPES):
            qual = _qualified(prefix, child.name)
            methods.append(
                MethodDescriptor(
                    name=qual,
                    start_line=child.lineno,
                    end_line=child.end_lineno or child.lineno,
                    complexity=_complexity_of_function(child),
                )
            )
            # Nested defs use the immediate function's bare name as prefix,
            # so a def inside ``Foo.bar`` is ``bar.inner`` (not ``Foo.bar.inner``).
            _collect(child, prefix=child.name, methods=methods)


def _complexity_of_function(func: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Count decision points in the function body, skipping nested named defs."""
    complexity = 1
    for stmt in func.body:
        if isinstance(stmt, _FUNCTION_TYPES):
            continue
        for node in _walk_skipping_functions(stmt):
            complexity += _node_complexity(node)
    return complexity


def _walk_skipping_functions(node: ast.AST) -> Iterator[ast.AST]:
    """Yield ``node`` and descendants, but do not descend into nested named defs."""
    yield node
    if isinstance(node, _FUNCTION_TYPES):
        return
    for child in ast.iter_child_nodes(node):
        yield from _walk_skipping_functions(child)


def _node_complexity(node: ast.AST) -> int:
    """Complexity contribution of a single AST node (0 for non-decision nodes)."""
    if isinstance(
        node,
        (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp, ast.match_case),
    ):
        return 1
    if isinstance(node, ast.BoolOp):
        return max(len(node.values) - 1, 0)
    if isinstance(node, _COMPREHENSION_TYPES):
        return len(node.generators)
    return 0
