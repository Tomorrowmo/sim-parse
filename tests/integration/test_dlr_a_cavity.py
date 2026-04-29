"""End-to-end tests against the DLR_A_cavity reference case.

Skipped automatically if D:\\Git\\SimGraph2\\test_data\\DLR_A_combustion\\DLR_A_cavity is missing.
"""
from __future__ import annotations

import pytest

from sim_parse import parse_case
from sim_parse.solvers.openfoam.tier1_identify import identify
from sim_parse.solvers.openfoam.tier2_inventory import inventory
from sim_parse.solvers.openfoam.tier3_metadata import metadata
from sim_parse.solvers.openfoam.tier4_field_stats import field_stats


# Skip Tier 4 tests if VTK is missing
vtk_required = pytest.importorskip("vtk", reason="VTK needed for Tier 4 tests")


# ─── Tier 1: Identify ─────────────────────────────────────────────────────────


def test_tier1_identifies_openfoam(dlr_a_cavity):
    result = identify(dlr_a_cavity)
    assert result is not None
    assert result["format"] == "openfoam"


def test_tier1_finds_reactingfoam_solver(dlr_a_cavity):
    result = identify(dlr_a_cavity)
    assert result["solver"] == "reactingFoam"


def test_tier1_extracts_version(dlr_a_cavity):
    result = identify(dlr_a_cavity)
    assert result["version"] == "v2212"


def test_tier1_layout_single_region(dlr_a_cavity):
    result = identify(dlr_a_cavity)
    assert result["layout"] == "single-region"


def test_tier1_no_lagrangian(dlr_a_cavity):
    result = identify(dlr_a_cavity)
    assert result["lagrangian"] is False


def test_tier1_empty_dir_returns_none(empty_dir):
    result = identify(empty_dir)
    assert result is None


def test_tier1_does_not_raise_on_garbage(tmp_path):
    """Garbage input must not raise (never-raise contract)."""
    garbage = tmp_path / "garbage"
    garbage.write_text("not a directory at all", encoding="utf-8")
    result = identify(garbage)
    assert result is None or result.get("format") is None


# ─── Tier 2: Inventory ────────────────────────────────────────────────────────


def test_tier2_lists_time_steps(dlr_a_cavity):
    identity = identify(dlr_a_cavity)
    inv = inventory(dlr_a_cavity, identity)
    assert inv is not None
    assert 0 in inv["time_steps"]
    # DLR_A_cavity ran to 5000 with writeInterval=500
    assert 5000 in inv["time_steps"]


def test_tier2_lists_variables(dlr_a_cavity):
    identity = identify(dlr_a_cavity)
    inv = inventory(dlr_a_cavity, identity)
    assert "T" in inv["variables"]
    assert "U" in inv["variables"]
    assert "p" in inv["variables"]
    # Combustion species (0/ has limited set; 5000/ has full GRI mech)
    assert any(v in inv["variables"] for v in ("CH4", "CO", "CO2", "H2O"))


def test_tier2_lists_scripts(dlr_a_cavity):
    identity = identify(dlr_a_cavity)
    inv = inventory(dlr_a_cavity, identity)
    assert "Allrun" in inv["scripts"]
    assert "Allclean" in inv["scripts"]


def test_tier2_lists_log_files(dlr_a_cavity):
    identity = identify(dlr_a_cavity)
    inv = inventory(dlr_a_cavity, identity)
    log_files = inv["log_files"]
    # At least the pre-processing logs
    assert any("blockMesh" in lf for lf in log_files)
    assert any("chemkinToFoam" in lf for lf in log_files)


def test_tier2_lists_system_files(dlr_a_cavity):
    identity = identify(dlr_a_cavity)
    inv = inventory(dlr_a_cavity, identity)
    sys_files = inv["system_files"]
    assert "controlDict" in sys_files
    assert "fvSchemes" in sys_files
    assert "fvSolution" in sys_files
    assert "blockMeshDict" in sys_files
    assert "decomposeParDict" in sys_files


