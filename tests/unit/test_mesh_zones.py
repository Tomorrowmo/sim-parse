"""Unit tests for #3: cross-solver mesh_zones schema.

Both OpenFOAM and Fluent emit a `mesh_zones` array with the same schema.
The OpenFOAM Tier 3 version is text-only (skeleton without geometry); the
Fluent Tier 3 version uses VTK with role inference based on Fluent's
zone-name suffix convention.
"""
from __future__ import annotations

from sim_parse.solvers.fluent.tier3_metadata import (
    _classify_fluent_zone_by_name,
)
from sim_parse.solvers.openfoam.tier3_metadata import (
    _build_mesh_zones_skeleton,
)


# ─── OpenFOAM skeleton ────────────────────────────────────────────────────────


def test_openfoam_skeleton_emits_volume_zone_when_owner_data_present():
    tier3 = {"mesh_cells": 3466, "mesh_points": 7109}
    out = _build_mesh_zones_skeleton(tier3, patches=[])
    volumes = [z for z in out if z["role"] == "volume"]
    assert len(volumes) == 1
    assert volumes[0]["name"] == "internalMesh"
    assert volumes[0]["n_cells"] == 3466
    assert volumes[0]["n_faces"] is None
    # Geometry fields must be None — Tier 3 has no VTK access
    assert volumes[0]["bounding_box"] is None
    assert volumes[0]["element_types"] is None
    assert volumes[0]["n_points"] is None


def test_openfoam_skeleton_emits_boundary_zones_from_patches():
    patches = [
        {"name": "inletfuel", "type": "patch", "nFaces": 4, "startFace": 0},
        {"name": "wall_a",    "type": "wall",  "nFaces": 8, "startFace": 4},
        {"name": "axis",      "type": "empty", "nFaces": 0, "startFace": 12},
    ]
    out = _build_mesh_zones_skeleton({"mesh_cells": 100}, patches)
    bnd = [z for z in out if z["role"] == "boundary"]
    assert len(bnd) == 3
    # Every boundary zone has n_faces from the patch but no n_cells
    for z in bnd:
        assert z["n_cells"] is None
        assert z["n_faces"] is not None
    by_name = {z["name"]: z for z in bnd}
    assert by_name["wall_a"]["patch_type"] == "wall"
    assert by_name["axis"]["patch_type"] == "empty"
    assert by_name["axis"]["n_faces"] == 0


def test_openfoam_skeleton_handles_no_patches():
    """Cases without polyMesh/boundary still emit the volume zone alone."""
    out = _build_mesh_zones_skeleton({"mesh_cells": 1000}, patches=None)
    assert len(out) == 1
    assert out[0]["role"] == "volume"


def test_openfoam_skeleton_handles_no_owner_data():
    """If Tier 3 couldn't read polyMesh/owner, no volume zone is fabricated."""
    out = _build_mesh_zones_skeleton({}, patches=None)
    assert out == []


# ─── Fluent role inference by name suffix ─────────────────────────────────────


def test_fluent_classify_volume_zones():
    assert _classify_fluent_zone_by_name("tets:fluid") == ("volume", "fluid")
    assert _classify_fluent_zone_by_name("solid_block:solid") == ("volume", "solid")


def test_fluent_classify_boundary_zones():
    role, t = _classify_fluent_zone_by_name("tria-3-wall:wall")
    assert role == "boundary"
    assert t == "wall"

    for ztype in ["pressure-far-field", "pressure-inlet", "pressure-outlet",
                  "mass-flow-inlet", "velocity-inlet", "axis", "symmetry"]:
        role, t = _classify_fluent_zone_by_name(f"some-zone:{ztype}")
        assert role == "boundary", f"{ztype} should be boundary, got {role}"
        assert t == ztype


def test_fluent_classify_interior_zone():
    """Interior face zones are interfaces, not boundaries — distinct role."""
    role, t = _classify_fluent_zone_by_name("default_interior-6:interior")
    assert role == "interface"
    assert t == "interior"


def test_fluent_classify_unknown_when_no_suffix():
    """A name without a colon doesn't have a Fluent-style zone type tag —
    must NOT be silently classified as anything."""
    assert _classify_fluent_zone_by_name("just_a_name") == ("unknown", "")
    assert _classify_fluent_zone_by_name("") == ("unknown", "")


def test_fluent_classify_unknown_zone_type():
    """An unrecognized suffix → unknown role with the raw type preserved."""
    role, t = _classify_fluent_zone_by_name("foo:something-weird-fluent-added")
    assert role == "unknown"
    assert t == "something-weird-fluent-added"


# ─── Cross-solver schema invariants ───────────────────────────────────────────


def test_openfoam_skeleton_schema_matches_fluent_keys():
    """Both solvers emit the same key set; consumers can iterate uniformly."""
    of_zone = _build_mesh_zones_skeleton(
        {"mesh_cells": 100},
        patches=[{"name": "wall_a", "type": "wall", "nFaces": 8, "startFace": 0}],
    )[1]  # boundary zone
    expected_keys = {
        "name", "role", "n_cells", "n_faces", "n_points",
        "bounding_box", "element_types", "patch_type",
    }
    # OpenFOAM skeleton has all of these (plus _source for traceability)
    assert expected_keys <= set(of_zone.keys())
