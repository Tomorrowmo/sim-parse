"""Unit tests for #5 unit-range sanity checks (mass fraction etc.)."""
from __future__ import annotations

from sim_parse.core.diagnostics import check_unit_ranges


def test_mass_fraction_within_range_no_violation():
    vr = {
        "CH4": {"min": 0.0, "max": 0.246, "mean": 0.05},
        "CO2": {"min": 0.0, "max": 0.087, "mean": 0.02},
        "H2O": {"min": 0.0, "max": 0.145, "mean": 0.06},
    }
    assert check_unit_ranges(vr) == []


def test_mass_fraction_max_above_one_flagged():
    """The exact case observed on sprayFoam openform-wrapper:
    CO=1.92, CO2=31.3 means the field is NOT a mass fraction."""
    vr = {
        "CO": {"min": 0.0, "max": 1.92, "mean": 0.5},
        "CO2": {"min": 0.0, "max": 31.3, "mean": 5.0},
    }
    violations = check_unit_ranges(vr)
    var_names = {v["variable"] for v in violations}
    assert var_names == {"CO", "CO2"}
    co = next(v for v in violations if v["variable"] == "CO")
    assert co["label"] == "mass_fraction_out_of_range"
    assert co["stat"] == "max"
    assert co["value"] == 1.92
    assert co["expected_max"] == 1.01


def test_mass_fraction_negative_min_flagged():
    """Tiny negative values can be float noise, but still informative."""
    vr = {"CH4": {"min": -0.5, "max": 0.5}}
    violations = check_unit_ranges(vr)
    assert len(violations) == 1
    assert violations[0]["variable"] == "CH4"
    assert violations[0]["stat"] == "min"


def test_unrelated_variables_not_flagged():
    """Pressure / temperature / velocity have no unit-range constraint —
    must NOT be touched by mass-fraction rules."""
    vr = {
        "T":  {"min": 300, "max": 4796, "mean": 800},     # high but valid
        "p":  {"min": 100, "max": 1.0e9, "mean": 1e5},
        "U":  {"magnitude_min": 0.0, "magnitude_max": 3300},
    }
    assert check_unit_ranges(vr) == []


def test_kappa_check():
    """EDC kappa must be in [0,1]; if it's 5 the formulation is broken."""
    vr = {"EDC_psiReactionThermo__kappa": {"min": 0.0, "max": 5.0}}
    violations = check_unit_ranges(vr)
    assert len(violations) == 1
    assert violations[0]["label"] == "kappa_out_of_range"


def test_violation_carries_traceable_metadata():
    """Each record must include enough info to reproduce the judgment."""
    vr = {"CO": {"max": 1.92}}
    v = check_unit_ranges(vr)[0]
    assert "reason" in v
    assert "variable" in v
    assert "value" in v
    assert "expected_min" in v
    assert "expected_max" in v
    # Reason should hint at what to do next
    assert "molar concentration" in v["reason"] or "normalize" in v["reason"]


def test_handles_missing_stats_key():
    """If a variable has no `max` field, we don't crash."""
    vr = {"CH4": {"mean": 0.5}}  # no min/max
    assert check_unit_ranges(vr) == []
