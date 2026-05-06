"""Unit tests for the cross-solver physics_setup container.

Coverage:
  - PhysicsSetup pydantic model accepts dict / NotApplicable / NotExtracted
  - physics_setup_unextractable() helper builds a uniform NotExtracted bundle
  - OpenFOAM _build_physics_setup uses file-presence to distinguish:
      file present + extracted     → use the extracted dict
      file present + not extracted → NotExtracted (parser debt)
      file absent                  → NotApplicable (case doesn't have it)
"""
from __future__ import annotations

from pathlib import Path

from sim_parse.core.schema import (
    NotApplicable,
    NotExtracted,
    NotSet,
    PhysicsSetup,
    physics_setup_unextractable,
)
from sim_parse.solvers.openfoam.tier3_metadata import _build_physics_setup


def _empty_case(tmp_path: Path) -> Path:
    """Make a fake OF case dir with empty constant/ — no physics files."""
    (tmp_path / "constant").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _case_with_files(tmp_path: Path, *files: str) -> Path:
    """Make a fake OF case dir with constant/ + listed file names touched."""
    constant = tmp_path / "constant"
    constant.mkdir(parents=True, exist_ok=True)
    for f in files:
        (constant / f).write_text("// dummy\n", encoding="utf-8")
    return tmp_path


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


def test_openfoam_extracted_components_pass_through(tmp_path):
    """When the legacy field is present, physics_setup uses it verbatim
    regardless of file presence (extraction wins over file probing)."""
    case = _empty_case(tmp_path)
    fake_t3 = {
        "turbulence": {"simulationType": "RAS", "RASModel": "kEpsilon"},
        "thermophysics": {"thermoType": {"type": "psiThermo"}},
        "chemistry": {"enabled": True, "chemistryType": {}},
        "combustion": {"combustionModel": "EDC", "active": True},
    }
    ps = _build_physics_setup(fake_t3, case)
    assert ps["turbulence"] == fake_t3["turbulence"]
    assert ps["chemistry"]["enabled"] is True
    assert ps["combustion"]["combustionModel"] == "EDC"


def test_openfoam_no_chem_or_comb_file_marks_not_applicable(tmp_path):
    """Cold-flow case (no chemistryProperties / combustionProperties files
    in constant/): chemistry & combustion → NotApplicable. This is the
    legitimate cold-flow signal."""
    case = _empty_case(tmp_path)
    ps = _build_physics_setup({"turbulence": {"simulationType": "RAS"}}, case)
    assert ps["chemistry"]["status"] == "not_applicable"
    assert "not present" in ps["chemistry"]["reason"]
    assert ps["combustion"]["status"] == "not_applicable"


def test_openfoam_chem_file_present_but_no_extraction_marks_not_extracted(tmp_path):
    """Critical case: the file IS in constant/ but our extractor produced
    nothing → this is parser debt (NotExtracted), NOT NotApplicable.

    Old behavior would silently mask reactingFoam-with-broken-extractor as
    a non-reacting case; this test guards against the regression."""
    case = _case_with_files(tmp_path, "chemistryProperties", "combustionProperties")
    ps = _build_physics_setup({"turbulence": {"x": 1}}, case)
    assert ps["chemistry"]["status"] == "not_extracted", \
        "chemistryProperties file exists but extractor failed → must be NotExtracted, not NotApplicable"
    assert "extractor produced no result" in ps["chemistry"]["reason"]
    assert ps["combustion"]["status"] == "not_extracted"


def test_openfoam_radiation_file_presence_check(tmp_path):
    """radiation honors the same file-presence pattern: file present +
    no extraction → NotExtracted; file absent → NotApplicable."""
    # Without file → NotApplicable
    case_a = _empty_case(tmp_path / "no_rad")
    ps_a = _build_physics_setup({}, case_a)
    assert ps_a["radiation"]["status"] == "not_applicable"
    # With file → NotExtracted (we don't have a radiation extractor yet,
    # but if the file is there, the user's case has radiation configured)
    case_b = _case_with_files(tmp_path / "with_rad", "radiationProperties")
    ps_b = _build_physics_setup({}, case_b)
    assert ps_b["radiation"]["status"] == "not_extracted"


def test_openfoam_turbulence_missing_marked_not_extracted(tmp_path):
    """turbulence is universal in modern OF cases — file-presence check
    not used here; absence always means our parser couldn't read it."""
    case = _empty_case(tmp_path)
    ps = _build_physics_setup({}, case)
    assert ps["turbulence"]["status"] == "not_extracted"
    assert "turbulenceProperties" in ps["turbulence"]["reason"]


def test_openfoam_thermophysics_absent_marks_not_applicable(tmp_path):
    """Incompressible cases (simpleFoam etc.) have no thermophysicalProperties
    file — they use transportProperties instead. Honest reason = NotApplicable.

    Lock-in test for the airFoil2D regression — earlier heuristic incorrectly
    flagged thermophysics as NotExtracted for cold-flow cases, suggesting
    parser debt where there was none."""
    case = _empty_case(tmp_path)
    ps = _build_physics_setup({}, case)
    assert ps["thermophysics"]["status"] == "not_applicable"
    assert "thermophysicalProperties" in ps["thermophysics"]["reason"]


def test_openfoam_multiphase_always_not_applicable_for_now(tmp_path):
    """multiphase has no canonical single-file gate yet; documented as
    NotApplicable with explicit "not yet detected" reason."""
    case = _empty_case(tmp_path)
    ps = _build_physics_setup({"turbulence": {"x": 1}}, case)
    assert ps["multiphase"]["status"] == "not_applicable"
    assert "not yet implemented" in ps["multiphase"]["reason"]


def test_openfoam_physics_setup_serializes_to_plain_dict(tmp_path):
    """Output is plain dict (not pydantic model) so JSON serialization
    works through the existing parse_case → MCP path without changes."""
    case = _empty_case(tmp_path)
    ps = _build_physics_setup({"combustion": {"combustionModel": "EDC"}}, case)
    assert isinstance(ps, dict)
    for v in ps.values():
        assert isinstance(v, dict)
