"""Unit tests for the restricted ast-based expression evaluator."""
from __future__ import annotations

import pytest

from sim_parse.core.canonical.safe_eval import safe_eval


# ─── Allowed: numeric ops, named vars, whitelisted funcs ──────────────────────


def test_simple_arithmetic():
    assert safe_eval("1 + 2", {}) == 3
    assert safe_eval("10 - 3", {}) == 7
    assert safe_eval("4 * 5", {}) == 20
    assert safe_eval("10 / 4", {}) == 2.5
    assert safe_eval("10 // 3", {}) == 3
    assert safe_eval("10 % 3", {}) == 1
    assert safe_eval("2 ** 10", {}) == 1024


def test_variable_lookup():
    assert safe_eval("a + b", {"a": 5, "b": 3}) == 8
    assert safe_eval("T_max - T_inlet", {"T_max": 2000, "T_inlet": 300}) == 1700


def test_unary_negation():
    assert safe_eval("-x", {"x": 5}) == -5
    assert safe_eval("-(a + b)", {"a": 2, "b": 3}) == -5


def test_function_calls():
    assert safe_eval("abs(-7)", {}) == 7
    assert safe_eval("min(a, b)", {"a": 3, "b": 7}) == 3
    assert safe_eval("max(a, b)", {"a": 3, "b": 7}) == 7
    assert safe_eval("sqrt(16)", {}) == 4
    assert safe_eval("log10(1000)", {}) == 3


def test_compound_expression():
    """The is_extinguished low_temperature_excess criterion."""
    assert safe_eval("T_max - T_inlet", {"T_max": 1998, "T_inlet": 292}) == 1706
    # Voting threshold check would then apply (1706 < 100 → False, flame on)


def test_mass_imbalance_expression():
    """Real physics: |m_in - m_out| / m_in."""
    result = safe_eval(
        "abs(m_in - m_out) / m_in",
        {"m_in": 100.0, "m_out": 99.5},
    )
    assert abs(result - 0.005) < 1e-9


def test_expression_with_min_list():
    assert safe_eval("min([1, 2, 3])", {}) == 1
    assert safe_eval("max([a, b, c])", {"a": 5, "b": 9, "c": 2}) == 9


# ─── Disallowed: must return None and not raise ───────────────────────────────


def test_attribute_access_rejected():
    """Cannot access object attributes."""
    assert safe_eval("a.b", {"a": {"b": 1}}) is None


def test_subscript_rejected():
    """Cannot index into objects."""
    assert safe_eval("a[0]", {"a": [1, 2, 3]}) is None


def test_unknown_name_rejected():
    """Names not in the namespace and not in whitelist → None."""
    assert safe_eval("undefined_thing", {}) is None


def test_undefined_function_rejected():
    """Function not in whitelist."""
    assert safe_eval("os_system('rm -rf /')", {}) is None
    assert safe_eval("__import__('os')", {}) is None


def test_lambda_rejected():
    assert safe_eval("(lambda x: x*2)(5)", {}) is None


def test_imports_rejected():
    """Statements / imports not allowed."""
    # Import statement isn't even an expression, but make sure parse fails gracefully
    assert safe_eval("import os", {}) is None


def test_division_by_zero_returns_none():
    """ZeroDivisionError should be caught."""
    assert safe_eval("a / 0", {"a": 5}) is None


def test_none_value_in_namespace():
    """If a referenced variable is None, eval returns None."""
    assert safe_eval("a + b", {"a": 5, "b": None}) is None


def test_empty_expression_returns_none():
    assert safe_eval("", {}) is None


def test_syntax_error_returns_none():
    assert safe_eval("1 +", {}) is None
    assert safe_eval("a *", {"a": 5}) is None


# ─── Numeric edge cases ────────────────────────────────────────────────────────


def test_float_precision():
    assert abs(safe_eval("0.1 + 0.2", {}) - 0.3) < 1e-9


def test_large_numbers():
    assert safe_eval("1.0e+9 / 1.0e+6", {}) == 1000.0


def test_negative_zero():
    result = safe_eval("0.0 - x", {"x": 0.0})
    assert result == 0.0