def test_tier2_finds_chemkin_auxiliary(dlr_a_cavity):
    identity = identify(dlr_a_cavity)
    inv = inventory(dlr_a_cavity, identity)
    assert "chemkin" in inv["auxiliary"]


def test_tier2_initial_condition_dir(dlr_a_cavity):
    """DLR_A_cavity has 0.orig (template) and 0 (restored)."""
    identity = identify(dlr_a_cavity)
    inv = inventory(dlr_a_cavity, identity)
    assert "0.orig" in inv.get("initial_condition_dirs", [])


# ─── Tier 3: Metadata ─────────────────────────────────────────────────────────


def test_tier3_application(dlr_a_cavity):
    identity = identify(dlr_a_cavity)
    inv = inventory(dlr_a_cavity, identity)
    md = metadata(dlr_a_cavity, identity, inv)
    assert md["application"] == "reactingFoam"


def test_tier3_mesh_counts(dlr_a_cavity):
    identity = identify(dlr_a_cavity)
    inv = inventory(dlr_a_cavity, identity)
    md = metadata(dlr_a_cavity, identity, inv)
    # From polyMesh/owner header note "nPoints:7109 nCells:3466 nFaces:13905 nInternalFaces:6797"
    assert md["mesh_cells"] == 3466
    assert md["mesh_points"] == 7109
    assert md["mesh_faces"] == 13905
    assert md["mesh_internal_faces"] == 6797


def test_tier3_boundary_patches(dlr_a_cavity):
    identity = identify(dlr_a_cavity)
    inv = inventory(dlr_a_cavity, identity)
    md = metadata(dlr_a_cavity, identity, inv)
    patches = md["boundaries"]
    names = {p["name"] for p in patches}
    expected = {"inletfuel", "inletair", "outlet", "axis", "leftside",
                "burnerwall", "burnertip", "front", "back"}
    assert expected.issubset(names)
    # Verify type of inletfuel
    inletfuel = next(p for p in patches if p["name"] == "inletfuel")
    assert inletfuel["type"] == "patch"
    # Verify wedge BC for front/back
    front = next(p for p in patches if p["name"] == "front")
    assert front["type"] == "wedge"


def test_tier3_turbulence(dlr_a_cavity):
    identity = identify(dlr_a_cavity)
    inv = inventory(dlr_a_cavity, identity)
    md = metadata(dlr_a_cavity, identity, inv)
    turb = md["turbulence"]
    assert turb["simulationType"] == "RAS"
    assert turb.get("RASModel") == "kEpsilon"


def test_tier3_thermophysics(dlr_a_cavity):
    identity = identify(dlr_a_cavity)
    inv = inventory(dlr_a_cavity, identity)
    md = metadata(dlr_a_cavity, identity, inv)
    thermo = md["thermophysics"]
    assert thermo["thermoType"]["type"] == "hePsiThermo"
    assert thermo["thermoType"]["mixture"] == "reactingMixture"
    assert thermo["inertSpecie"] == "N2"


def test_tier3_chemistry(dlr_a_cavity):
    identity = identify(dlr_a_cavity)
    inv = inventory(dlr_a_cavity, identity)
    md = metadata(dlr_a_cavity, identity, inv)
    chem = md["chemistry"]
    assert chem["enabled"] is True
    assert chem.get("chemistry") == "on"


def test_tier3_combustion(dlr_a_cavity):
    identity = identify(dlr_a_cavity)
    inv = inventory(dlr_a_cavity, identity)
    md = metadata(dlr_a_cavity, identity, inv)
    comb = md["combustion"]
    assert comb["combustionModel"] == "EDC"


def test_tier3_decomposition(dlr_a_cavity):
    identity = identify(dlr_a_cavity)
    inv = inventory(dlr_a_cavity, identity)
    md = metadata(dlr_a_cavity, identity, inv)
    decomp = md["decomposition"]
    assert decomp["numberOfSubdomains"] == 6
    assert decomp["method"] == "simple"


