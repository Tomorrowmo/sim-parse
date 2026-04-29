"""Unit tests for #5: rule_file, inputs_resolved, and labeled_cases validation."""
from __future__ import annotations

import yaml

from sim_parse.core.canonical.qoi_engine import (
    _annotate_source,
    _resolve_inputs_traced,
    evaluate_qoi_rules,
)
from sim_parse.core.canonical.validation import (
    _compare,
    _normalize_path,
    validate_against_labels,
)


# ─── Rule-source annotation ───────────────────────────────────────────────────


def test_annotate_source_stamps_each_rule():
    rules = [{"name": "foo"}, {"name": "bar"}]
    _annotate_source(rules, "/fake/path/criteria.yaml")
    for r in rules:
        assert r["_source_file"] == "/fake/path/criteria.yaml"


def test_annotate_source_does_not_overwrite_existing():
    """If a rule already has _source_file (e.g., from sim-knowledge),
    annotate_source must not clobber it with builtin path."""
    rules = [{"name": "foo", "_source_file": "/sim-knowledge/x.yaml"}]
    _annotate_source(rules, "/builtin/qoi_rules.yaml")
    assert rules[0]["_source_file"] == "/sim-knowledge/x.yaml"


# ─── inputs_resolved trace ────────────────────────────────────────────────────


def test_inputs_resolved_tracks_path_and_value():
    parse = {"tier_4_field_stats": {"variable_ranges": {"T": {"max": 1500.0}}}}
    inputs_spec = {
        "T_max": {"path": "tier_4.variable_ranges.T.max"},
    }
    traced = _resolve_inputs_traced(inputs_spec, parse)
    assert traced is not None
    assert traced["T_max"]["value"] == 1500.0
    assert traced["T_max"]["from_path"] == "tier_4.variable_ranges.T.max"
    assert traced["T_max"]["fallback_used"] is False


def test_inputs_resolved_tracks_fallback_used():
    parse = {"tier_4_field_stats": {}}  # no T_max available
    inputs_spec = {
        "T_inlet": {"path": "tier_3.bc.T_inlet", "fallback": 300.0},
    }
    traced = _resolve_inputs_traced(inputs_spec, parse)
    assert traced is not None
    assert traced["T_inlet"]["value"] == 300.0
    assert traced["T_inlet"]["fallback_used"] is True


def test_inputs_resolved_returns_none_when_required_missing():
    parse = {"tier_4_field_stats": {}}
    inputs_spec = {
        "T_max": {"path": "tier_4.variable_ranges.T.max"},  # no fallback
    }
    assert _resolve_inputs_traced(inputs_spec, parse) is None


def test_pluck_record_carries_rule_file_and_inputs_resolved():
    rules = [{
        "name": "max_T",
        "type": "pluck",
        "source": {"tier": "4", "path": "variable_ranges.T.max"},
        "_source_file": "/path/to/heat_transfer/criteria.yaml",
    }]
    parse = {"tier_4_field_stats": {"variable_ranges": {"T": {"max": 1500.0}}}}
    out = evaluate_qoi_rules(rules, parse)
    assert out[0]["rule_file"] == "/path/to/heat_transfer/criteria.yaml"
    assert out[0]["inputs_resolved"]["_pluck"]["from_path"] == "tier_4.variable_ranges.T.max"
    assert out[0]["inputs_resolved"]["_pluck"]["value"] == 1500.0


def test_expression_record_carries_per_input_resolution():
    rules = [{
        "name": "T_excess",
        "type": "expression",
        "expression": "T_max - T_inlet",
        "_source_file": "/sk/combustion/criteria.yaml",
        "inputs": {
            "T_max": {"path": "tier_4.variable_ranges.T.max"},
            "T_inlet": {"path": "tier_3.bc.T_inlet", "fallback": 300.0},
        },
    }]
    parse = {"tier_4_field_stats": {"variable_ranges": {"T": {"max": 1500.0}}}}
    out = evaluate_qoi_rules(rules, parse)
    rec = out[0]
    assert rec["value"] == 1200.0
    assert rec["rule_file"] == "/sk/combustion/criteria.yaml"
    inp = rec["inputs_resolved"]
    assert inp["T_max"]["value"] == 1500.0
    assert inp["T_max"]["fallback_used"] is False
    assert inp["T_inlet"]["value"] == 300.0
    assert inp["T_inlet"]["fallback_used"] is True


# ─── _compare logic ───────────────────────────────────────────────────────────


def test_compare_booleans_match_exactly():
    match, diff, tol = _compare(True, True, None)
    assert match is True
    assert diff is None
    match, diff, tol = _compare(False, True, None)
    assert match is False


