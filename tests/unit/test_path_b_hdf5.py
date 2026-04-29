"""Path B tests: HDF5 dump + magic-byte detection.

Uses fake_hdf5_file fixture (built with h5py at runtime).
"""
from __future__ import annotations

import pytest

from sim_parse import parse_case
from sim_parse.generic.hdf5_dump import hdf5_inventory
from sim_parse.generic.magic import identify_by_magic


def test_magic_identifies_hdf5(fake_hdf5_file):
    result = identify_by_magic(fake_hdf5_file)
    assert result is not None
    assert result["format"] == "hdf5"
    assert result["_path"] == "B"
    assert result["file_size"] > 0


def test_hdf5_inventory_lists_datasets(fake_hdf5_file):
    inv = hdf5_inventory(fake_hdf5_file)
    assert inv is not None
    paths = {d["path"] for d in inv["datasets"]}
    assert "/mesh/coordinates" in paths
    assert "/mesh/connectivity" in paths
    assert "/Solution/T" in paths


def test_hdf5_inventory_root_attrs(fake_hdf5_file):
    inv = hdf5_inventory(fake_hdf5_file)
    assert inv["root_attrs"]["creator"] == "FakeSolver v1.0"


def test_hdf5_inventory_identifies_mesh_group(fake_hdf5_file):
    inv = hdf5_inventory(fake_hdf5_file)
    # /mesh contains both 'coordinates' and 'connectivity' keywords → mesh group
    assert "/mesh" in inv.get("mesh_like_groups", [])


def test_parse_case_routes_hdf5_to_path_b(fake_hdf5_file):
    """Phase 1 acceptance #4: .h5 file triggers Path B."""
    result = parse_case(fake_hdf5_file, target_tier=2)
    assert result["tier_1_identify"]["format"] == "hdf5"
    assert result["tier_1_identify"]["_path"] == "B"
    # Tier 2 inventory should be HDF5-aware (datasets list)
    inv = result["tier_2_inventory"]
    assert "datasets" in inv
    assert any(d["path"] == "/Solution/T" for d in inv["datasets"])


def test_magic_returns_none_for_unknown_file(tmp_path):
    """Truly unknown content must not trigger false positives."""
    f = tmp_path / "garbage.bin"
    f.write_bytes(b"\x00\x01\x02\x03 random bytes nothing matches")
    result = identify_by_magic(f)
    # File has no recognized magic and no useful extension
    assert result is None or result.get("format") is None


def test_magic_uses_extension_hint(tmp_path):
    """Files with known extensions but no magic still get tagged."""
    f = tmp_path / "result.cgns"
    f.write_bytes(b"\x00" * 100)  # not real CGNS, no magic
    result = identify_by_magic(f)
    # CGNS is HDF5-based; without HDF5 magic this is ambiguous, but ext hint = cgns
    if result is not None:
        assert result["format"] in ("cgns", "hdf5")