def test_tier3_time_control(dlr_a_cavity):
    identity = identify(dlr_a_cavity)
    inv = inventory(dlr_a_cavity, identity)
    md = metadata(dlr_a_cavity, identity, inv)
    tc = md["time_control"]
    assert tc["endTime"] == 5000
    assert tc["deltaT"] == 1
    assert tc["writeInterval"] == 500
    assert tc["writeFormat"] == "binary"


def test_tier3_case_origin_path(dlr_a_cavity):
    identity = identify(dlr_a_cavity)
    inv = inventory(dlr_a_cavity, identity)
    md = metadata(dlr_a_cavity, identity, inv)
    # Should pick up 'Case : ...' from log.blockMesh banner
    origin = md.get("case_origin_path")
    assert origin is not None
    assert "DLR_A_LTS" in origin or "reactingFoam" in origin


def test_tier3_is_completed(dlr_a_cavity):
    identity = identify(dlr_a_cavity)
    inv = inventory(dlr_a_cavity, identity)
    md = metadata(dlr_a_cavity, identity, inv)
    # Latest time = 5000, endTime = 5000 → completed
    assert md.get("is_completed") is True


# ─── Tier 4: FieldStats (VTK) ─────────────────────────────────────────────────


def test_tier4_runs_without_error(dlr_a_cavity):
    identity = identify(dlr_a_cavity)
    inv = inventory(dlr_a_cavity, identity)
    md = metadata(dlr_a_cavity, identity, inv)
    t4 = field_stats(dlr_a_cavity, identity, md)
    assert t4 is not None
    assert t4.get("_errors") == []


def test_tier4_temperature_range(dlr_a_cavity):
    identity = identify(dlr_a_cavity)
    inv = inventory(dlr_a_cavity, identity)
    md = metadata(dlr_a_cavity, identity, inv)
    t4 = field_stats(dlr_a_cavity, identity, md)
    T_stats = t4["variable_ranges"]["T"]
    # Inlet ~292 K
    assert 290 <= T_stats["min"] <= 295
    # Combustion case: peak T well above 1500 K
    assert T_stats["max"] > 1500


def test_tier4_velocity_magnitude(dlr_a_cavity):
    identity = identify(dlr_a_cavity)
    inv = inventory(dlr_a_cavity, identity)
    md = metadata(dlr_a_cavity, identity, inv)
    t4 = field_stats(dlr_a_cavity, identity, md)
    U_stats = t4["variable_ranges"]["U"]
    # inletfuel U = 42.2 m/s; jet flame max ~ inletfuel velocity
    assert U_stats["magnitude_max"] >= 30
    assert U_stats["magnitude_max"] <= 100


def test_tier4_bounding_box(dlr_a_cavity):
    identity = identify(dlr_a_cavity)
    inv = inventory(dlr_a_cavity, identity)
    md = metadata(dlr_a_cavity, identity, inv)
    t4 = field_stats(dlr_a_cavity, identity, md)
    bbox = t4["bounding_box"]
    # blockMeshDict scale=0.001; L=1000 → z up to 1.0 m
    assert 0.9 <= bbox["z"][1] <= 1.5
    # axisymmetric wedge → narrow y range
    assert abs(bbox["y"][1] - bbox["y"][0]) < 0.05


def test_tier4_combustion_active(dlr_a_cavity):
    """For a burning case, Qdot.max should be very high and OH should exist."""
    identity = identify(dlr_a_cavity)
    inv = inventory(dlr_a_cavity, identity)
    md = metadata(dlr_a_cavity, identity, inv)
    t4 = field_stats(dlr_a_cavity, identity, md)
    Qdot_stats = t4["variable_ranges"]["Qdot"]
    OH_stats = t4["variable_ranges"]["OH"]
    # Burning combustor: Qdot peak in MW-GW/m^3 range
    assert Qdot_stats["max"] > 1e7
    # OH present (flame exists)
    assert OH_stats["max"] > 1e-5