def test_compare_numerics_within_tolerance():
    """Default rel-tolerance is 5%; 1500 vs 1530 → within 5%."""
    match, diff, tol = _compare(1500.0, 1530.0, None)
    assert match is True
    assert abs(diff - 30.0) < 1e-9
    assert tol == 0.05


def test_compare_numerics_outside_tolerance():
    match, diff, tol = _compare(1500.0, 2000.0, None)
    assert match is False
    assert diff == 500.0


def test_compare_numerics_with_explicit_tolerance():
    """Per-label override beats the default."""
    match, _, tol = _compare(100.0, 110.0, 0.20)  # 20% tolerance
    assert match is True
    assert tol == 0.20


def test_compare_numerics_zero_expected_uses_absolute():
    """When expected==0, relative tolerance is meaningless; compare absolute."""
    match, _, _ = _compare(0.0, 0.001, 0.01)
    assert match is True
    match, _, _ = _compare(0.0, 1.0, 0.01)
    assert match is False


def test_compare_lists_set_equal():
    match, _, _ = _compare(["a", "b", "c"], ["c", "a", "b"], None)
    assert match is True
    match, _, _ = _compare(["a", "b"], ["a", "c"], None)
    assert match is False


# ─── Path normalization ───────────────────────────────────────────────────────


def test_normalize_path_handles_windows_unix():
    a = _normalize_path(r"D:\Foo\Bar")
    b = _normalize_path("D:/Foo/Bar")
    assert a == b


def test_normalize_path_strips_trailing_slash():
    assert _normalize_path("/a/b/") == _normalize_path("/a/b")


# ─── Validation E2E with synthetic data ───────────────────────────────────────


def test_validate_returns_none_when_no_labeled_case_matches(tmp_path):
    """A made-up case_root that no ground_truth references should yield None."""
    fake_case = tmp_path / "made_up_case_xyz_123"
    fake_case.mkdir()
    parse_result = {"case_root": str(fake_case), "tier_5_qoi": []}
    out = validate_against_labels(parse_result)
    assert out is None


def test_validate_against_synthetic_labels(tmp_path, monkeypatch):
    """Build a tiny labeled_cases tree under tmp_path, point validation at it,
    and verify the comparison logic end-to-end with mixed match/mismatch
    /missing/skipped statuses."""
    # Create fake sim-knowledge tree
    fake_sk = tmp_path / "sim-knowledge"
    fake_lc = fake_sk / "labeled_cases" / "test_case"
    fake_lc.mkdir(parents=True)
    case_dir = tmp_path / "the_case"
    case_dir.mkdir()
    gt = {
        "case_id": "test_case",
        "case_path": str(case_dir),
        "labels": {
            "is_ignited": True,                    # → match
            "max_temperature": 1500.0,             # → match (within 5%)
            "is_extinguished": True,               # → mismatch
            "missing_label": True,                 # → missing_in_qoi
            "skipped_thing": 42.0,                 # → skipped
        },
    }
    (fake_lc / "ground_truth.yaml").write_text(yaml.safe_dump(gt), encoding="utf-8")

    # Patch the validation module's _SIM_KNOWLEDGE constant
    import sim_parse.core.canonical.validation as v
    monkeypatch.setattr(v, "_SIM_KNOWLEDGE", fake_sk)

    parse_result = {
        "case_root": str(case_dir),
        "tier_5_qoi": [
            {"variable": "is_ignited", "value": True},
            {"variable": "max_temperature", "value": 1530.0, "unit": "K"},
            {"variable": "is_extinguished", "value": False},
            # missing_label: not present at all
            {"variable": "skipped_thing", "value": None, "status": "skipped_static_snapshot",
             "reason": "needs time series"},
        ],
    }
    result = v.validate_against_labels(parse_result)
    assert result is not None
    assert result["case_id"] == "test_case"

    by_label = {r["label"]: r for r in result["results"]}
    assert by_label["is_ignited"]["status"] == "match"
    assert by_label["max_temperature"]["status"] == "match"
    assert by_label["is_extinguished"]["status"] == "mismatch"
    assert by_label["missing_label"]["status"] == "missing_in_qoi"
    assert by_label["skipped_thing"]["status"] == "skipped"
    assert by_label["skipped_thing"]["skip_reason"] == "needs time series"

    s = result["summary"]
    assert s["n_match"] == 2
    assert s["n_mismatch"] == 1
    assert s["n_missing_in_qoi"] == 1
    assert s["n_skipped"] == 1
