"""Unit tests for #2: static-snapshot rule degeneration detection.

The qoi_engine must NOT fabricate values for rules that require time-series
data when only a single-time snapshot is available, and must NOT silently
return constants when an expression's inputs all resolve to the same path.
"""
from __future__ import annotations

from sim_parse.core.canonical.qoi_engine import (
    _detect_capabilities,
    _detect_degenerate_expression_inputs,
    _make_skipped_record,
    evaluate_qoi_rules,
)


# ─── Capability detection ─────────────────────────────────────────────────────


def test_detect_capabilities_no_time_series_when_tier4_missing():
    caps = _detect_capabilities({})
    assert caps["time_series_field_stats"] is False


def test_detect_capabilities_no_time_series_when_only_snapshot():
    """Today's Tier 4 only emits a single-time view → time_series is False."""
    parse = {"tier_4_field_stats": {
        "variable_ranges": {"T": {"min": 300, "max": 1500}},
        "field_stats_time": 5000.0,
    }}
    caps = _detect_capabilities(parse)
    assert caps["time_series_field_stats"] is False


def test_detect_capabilities_time_series_when_marker_set():
    """When future Tier 4 sets the explicit marker, capability flips True."""
    parse = {"tier_4_field_stats": {"time_series_data": {"T": {"per_time": []}}}}
    caps = _detect_capabilities(parse)
    assert caps["time_series_field_stats"] is True


# ─── Temporal-requirement gate ────────────────────────────────────────────────


def test_temporal_rule_skipped_in_snapshot_mode():
    rules = [{
        "name": "collapse_ratio",
        "type": "expression",
        "temporal_requirement": "time_series",
        "expression": "Q_last / Q_peak",
        "inputs": {
            "Q_last": {"path": "tier_4.time_series.Q.last"},
            "Q_peak": {"path": "tier_4.time_series.Q.peak"},
        },
    }]
    parse = {"tier_4_field_stats": {"variable_ranges": {}}}  # snapshot only
    out = evaluate_qoi_rules(rules, parse)
    assert len(out) == 1
    assert out[0]["status"] == "skipped_static_snapshot"
    assert out[0]["value"] is None
    assert out[0]["confidence"] == "N/A"
    # Reason must explicitly mention the rule name + the requirement
    assert "collapse_ratio" in out[0]["reason"]
    assert "time_series" in out[0]["reason"]


def test_snapshot_rule_fires_normally_in_snapshot_mode():
    """A rule without temporal_requirement must not be affected."""
    rules = [{
        "name": "max_T",
        "type": "pluck",
        "source": {"tier": "4", "path": "variable_ranges.T.max"},
    }]
    parse = {"tier_4_field_stats": {"variable_ranges": {"T": {"max": 1500.0}}}}
    out = evaluate_qoi_rules(rules, parse)
    assert len(out) == 1
    assert out[0]["value"] == 1500.0
    assert "status" not in out[0] or out[0].get("status") not in (
        "skipped_static_snapshot", "skipped_degenerate_inputs",
    )


def test_temporal_rule_fires_when_capability_present():
    """When parse_result advertises time_series_data, the rule evaluates."""
    rules = [{
        "name": "collapse_ratio",
        "type": "expression",
        "temporal_requirement": "time_series",
        "expression": "Q_last / Q_peak",
        "inputs": {
            "Q_last": {"path": "tier_4.time_series.Q.last"},
            "Q_peak": {"path": "tier_4.time_series.Q.peak"},
        },
    }]
    parse = {"tier_4_field_stats": {
        "time_series_data": True,
        "time_series": {"Q": {"last": 100.0, "peak": 200.0}},
    }}
    out = evaluate_qoi_rules(rules, parse)
    assert len(out) == 1
    # Either fires successfully or is skipped for a non-temporal reason
    assert out[0].get("status") != "skipped_static_snapshot"


# ─── Degenerate-inputs detection ──────────────────────────────────────────────


def test_detect_degenerate_inputs_flags_all_same_path():
    rule = {
        "type": "expression",
        "expression": "a / b",
        "inputs": {
            "a": {"path": "tier_4.variable_ranges.Qdot.max"},
            "b": {"path": "tier_4.variable_ranges.Qdot.max"},
        },
    }
    deg = _detect_degenerate_expression_inputs(rule)
    assert deg == ["a", "b"]


def test_detect_degenerate_inputs_passes_when_paths_differ():
    rule = {
        "type": "expression",
        "expression": "T_max - T_inlet",
        "inputs": {
            "T_max": {"path": "tier_4.variable_ranges.T.max"},
            "T_inlet": {"path": "tier_3.bc.T_inlet"},
        },
    }
    assert _detect_degenerate_expression_inputs(rule) is None


def test_detect_degenerate_inputs_passes_when_single_input():
    """A single input is never 'all-same' relative to anything."""
    rule = {
        "type": "expression",
        "expression": "T - 273",
        "inputs": {"T": {"path": "tier_4.variable_ranges.T.max"}},
    }
    assert _detect_degenerate_expression_inputs(rule) is None


def test_degenerate_rule_emits_skip_record_via_engine():
    """End-to-end: a rule with all inputs sharing one path is skipped with
    explicit reason, not silently dropped or returning a constant."""
    rules = [{
        "name": "ratio_of_self",
        "type": "expression",
        "expression": "a / b",
        "inputs": {
            "a": {"path": "tier_4.variable_ranges.Qdot.max"},
            "b": {"path": "tier_4.variable_ranges.Qdot.max"},
        },
    }]
    parse = {"tier_4_field_stats": {"variable_ranges": {"Qdot": {"max": 5e8}}}}
    out = evaluate_qoi_rules(rules, parse)
    assert len(out) == 1
    assert out[0]["status"] == "skipped_degenerate_inputs"
    assert out[0]["value"] is None
    assert "tier_4.variable_ranges.Qdot.max" in out[0]["reason"]


# ─── Skipped record schema ────────────────────────────────────────────────────


def test_make_skipped_record_has_required_fields():
    rec = _make_skipped_record(
        {"name": "foo", "unit": "K", "description": "blah"},
        status="skipped_static_snapshot",
        reason="needs time series",
    )
    assert rec["variable"] == "foo"
    assert rec["value"] is None
    assert rec["unit"] == "K"
    assert rec["status"] == "skipped_static_snapshot"
    assert rec["reason"] == "needs time series"
    assert rec["confidence"] == "N/A"
    assert rec["description"] == "blah"