def test_tier4_convergence_orders(dlr_a_cavity):
    identity = identify(dlr_a_cavity)
    inv = inventory(dlr_a_cavity, identity)
    md = metadata(dlr_a_cavity, identity, inv)
    t4 = field_stats(dlr_a_cavity, identity, md)
    orders = t4.get("convergence_orders", {})
    # Many variables should have dropped at least 3 orders of magnitude
    well_converged = [v for v in orders.values() if v >= 3]
    assert len(well_converged) >= 5


def test_tier4_field_stats_time(dlr_a_cavity):
    identity = identify(dlr_a_cavity)
    inv = inventory(dlr_a_cavity, identity)
    md = metadata(dlr_a_cavity, identity, inv)
    t4 = field_stats(dlr_a_cavity, identity, md)
    # Default: latest time = 5000
    assert t4["field_stats_time"] == 5000.0


# ─── End-to-end via parse_case dispatcher ─────────────────────────────────────


def test_parse_case_tier1(dlr_a_cavity):
    result = parse_case(dlr_a_cavity, target_tier=1)
    assert result["tier_1_identify"]["format"] == "openfoam"
    assert result["tier_1_identify"]["solver"] == "reactingFoam"


def test_parse_case_tier2(dlr_a_cavity):
    result = parse_case(dlr_a_cavity, target_tier=2)
    assert "tier_2_inventory" in result
    assert "T" in result["tier_2_inventory"]["variables"]


def test_parse_case_tier3(dlr_a_cavity):
    result = parse_case(dlr_a_cavity, target_tier=3)
    md = result["tier_3_metadata"]
    assert md["mesh_cells"] == 3466
    assert md["application"] == "reactingFoam"
    assert md["combustion"]["combustionModel"] == "EDC"


def test_parse_case_tier3_field_count(dlr_a_cavity):
    """Phase 1 acceptance #1: tier 3+ should have ≥ 30 non-meta fields total."""
    result = parse_case(dlr_a_cavity, target_tier=3)
    from sim_parse.core.tiers import count_present_fields
    total = (
        count_present_fields(result.get("tier_1_identify", {}))
        + count_present_fields(result.get("tier_2_inventory", {}))
        + count_present_fields(result.get("tier_3_metadata", {}))
    )
    assert total >= 30, f"Expected ≥30 fields, got {total}"


def test_parse_case_tier4_e2e(dlr_a_cavity):
    """Phase 1 acceptance: full e2e parse_case with target_tier=4."""
    result = parse_case(dlr_a_cavity, target_tier=4)
    assert "tier_4_field_stats" in result
    t4 = result["tier_4_field_stats"]
    assert "variable_ranges" in t4
    assert "T" in t4["variable_ranges"]
    assert t4["variable_ranges"]["T"]["max"] > 1500


def test_parse_case_accepts_qoi_rules_path(dlr_a_cavity, tmp_path):
    """Phase 1 acceptance #5: qoi_rules_path accepted without crash (loading deferred to Phase 2)."""
    rules = tmp_path / "extinction.yaml"
    rules.write_text("dummy: rules\n", encoding="utf-8")
    result = parse_case(dlr_a_cavity, target_tier=3, qoi_rules_path=rules)
    assert "tier_3_metadata" in result
    assert result["errors"] == []


# ─── Tier 5: QOI ──────────────────────────────────────────────────────────────


def test_tier5_produces_records(dlr_a_cavity):
    result = parse_case(dlr_a_cavity, target_tier=5)
    qoi = result.get("tier_5_qoi", [])
    assert len(qoi) >= 10, f"Expected ≥10 QOI records, got {len(qoi)}"


def test_tier5_mesh_cells_pluck(dlr_a_cavity):
    result = parse_case(dlr_a_cavity, target_tier=5)
    qoi_by_name = {q["variable"]: q for q in result["tier_5_qoi"]}
    assert qoi_by_name["mesh_cells"]["value"] == 3466
    assert qoi_by_name["mesh_cells"]["unit"] == "1"
    assert qoi_by_name["mesh_cells"]["confidence"] == "HIGH"


