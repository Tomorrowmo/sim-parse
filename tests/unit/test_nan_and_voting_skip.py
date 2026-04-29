"""Unit tests for the follow-up rule fixes:
  - Tier 4 NaN/Inf field detection
  - voting rules emit skipped_no_data when all criteria are missing
  - complete_combustion rule needs both criteria to fire (not just one)
"""
from __future__ import annotations

import math

from sim_parse.core.canonical.qoi_engine import evaluate_qoi_rules
from sim_parse.core.diagnostics import (
    _is_finite_number,
    detect_nonfinite_fields as _detect_nonfinite_fields,
)


# ─── _is_finite_number ────────────────────────────────────────────────────────


def test_is_finite_accepts_normal_numbers():
    assert _is_finite_number(1.0)
    assert _is_finite_number(0)
    assert _is_finite_number(-273.15)
    assert _is_finite_number(1e30)


def test_is_finite_rejects_nan_and_inf():
    assert not _is_finite_number(float("nan"))
    assert not _is_finite_number(float("inf"))
    assert not _is_finite_number(float("-inf"))


def test_is_finite_rejects_non_numeric():
    assert not _is_finite_number("3.14")
    assert not _is_finite_number(None)
    assert not _is_finite_number([])
    assert not _is_finite_number(True)  # bool excluded; not a "stat number"


# ─── _detect_nonfinite_fields ─────────────────────────────────────────────────


def test_detect_nonfinite_finds_nan_in_min():
    vr = {
        "T": {"min": float("nan"), "max": 1500.0, "mean": 800.0},
        "p": {"min": 1e5, "max": 2e5, "mean": 1.5e5},
    }
    assert _detect_nonfinite_fields(vr) == {"T"}


def test_detect_nonfinite_finds_inf_in_mean():
    vr = {"U": {"magnitude_min": 0.0, "magnitude_max": 100.0,
                "magnitude_mean": float("inf"),
                "component_min": [0, 0, 0], "component_max": [100, 0, 0]}}
    assert _detect_nonfinite_fields(vr) == {"U"}


def test_detect_nonfinite_scans_component_lists():
    """NaN buried inside a component_max list must still be caught."""
    vr = {
        "U": {
            "magnitude_min": 0.0, "magnitude_max": 100.0, "magnitude_mean": 10.0,
            "component_min": [0.0, 0.0, 0.0],
            "component_max": [100.0, float("nan"), 0.0],
        }
    }
    assert _detect_nonfinite_fields(vr) == {"U"}


def test_detect_nonfinite_returns_empty_for_clean_data():
    vr = {"T": {"min": 300, "max": 1500, "mean": 800}}
    assert _detect_nonfinite_fields(vr) == set()


def test_detect_nonfinite_handles_missing_or_malformed_entries():
    """Strings, None, missing fields, etc. must not crash the scanner."""
    vr = {
        "T":   {"min": 300.0, "max": 1500.0},
        "weird": {"min": "300", "shape": [10, 3]},  # malformed types
        "good": {"min": 0.5, "max": 0.6, "mean": 0.55},
    }
    assert _detect_nonfinite_fields(vr) == set()


# ─── voting rule: skipped_no_data when all criteria missing ───────────────────


def test_voting_rule_with_all_paths_missing_emits_skipped():
    rules = [{
        "name": "excessive_courant",
        "type": "voting",
        "voting_threshold": 1,
        "criteria": [{
            "name": "courant_max_too_high",
            "path": "tier_4.variable_ranges.CourantNumber.max",
            "op": ">",
            "threshold": 5.0,
            "skip_if_missing": True,
        }],
    }]
    parse = {"tier_4_field_stats": {"variable_ranges": {}}}  # no CourantNumber
    out = evaluate_qoi_rules(rules, parse)
    assert len(out) == 1
    rec = out[0]
    assert rec["variable"] == "excessive_courant"
    assert rec["value"] is None
    assert rec["status"] == "skipped_no_data"
    assert rec["confidence"] == "N/A"
    assert "no evaluable criteria" in rec["reason"]


