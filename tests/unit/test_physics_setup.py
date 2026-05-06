"""Unit tests for the cross-solver physics_setup container.

Coverage:
  - PhysicsSetup pydantic model accepts dict / NotApplicable / NotExtracted
  - physics_setup_unextractable() helper builds a uniform NotExtracted bundle
  - OpenFOAM _build_physics_setup honors the double-write contract:
    extracted dict in tier3_out → use it; absent → sentinel with the
    right kind of "missing" reason
"""
from __future__ import annotations

from sim_parse.core.schema import (
    NotApplicable,
    NotExtracted,
    NotSet,
    PhysicsSetup,
    physics_setup_unextractable,
)
from sim_parse.solvers.openfoam.tier3_metadata import _build_physics_setup


# ─── PhysicsSetup model ───────────────────────────────────────────────────────


def test_physics_setup_accepts_extracted_dict():
    ps = PhysicsSetup(
        turbulence={"simulationType": "RAS", "RASModel": "kEpsilon"},
        thermophysics={"thermoType": {"type": "psiThermo"}},
    )
    assert ps.turbulence["RASModel"] == "kEpsilon"


def test_physics_setup_accepts_sentinels_per_field():
    ps = PhysicsSetup(
        turbulence=NotExtracted(reason="not parsed",
                                would_require="turb file reader"),
        chemistry=NotApplicable(reason="cold-flow"),
        combustion=NotSet(),
    )
    assert ps.turbulence.status == "not_extracted"
    assert ps.chemistry.status == "not_applicable"
    assert ps.combustion.status == "not_set"


def test_physics_setup_unspecified_components_default_to_none():
    """Forward compat: a consumer adding a new field doesn't need every
    solver to fill it immediately. None means 'unspecified'."""
    ps = PhysicsSetup(turbulence={"x": 1})
    assert ps.thermophysics is None
    assert ps.radiation is None


# ─── physics_setup_unextractable helper ───────────────────────────────────────


def test_unextractable_helper_marks_every_component():
    ps = physics_setup_unextractable(
        reason="Fluent legacy .cas binary section 39",
        would_require="binary section parser",
    )
    for field in ("turbulence", "thermophysics", "chemistry",
                  "combustion", "radiation", "multiphase"):
        v = getattr(ps, field)
        assert isinstance(v, NotExtracted)
        assert "section 39" in v.reason


# ─── OpenFOAM _build_physics_setup contract ───────────────────────────────────


def test_openfoam_extracted_components_pass_through():
    """When the legacy field is present, physics_setup uses it verbatim."""
    fake_t3 = {
        "turbulence": {"simulationType": "RAS", "RASModel": "kEpsilon"},
        "thermophysics": {"thermoType": {"type": "psiThermo"}},
        "chemistry": {"enabled": True, "chemistryType": {}},
        "combustion": {"combustionModel": "EDC", "active": True},
    }
    ps = _build_physics_setup(fake_t3)
    assert ps["turbulence"] == fake_t3["turbulence"]
    assert ps["chemistry"]["enabled"] is True
    assert ps["combustion"]["combustionModel"] == "EDC"


def test_openfoam_chemistry_combustion_missing_marked_not_applicable():
    """Cold-flow case: no chemistry/combustion files → NotApplicable, NOT
    NotExtracted (those files are legitimately absent, not unread)."""
    fake_t3 = {"turbulence": {"simulationType": "RAS"}}
    ps = _build_physics_setup(fake_t3)
    assert ps["chemistry"]["status"] == "not_applicable"
    assert "non-reacting" in ps["chemistry"]["reason"]
    assert ps["combustion"]["status"] == "not_applicable"


def test_openfoam_turbulence_missing_marked_not_extracted():
    """turbulence is universal in modern OF cases — absence means our
    parser couldn't read it (NotExtracted), not that the case lacks it."""
    fake_t3 = {}  # nothing extracted
    ps = _build_physics_setup(fake_t3)
    assert ps["turbulence"]["status"] == "not_extracted"
    assert "turbulenceProperties" in ps["turbulence"]["reason"]


def test_openfoam_radiation_multiphase_always_not_applicable_for_now():
    """We don't extract these yet; placeholder NotApplicable until added."""
    ps = _build_physics_setup({"turbulence": {"x": 1}})
    assert ps["radiation"]["status"] == "not_applicable"
    assert "not yet implemented" in ps["radiation"]["reason"]
    assert ps["multiphase"]["status"] == "not_applicable"


def test_openfoam_physics_setup_serializes_to_plain_dict():
    """Output is plain dict (not pydantic model) so JSON serialization
    works through the existing parse_case → MCP path without changes."""
    ps = _build_physics_setup({"combustion": {"combustionModel": "EDC"}})
    assert isinstance(ps, dict)
    # Every value is also a dict (extracted or sentinel.model_dump())
    for v in ps.values():
        assert isinstance(v, dict)
