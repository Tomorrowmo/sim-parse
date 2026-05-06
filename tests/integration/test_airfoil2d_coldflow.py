"""Regression tests against the airFoil2D cold-flow case.

Locks in the Tier 3 extraction fixes for two distinct OpenFOAM gotchas the
combustion DLR_A_cavity case did NOT exercise:

  1. polyMesh files are gzipped (owner.gz / boundary.gz / points.gz / ...).
     Until we added .gz fallback to read_boundary_patches, this case had
     `boundaries=None` → mesh_zones empty → the cross-solver "mesh_zones is
     always present" invariant silently broken.

  2. polyMesh/owner header has NO `note "..."` field with mesh counts.
     Until we added _parse_array_length_fallback, mesh_cells / mesh_points
     / mesh_faces were all None.

  3. fvSchemes ddtSchemes.default = steadyState → is_transient should be
     False. (DLR_A is reactingFoam = transient; this is the steady twin.)

Skipped automatically if the case data is missing.
"""
from __future__ import annotations

import pytest

from sim_parse import parse_case


# ─── Tier 1 ───────────────────────────────────────────────────────────────────


def test_tier1_identifies_openfoam(airfoil2d_coldflow):
    r = parse_case(airfoil2d_coldflow, target_tier=1)
    t1 = r["tier_1_identify"]
    assert t1["format"] == "openfoam"
    assert t1["solver"] == "simpleFoam"
    assert t1["layout"] == "single-region"


# ─── Tier 3: the regression-critical assertions ───────────────────────────────


def test_tier3_extracts_nfaces_from_note_less_owner(airfoil2d_coldflow):
    """owner.gz has no `note "..."` field — but the array-length integer
    just after FoamFile{} IS nFaces. Verify we recovered it."""
    r = parse_case(airfoil2d_coldflow, target_tier=3)
    t3 = r["tier_3_metadata"]
    assert t3.get("mesh_faces") is not None, \
        "mesh_faces should be populated from owner array length even " \
        "when the FoamFile note is absent"
    assert t3["mesh_faces"] > 0


def test_tier3_extracts_boundaries_from_compressed_polymesh(airfoil2d_coldflow):
    """boundary.gz must be read transparently (mirror of owner.gz handling)."""
    r = parse_case(airfoil2d_coldflow, target_tier=3)
    t3 = r["tier_3_metadata"]
    boundaries = t3.get("boundaries") or []
    assert len(boundaries) >= 4, \
        f"airFoil2D has 4 patches (inlet/outlet/walls/frontAndBack); " \
        f"got {len(boundaries)}"
    names = {b["name"] for b in boundaries}
    assert {"inlet", "outlet", "walls", "frontAndBack"} <= names


def test_tier3_mesh_zones_always_present(airfoil2d_coldflow):
    """Cross-solver invariant: mesh_zones must be present even when
    Tier 3 couldn't pull mesh_cells from the owner header."""
    r = parse_case(airfoil2d_coldflow, target_tier=3)
    t3 = r["tier_3_metadata"]
    zones = t3.get("mesh_zones") or []
    assert len(zones) >= 1, "mesh_zones must contain at least the volume zone"
    volumes = [z for z in zones if z["role"] == "volume"]
    assert len(volumes) == 1
    assert volumes[0]["name"] == "internalMesh"
    # Every patch should also be present as a boundary zone.
    boundary_names = {z["name"] for z in zones if z["role"] == "boundary"}
    assert {"inlet", "outlet", "walls", "frontAndBack"} <= boundary_names


def test_tier3_detects_steady_state(airfoil2d_coldflow):
    """fvSchemes::ddtSchemes.default == steadyState → is_transient=False."""
    r = parse_case(airfoil2d_coldflow, target_tier=3)
    t3 = r["tier_3_metadata"]
    assert t3.get("is_transient") is False, \
        "simpleFoam case with steadyState ddt scheme should be is_transient=False"