def test_voting_rule_with_some_paths_missing_still_evaluates():
    """If at least one criterion has data, the rule fires normally."""
    rules = [{
        "name": "is_diverged",
        "type": "voting",
        "voting_threshold": 1,
        "criteria": [
            {
                "name": "missing_one",
                "path": "tier_4.variable_ranges.GhostField.max",
                "op": ">",
                "threshold": 1.0,
                "skip_if_missing": True,
            },
            {
                "name": "real_one",
                "path": "tier_4.variable_ranges.T.max",
                "op": ">",
                "threshold": 1.0e8,
                "skip_if_missing": True,
            },
        ],
    }]
    parse = {"tier_4_field_stats": {"variable_ranges": {"T": {"max": 1500.0}}}}
    out = evaluate_qoi_rules(rules, parse)
    assert len(out) == 1
    # T.max is 1500 < 1e8, so doesn't match → False
    assert out[0]["value"] is False
    # status field should NOT be skipped — the rule ran
    assert out[0].get("status") != "skipped_no_data"


# ─── complete_combustion: voting_threshold=2 ──────────────────────────────────


def test_complete_combustion_diffusion_flame_returns_false():
    """DLR_A-style: high CO2 (combustion did happen) but high CH4 (jet core
    has unburnt fuel). With voting_threshold=2 (AND), result must be False."""
    rules = [{
        "name": "complete_combustion",
        "type": "voting",
        "voting_threshold": 2,
        "required_paths": ["tier_3.combustion.combustionModel"],
        "criteria": [
            {"name": "high_co2",
             "path": "tier_4.variable_ranges.CO2.max",
             "op": ">", "threshold": 0.05, "skip_if_missing": True},
            {"name": "low_fuel_residual",
             "path": "tier_4.variable_ranges.CH4.max",
             "op": "<", "threshold": 0.005, "skip_if_missing": True},
        ],
    }]
    parse = {
        "tier_3_metadata": {"combustion": {"combustionModel": "EDC"}},
        "tier_4_field_stats": {"variable_ranges": {
            "CO2": {"max": 0.0875},   # > 0.05  ✓
            "CH4": {"max": 0.246},    # NOT < 0.005  ✗
        }},
    }
    out = evaluate_qoi_rules(rules, parse)
    assert len(out) == 1
    assert out[0]["value"] is False  # 1/2 < 2


def test_complete_combustion_premixed_burnt_returns_true():
    """A perfectly-mixed burnt mixture: both criteria satisfied → True."""
    rules = [{
        "name": "complete_combustion",
        "type": "voting",
        "voting_threshold": 2,
        "required_paths": ["tier_3.combustion.combustionModel"],
        "criteria": [
            {"name": "high_co2",
             "path": "tier_4.variable_ranges.CO2.max",
             "op": ">", "threshold": 0.05, "skip_if_missing": True},
            {"name": "low_fuel_residual",
             "path": "tier_4.variable_ranges.CH4.max",
             "op": "<", "threshold": 0.005, "skip_if_missing": True},
        ],
    }]
    parse = {
        "tier_3_metadata": {"combustion": {"combustionModel": "EDC"}},
        "tier_4_field_stats": {"variable_ranges": {
            "CO2": {"max": 0.15},     # > 0.05  ✓
            "CH4": {"max": 0.001},    # < 0.005  ✓
        }},
    }
    out = evaluate_qoi_rules(rules, parse)
    assert out[0]["value"] is True


def test_complete_combustion_no_co2_data_returns_false():
    """Only CH4 reported, no CO2: 1/1 evaluable matches but threshold=2 → False."""
    rules = [{
        "name": "complete_combustion",
        "type": "voting",
        "voting_threshold": 2,
        "required_paths": ["tier_3.combustion.combustionModel"],
        "criteria": [
            {"name": "high_co2",
             "path": "tier_4.variable_ranges.CO2.max",
             "op": ">", "threshold": 0.05, "skip_if_missing": True},
            {"name": "low_fuel_residual",
             "path": "tier_4.variable_ranges.CH4.max",
             "op": "<", "threshold": 0.005, "skip_if_missing": True},
        ],
    }]
    parse = {
        "tier_3_metadata": {"combustion": {"combustionModel": "EDC"}},
        "tier_4_field_stats": {"variable_ranges": {"CH4": {"max": 0.001}}},
    }
    out = evaluate_qoi_rules(rules, parse)
    assert out[0]["value"] is False  # 1/1 evaluable < threshold 2
