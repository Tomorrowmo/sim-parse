"""Unit tests for #4: convergence-status alerts.

The flat `convergence_orders` dict is preserved (back-compat); a new
`convergence_status` dict adds per-variable status + thresholds, and
`convergence_summary` rolls up counts + lists. Tier 4 also emits a
top-level warning when any variable diverged.
"""
from __future__ import annotations

import pytest

from sim_parse.core.diagnostics import (
    classify_convergence_orders as _classify_convergence_orders,
    convergence_status_label as _convergence_status_label,
    summarize_convergence_status as _summarize_convergence_status,
)


# ─── Threshold logic ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("value,expected", [
    (-10.0, "diverged"),
    (-0.5,  "diverged"),
    (-1e-9, "diverged"),
    (0.0,   "marginal"),
    (1.5,   "marginal"),
    (2.999, "marginal"),
    (3.0,   "converged"),
    (6.6,   "converged"),
    (15.0,  "converged"),
])
def test_convergence_status_label(value, expected):
    assert _convergence_status_label(value) == expected


def test_classify_attaches_thresholds_for_traceability():
    """Each per-variable status must carry the thresholds that produced it,
    so a downstream reviewer can audit without recomputing."""
    out = _classify_convergence_orders({"Ux": 6.6})
    rec = out["Ux"]
    assert rec["value"] == 6.6
    assert rec["status"] == "converged"
    assert rec["threshold_used"]["diverged"] == 0.0
    assert rec["threshold_used"]["converged"] == 3.0


def test_classify_handles_all_three_statuses():
    out = _classify_convergence_orders({"Ux": 6.6, "h": 1.5, "CH": -5.99})
    assert out["Ux"]["status"] == "converged"
    assert out["h"]["status"] == "marginal"
    assert out["CH"]["status"] == "diverged"


# ─── Summary roll-up ──────────────────────────────────────────────────────────


def test_summary_counts():
    cstatus = _classify_convergence_orders({
        "Ux": 6.6, "Uy": 6.2, "p": 5.5,    # converged
        "h": 2.0, "k": 1.8,                  # marginal
        "CH": -5.99, "C2H5": -2.09,          # diverged
    })
    s = _summarize_convergence_status(cstatus)
    assert s["n_total"] == 7
    assert s["n_converged"] == 3
    assert s["n_marginal"] == 2
    assert s["n_diverged"] == 2
    assert s["converged_vars"] == ["Ux", "Uy", "p"]
    assert s["marginal_vars"] == ["h", "k"]
    assert s["diverged_vars"] == ["C2H5", "CH"]


def test_summary_handles_empty_input():
    s = _summarize_convergence_status({})
    assert s["n_total"] == 0
    assert s["n_converged"] == 0
    assert s["n_marginal"] == 0
    assert s["n_diverged"] == 0
    assert s["diverged_vars"] == []


def test_summary_lists_are_sorted():
    """Sorted output makes diff-based regression testing reliable."""
    cstatus = _classify_convergence_orders({
        "z_var": 5.0, "a_var": 5.0, "m_var": 5.0,
    })
    s = _summarize_convergence_status(cstatus)
    assert s["converged_vars"] == ["a_var", "m_var", "z_var"]
