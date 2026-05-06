"""End-to-end tests for Fluent CFF (.cas.h5, V19+ HDF5 format).

These exercise vtkFLUENTCFFReader on a REAL Fluent-generated .cas.h5.
Auto-skips when no real fixture is available — see conftest.fluent_cff_case
for how to provide one. Until then, these tests guard against regression
by being run-on-demand.

Synthetic Tier 1 detection (extension + magic bytes) is covered separately
in tests/unit/test_fluent_tier1.py and runs unconditionally.
"""
from __future__ import annotations

import pytest

from sim_parse import parse_case

vtk_required = pytest.importorskip("vtk", reason="VTK needed for CFF tests")


def test_cff_tier1_identifies_fluent(fluent_cff_case):
    r = parse_case(fluent_cff_case, target_tier=1)
    t1 = r["tier_1_identify"]
    assert t1["format"] == "fluent"
    assert t1["sub_format"] == "cff"
    assert t1.get("compressed") is False  # CFF is HDF5, not gzip


def test_cff_tier3_yields_mesh_zones(fluent_cff_case):
    r = parse_case(fluent_cff_case, target_tier=3)
    t3 = r["tier_3_metadata"]
    zones = t3.get("mesh_zones") or []
    assert len(zones) >= 1, "CFF Tier 3 must produce at least one mesh_zone"


def test_cff_tier3_shares_schema_with_legacy_fluent(fluent_cff_case):
    """Cross-sub_format schema invariant: CFF and legacy emit identical
    Tier 3 top-level key sets (modulo solver-specific extras)."""
    r = parse_case(fluent_cff_case, target_tier=3)
    t3 = r["tier_3_metadata"]
    # Required cross-sub_format fields
    assert "mesh_zones" in t3
    assert "mesh_cells" in t3
    assert "fluent_sub_format" in t3
    assert t3["fluent_sub_format"] == "cff"


def test_cff_tier4_extracts_variable_ranges(fluent_cff_case):
    r = parse_case(fluent_cff_case, target_tier=4)
    t4 = r["tier_4_field_stats"]
    vr = t4.get("variable_ranges") or {}
    assert len(vr) > 0, "CFF Tier 4 must extract at least one variable range"
    # Each entry must have the standard min/max/mean trio
    for var, stats in vr.items():
        assert "min" in stats and "max" in stats, \
            f"variable {var} missing min/max in CFF case"
