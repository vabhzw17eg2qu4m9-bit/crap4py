"""Cognitive complexity (SonarSource v1.2) via the stdlib ``ast`` module.

Port of the crap4dart ``CognitiveCalculator`` (``analysis/cognitive.dart``),
adapted to Python's grammar.

Increments:
  - **Basic** ``+1`` for each break in the linear flow: ``if``, ``elif``,
    ``else``, ``for``/``async for``, ``while``, ``except`` handler, every
    ``match`` case, the ternary ``x if c else y``, and each *sequence* of
    identical ``and``/``or`` operators (``a and b and c`` is one sequence
    → +1, ``a and b or c`` is two sequences → +2).
  - **Nesting** ``+N`` (on top of the basic increment) for the structural
    constructs above — everything except ``and``/``or`` — when they appear
    inside another structural construct at depth ``N``.
  - An ``elif`` chain is flat: each chained ``if`` increments once at the
    depth of its originating ``if``, so ``if/elif/elif`` scores 3 regardless
    of chain length. Python parses ``elif`` and ``else`` + ``if`` into the
    same AST, so both stay flat; an ``if`` after other ``else`` statements
    keeps paying the nesting penalty. Loop ``else`` suites (no upstream
    analog) nest like the loop body without an extra increment.

Nesting depth is increased by ``if`` branches (then and bare ``else``), loop
bodies, ``except`` bodies, ``match`` cases, ternary branches, lambda bodies,
and comprehension bodies (lambdas/comprehensions themselves do not increment
— like nested methods in the SonarSource definition). Nested named function
defs are skipped entirely, mirroring the cyclomatic calculator.

Unlike cyclomatic complexity, the score starts at 0 and is not a path count;
it tracks how hard a method is to *read*, which is why flat ``elif`` chains
stay cheap and nested ones do not.
"""

from __future__ import annotations

import ast

_FUNCTION_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)
_COMPREHENSION_TYPES = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_FLOW_BREAKS = (ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.match_case, ast.IfExp)
_NESTED_UNITS = (ast.Lambda, *_COMPREHENSION_TYPES)


def cognitive_complexity(func: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Cognitive complexity of the function body, skipping nested named defs."""
    visitor = _CognitiveVisitor()
    for stmt in func.body:
        if isinstance(stmt, _FUNCTION_TYPES):
            continue
        visitor.visit(stmt)
    return visitor.score


class _CognitiveVisitor:
    """Recursive AST walker accumulating the score at the current nesting depth."""

    def __init__(self) -> None:
        self.depth = 0
        self.score = 0

    def visit(self, node: ast.AST, seq_op: ast.boolop | None = None) -> None:
        """Visit ``node``; ``seq_op`` is the operator of the enclosing
        same-operator ``and``/``or`` sequence, else ``None``."""
        if isinstance(node, _FUNCTION_TYPES):
            return  # nested named defs are separate units of code
        if isinstance(node, ast.If):
            self._visit_if(node)
        elif isinstance(node, _FLOW_BREAKS):
            self._flow_break(node)
        elif isinstance(node, ast.BoolOp):
            self._visit_boolop(node, seq_op)
        elif isinstance(node, _NESTED_UNITS):
            self._nested_unit(node)
        else:
            self._visit_children(node)

    def _visit_if(self, node: ast.If) -> None:
        """``if``: +1+depth, then-branch one level deeper; ``elif`` chains stay
        flat at this if's depth, a bare ``else`` pays the nesting penalty too."""
        self.score += 1 + self.depth
        self.visit(node.test)
        self._deeper(node.body)
        orelse = node.orelse
        if len(orelse) == 1 and isinstance(orelse[0], ast.If):
            self.visit(orelse[0])  # ``elif``: one flat increment at this if's depth
        elif orelse:
            self.score += 1 + self.depth
            self._deeper(orelse)

    def _flow_break(self, node: ast.AST) -> None:
        """Loop/except/match-case/ternary: +1+depth, children one level deeper."""
        self.score += 1 + self.depth
        self.depth += 1
        self._visit_children(node)
        self.depth -= 1

    def _visit_boolop(self, node: ast.BoolOp, seq_op: ast.boolop | None) -> None:
        """Only the root of a same-operator sequence increments; sequences are flat."""
        if seq_op is not node.op:
            self.score += 1
        self._visit_children(node, node.op)

    def _nested_unit(self, node: ast.AST) -> None:
        """Lambda/comprehension bodies deepen without incrementing themselves."""
        self.depth += 1
        self._visit_children(node)
        self.depth -= 1

    def _deeper(self, stmts: list[ast.stmt]) -> None:
        self.depth += 1
        for stmt in stmts:
            self.visit(stmt)
        self.depth -= 1

    def _visit_children(self, node: ast.AST, seq_op: ast.boolop | None = None) -> None:
        for child in ast.iter_child_nodes(node):
            self.visit(child, seq_op)
