"""Restricted expression evaluator for QOI rule expressions.

Phase 2 feature: lets YAML criteria use math expressions on multiple signals.

Allowed:
    - Numeric literals (int, float)
    - Named variables (looked up in `names` dict)
    - Binary ops: + - * / // % **
    - Unary ops: + -
    - Function calls — only to whitelist: abs, min, max, sqrt, pow, log, log10, exp, sum

Rejected:
    - Attribute access (foo.bar) — names are flat
    - Subscript (foo[0]) — none of our values are indexed
    - Function/class definitions
    - Imports
    - Lambda
    - Comparisons (those go in the `op`/`threshold` fields, not expressions)

The evaluator NEVER raises on malformed input — returns None and logs.
"""
from __future__ import annotations

import ast
import logging
import math
import operator
from typing import Any

logger = logging.getLogger("sim_parse.safe_eval")


_ALLOWED_FUNCS: dict[str, Any] = {
    "abs": abs,
    "min": min,
    "max": max,
    "sum": sum,
    "sqrt": math.sqrt,
    "pow": pow,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "exp": math.exp,
}

_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_ALLOWED_UNARYOPS = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def safe_eval(expr_str: str, names: dict[str, Any]) -> float | None:
    """Evaluate a numeric expression against `names`.

    Args:
        expr_str: e.g. "T_max - T_inlet" or "abs(m_in - m_out) / m_in"
        names: e.g. {"T_max": 1998, "T_inlet": 292}

    Returns:
        The numeric result, or None on any error.
    """
    if not expr_str:
        return None
    try:
        tree = ast.parse(expr_str, mode="eval")
        return _eval_node(tree.body, names)
    except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError) as e:
        logger.warning("safe_eval(%r) failed: %s", expr_str, e)
        return None


def _eval_node(node: ast.AST, names: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, bool)):
            return node.value
        raise ValueError(f"Disallowed constant type: {type(node.value).__name__}")

    if isinstance(node, ast.Name):
        if node.id in names:
            v = names[node.id]
            if v is None:
                raise ValueError(f"Variable '{node.id}' is None")
            return v
        if node.id in _ALLOWED_FUNCS:
            return _ALLOWED_FUNCS[node.id]
        raise ValueError(f"Unknown name: {node.id}")

    if isinstance(node, ast.BinOp):
        op_fn = _ALLOWED_BINOPS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Disallowed binary operator: {type(node.op).__name__}")
        lhs = _eval_node(node.left, names)
        rhs = _eval_node(node.right, names)
        return op_fn(lhs, rhs)

    if isinstance(node, ast.UnaryOp):
        op_fn = _ALLOWED_UNARYOPS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Disallowed unary operator: {type(node.op).__name__}")
        return op_fn(_eval_node(node.operand, names))

    if isinstance(node, ast.Call):
        # Only allow named function calls on the whitelist
        if not isinstance(node.func, ast.Name):
            raise ValueError("Function calls must be plain name references")
        fname = node.func.id
        if fname not in _ALLOWED_FUNCS:
            raise ValueError(f"Function '{fname}' not in whitelist")
        # No keyword args, no starred
        if node.keywords:
            raise ValueError("Keyword arguments not allowed")
        args = [_eval_node(a, names) for a in node.args]
        return _ALLOWED_FUNCS[fname](*args)

    if isinstance(node, ast.Tuple) or isinstance(node, ast.List):
        # Allow as args to min/max/sum
        return [_eval_node(elt, names) for elt in node.elts]

    raise ValueError(f"Disallowed AST node: {type(node).__name__}")
