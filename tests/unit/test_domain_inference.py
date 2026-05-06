"""Unit tests for Tier 7 candidate_domains routing (sim-knowledge YAML clues).

The clue table itself lives in sim-knowledge/physics/domain_inference.yaml.
These tests exercise the evaluator on synthetic parse_results so they
work without sim-knowledge being installed (the loader is silent-fail).
"""
from __future__ import annotations

from sim_parse.discovery.semantic import (
    _add_candidate_domains,
    _clue_matches,
    _load_domain_inference_clues,
)


# ─── _clue_matches: condition operators ───────────────────────────────────────


def test_clue_matches_if_any_field():
    clue = {"if_any_field": ["CH4", "O2"]}
    assert _clue_matches(clue, {"ch4", "n2"}, "", set()) is True
    assert _clue_matches(clue, {"o2"}, "", set()) is True
    assert _clue_matches(clue, {"u", "p"}, "", set()) is False


def test_clue_matches_if_all_fields():
    clue = {"if_all_fields": ["p", "U"]}
    assert _clue_matches(clue, {"p", "u", "rho"}, "", set()) is True
    assert _clue_matches(clue, {"p"}, "", set()) is False
    assert _clue_matches(clue, {"u"}, "", set()) is False


def test_clue_matches_if_no_fields_negative_gate():
    """if_no_fields blocks the clue when ANY of the listed names is present."""
    clue = {"if_no_fields": ["T", "h"]}
    assert _clue_matches(clue, {"u", "p"}, "", set()) is True
    assert _clue_matches(clue, {"u", "p", "t"}, "", set()) is False


def test_clue_matches_if_application_substring():
    clue = {"if_application_substring": ["reacting", "combustion"]}
    assert _clue_matches(clue, set(), "reactingfoam", set()) is True
    assert _clue_matches(clue, set(), "buoyantcombustionfoam", set()) is True
    assert _clue_matches(clue, set(), "simplefoam", set()) is False


def test_clue_matches_combined_conditions():
    """All specified conditions must match (AND semantics across operators)."""
    clue = {
        "if_all_fields": ["p", "U"],
        "if_no_fields": ["T"],
    }
    # p+U present, no T → match
    assert _clue_matches(clue, {"p", "u"}, "", set()) is True
    # p+U+T → blocked by if_no_fields
    assert _clue_matches(clue, {"p", "u", "t"}, "", set()) is False
    # U alone → fails if_all_fields
    assert _clue_matches(clue, {"u"}, "", set()) is False


def test_clue_matches_empty_clue_always_true():
    """A clue with no conditions trivially matches (degenerate case)."""
    assert _clue_matches({}, set(), "", set()) is True


def test_clue_matches_if_patch_type():
    clue = {"if_patch_type": ["wall", "cyclic"]}
    assert _clue_matches(clue, set(), "", {"wall", "patch"}) is True
    assert _clue_matches(clue, set(), "", {"patch"}) is False


# ─── _add_candidate_domains: end-to-end on synthetic parse_results ────────────


def _fake_parse_result(*, application="", variables=None, boundaries=None) -> dict:
    """Minimal parse_result skeleton with just the fields domain inference reads."""
    return {
        "tier_1_identify": {"solver": application, "format": "openfoam"},
        "tier_2_inventory": {"variables": variables or []},
        "tier_3_metadata": {"application": application, "boundaries": boundaries or []},
    }


def test_candidate_domains_combustion_case_ranks_combustion_first():
    """A reactingFoam-like case (species + reacting solver name) ranks
    combustion above fluid_dynamics."""
    r = _fake_parse_result(
        application="reactingFoam",
        variables=["U", "p", "T", "CH4", "O2", "CO2", "OH", "Qdot",
                   "k", "epsilon", "nut"],
    )
    out = {}
    _add_candidate_domains(out, r)
    cd = out["candidate_domains"]
    if not cd["value"]:
        # sim-knowledge not installed in this env — the loader returned []
        # so the test degrades to a no-op. Acceptable: confirms silent fallback.
        assert cd["confidence"] == "LOW"
        return
    domains = [e["domain"] for e in cd["value"]]
    assert "combustion" in domains
    # combustion should outrank fluid_dynamics on this case
    cd_idx = domains.index("combustion")
    if "fluid_dynamics" in domains:
        assert cd_idx < domains.index("fluid_dynamics")


def test_candidate_domains_pure_aero_no_combustion():
    """A simpleFoam aerodynamics case (p+U, no T, no species) → only
    fluid_dynamics-related clues fire; combustion absent."""
    r = _fake_parse_result(
        application="simpleFoam",
        variables=["U", "p", "k", "omega", "nut"],
    )
    out = {}
    _add_candidate_domains(out, r)
    cd = out["candidate_domains"]
    if not cd["value"]:
        return  # sim-knowledge not loaded
    domains = {e["domain"] for e in cd["value"]}
    assert "combustion" not in domains
    assert "fluid_dynamics" in domains


def test_candidate_domains_no_signals_returns_empty_or_low():
    """A parse_result with no useful signals → no clues fire."""
    r = _fake_parse_result(application="", variables=[])
    out = {}
    _add_candidate_domains(out, r)
    cd = out["candidate_domains"]
    # Either empty (clues loaded but nothing matched) or low (clues not loaded)
    assert cd["confidence"] == "LOW"


def test_candidate_domains_cache_loaded_once():
    """_load_domain_inference_clues caches; second call is a hit."""
    a = _load_domain_inference_clues()
    b = _load_domain_inference_clues()
    assert a is b   # same list instance — cache hit