def test_tier5_max_temperature(dlr_a_cavity):
    result = parse_case(dlr_a_cavity, target_tier=5)
    qoi_by_name = {q["variable"]: q for q in result["tier_5_qoi"]}
    T_max = qoi_by_name["max_temperature"]
    assert T_max["unit"] == "K"
    assert T_max["value"] > 1500
    assert T_max["value"] < 2500


def test_tier5_is_extinguished_voting(dlr_a_cavity):
    """DLR_A_cavity is a vigorously burning case → is_extinguished must be False."""
    result = parse_case(dlr_a_cavity, target_tier=5)
    qoi_by_name = {q["variable"]: q for q in result["tier_5_qoi"]}
    rec = qoi_by_name["is_extinguished"]
    assert rec["value"] is False
    assert rec["unit"] == "boolean"
    assert "evidence" in rec
    # Voting evidence includes per-criterion match info
    assert all("matched" in e for e in rec["evidence"])


def test_tier5_phase2_expression_extinction(dlr_a_cavity):
    """Phase 2: is_extinguished now uses real (T_max - T_inlet) expression."""
    result = parse_case(dlr_a_cavity, target_tier=5)
    qoi_by_name = {q["variable"]: q for q in result["tier_5_qoi"]}
    rec = qoi_by_name["is_extinguished"]
    # DLR_A_cavity is burning vigorously, must be False
    assert rec["value"] is False
    # Find the temperature_excess criterion in evidence
    temp_excess = next(
        e for e in rec["evidence"] if e.get("name") == "low_temperature_excess"
    )
    # Real expression: T_max(~1998) - T_inlet(~292) ~= 1700K → way above threshold 100
    assert temp_excess["value"] > 1500
    # Must not be matched (not extinguished)
    assert temp_excess["matched"] is False


def test_tier5_phase2_temperature_excess_qoi(dlr_a_cavity):
    """Phase 2: new top-level expression-type QOI."""
    result = parse_case(dlr_a_cavity, target_tier=5)
    qoi_by_name = {q["variable"]: q for q in result["tier_5_qoi"]}
    rec = qoi_by_name["temperature_excess_above_inlet"]
    # T_max ~1998 - T_inlet 292 ~= 1706 K
    assert 1500 < rec["value"] < 1900
    assert rec["unit"] == "K"


def test_tier3_extracts_bc_inlet_T(dlr_a_cavity):
    """Phase 2: Tier 3 now extracts BC inlet values."""
    result = parse_case(dlr_a_cavity, target_tier=3)
    bc = result["tier_3_metadata"].get("bc")
    assert bc is not None
    # DLR_A_cavity has inletfuel and inletair both at 292 K
    assert bc["T_inlet"] == 292.0
    assert "inletfuel" in bc["T_inlet_per_patch"]
    assert "inletair" in bc["T_inlet_per_patch"]


def test_tier3_extracts_bc_inlet_U(dlr_a_cavity):
    """Phase 2: vector inlet BC also extracted (max magnitude)."""
    result = parse_case(dlr_a_cavity, target_tier=3)
    bc = result["tier_3_metadata"].get("bc")
    # inletfuel is the fuel jet at U=(0 0 42.2)
    assert bc["U_inlet"] == 42.2
    assert bc["U_inlet_vector"] == [0.0, 0.0, 42.2]


def test_tier5_external_qoi_rules_yaml(dlr_a_cavity, tmp_path):
    """qoi_rules_path lets caller swap in an external YAML."""
    custom = tmp_path / "custom_rules.yaml"
    custom.write_text("""
qois:
  - name: only_mesh_cells
    description: "Only one rule for testing"
    unit: "1"
    type: pluck
    source: {tier: 3, path: "mesh_cells"}
""", encoding="utf-8")
    result = parse_case(dlr_a_cavity, target_tier=5, qoi_rules_path=custom)
    qoi = result["tier_5_qoi"]
    # External YAML had only one rule
    assert len(qoi) == 1
    assert qoi[0]["variable"] == "only_mesh_cells"
    assert qoi[0]["value"] == 3466


# ─── Tier 6: VTU export ───────────────────────────────────────────────────────


def test_tier6_export_via_parse_case(dlr_a_cavity):
    result = parse_case(dlr_a_cavity, target_tier=6)
    t6 = result.get("tier_6_full_data", {})
    assert t6.get("supported") is True
    assert t6["vtu_path"]
    from pathlib import Path
    assert Path(t6["vtu_path"]).is_file()
    assert t6["n_cells"] == 3466
    assert t6["n_points"] == 7109


def test_tier6_to_vtu_public_api(dlr_a_cavity):
    from sim_parse import to_vtu
    vtu = to_vtu(dlr_a_cavity, time=5000, fields=["T", "U"])
    assert vtu is not None
    assert vtu.is_file()
    assert vtu.stat().st_size > 1024  # at least 1 KB


# ─── Tier 7: Semantic ─────────────────────────────────────────────────────────


def test_tier7_physics_class(dlr_a_cavity):
    result = parse_case(dlr_a_cavity, target_tier=7)
    t7 = result["tier_7_semantic"]
    physics = t7["physics_class"]["value"]
    # Must include combustion since it's a reactingFoam case
    assert "combustion" in physics
    assert t7["physics_class"]["confidence"] in ("HIGH", "MED")


def test_tier7_time_semantics_lts(dlr_a_cavity):
    """DLR_A_cavity path contains 'LTS' → pseudo-time."""
    result = parse_case(dlr_a_cavity, target_tier=7)
    t7 = result["tier_7_semantic"]
    assert "LTS" in t7["time_semantics"]["value"] or \
           "pseudo" in t7["time_semantics"]["value"]


def test_tier7_run_health_healthy(dlr_a_cavity):
    result = parse_case(dlr_a_cavity, target_tier=7)
    t7 = result["tier_7_semantic"]
    assert t7["run_health"]["value"] == "healthy"


def test_tier7_nl_summary_present(dlr_a_cavity):
    result = parse_case(dlr_a_cavity, target_tier=7)
    t7 = result["tier_7_semantic"]
    nls = t7["nl_summary"]["value"]
    assert "reactingFoam" in nls
    assert "EDC" in nls
    assert "K" in nls  # temperature unit


def test_tier7_complexity_class(dlr_a_cavity):
    result = parse_case(dlr_a_cavity, target_tier=7)
    t7 = result["tier_7_semantic"]
    cc = t7["complexity_class"]["value"]
    # Small mesh (3466), complex physics (combustion + chemistry + turbulence)
    assert cc.startswith("small")
    assert "complex" in cc or "moderate" in cc


def test_phase1_acceptance_30_fields(dlr_a_cavity):
    """Phase 1 acceptance #1+2: ≥ 30 fields with correct values."""
    from sim_parse.core.tiers import count_present_fields
    result = parse_case(dlr_a_cavity, target_tier=4)

    total = (
        count_present_fields(result.get("tier_1_identify", {}))
        + count_present_fields(result.get("tier_2_inventory", {}))
        + count_present_fields(result.get("tier_3_metadata", {}))
        + count_present_fields(result.get("tier_4_field_stats", {}))
    )
    assert total >= 30, f"Expected ≥30 fields, got {total}"

    # Phase 1 acceptance #2: field values match manual analysis
    assert result["tier_1_identify"]["solver"] == "reactingFoam"
    assert result["tier_3_metadata"]["mesh_cells"] == 3466
    assert result["tier_3_metadata"]["turbulence"]["RASModel"] == "kEpsilon"
    assert result["tier_3_metadata"]["combustion"]["combustionModel"] == "EDC"
    assert result["tier_4_field_stats"]["variable_ranges"]["T"]["max"] > 1500


def test_parse_case_no_exception_on_missing_path(tmp_path):
    """Per never-raise contract."""
    result = parse_case(tmp_path / "nonexistent_path", target_tier=4)
    assert isinstance(result, dict)
    assert result["errors"]  # must populate errors
    # Must NOT raise


def test_parse_case_empty_dir_returns_none_format(empty_dir):
    result = parse_case(empty_dir, target_tier=1)
    assert result["tier_1_identify"]["format"] is None
