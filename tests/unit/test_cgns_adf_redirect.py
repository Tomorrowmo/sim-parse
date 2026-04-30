"""Tests for CGNS ADF→HDF5 sibling auto-redirect logic."""
from __future__ import annotations

from pathlib import Path

import pytest

from sim_parse.solvers.cgns.tier1_identify import (
    _find_hdf5_sibling,
    _hdf5_sibling_patterns,
    identify,
)


# Minimal HDF5 file content — just the magic header + a CGNSBase group is
# enough to satisfy our `_hdf5_smells_like_cgns` check.
_HDF5_MAGIC = b"\x89HDF\r\n\x1a\n"
_ADF_HEADER = b"\xc0\xa8\xa3\xa9ADF Database Version B02012>AdF0Mon Jan  1 00:00:00 2025"


def _write_adf_cgns(path: Path) -> Path:
    path.write_bytes(_ADF_HEADER + b"\x00" * 64)
    return path


def _write_minimal_hdf5_cgns(path: Path) -> Path:
    """Real HDF5 file with a CGNSBase_t group. Requires h5py."""
    h5py = pytest.importorskip("h5py")
    with h5py.File(path, "w") as f:
        # Add a fake CGNSBase_NAME group so _hdf5_smells_like_cgns matches
        f.create_group("CGNSBase_t")
    return path


# ─── _hdf5_sibling_patterns ───────────────────────────────────────────────────


def test_sibling_patterns_strip_cgns_and_re_extension(tmp_path):
    adf = tmp_path / "case.cgns"
    cands = _hdf5_sibling_patterns(adf)
    cand_names = sorted(c.name for c in cands)
    assert "case.hdf5.cgns" in cand_names
    assert "case.h5.cgns" in cand_names
    assert "case_hdf5.cgns" in cand_names
    assert "case-hdf5.cgns" in cand_names
    assert "case.cgns.h5" in cand_names


def test_sibling_patterns_preserve_directory(tmp_path):
    adf = tmp_path / "subdir" / "data.cgns"
    cands = _hdf5_sibling_patterns(adf)
    for c in cands:
        assert c.parent == adf.parent


def test_sibling_patterns_skip_self_when_input_already_matches_pattern(tmp_path):
    """A file already named foo.hdf5.cgns shouldn't pattern-match to itself."""
    adf = tmp_path / "foo.hdf5.cgns"
    cands = _hdf5_sibling_patterns(adf)
    assert adf not in cands


# ─── _find_hdf5_sibling ───────────────────────────────────────────────────────


def test_find_returns_none_when_no_sibling_exists(tmp_path):
    adf = _write_adf_cgns(tmp_path / "case.cgns")
    assert _find_hdf5_sibling(adf) is None


def test_find_returns_sibling_when_hdf5_pattern_present(tmp_path):
    adf = _write_adf_cgns(tmp_path / "case.cgns")
    hdf5_sibling = _write_minimal_hdf5_cgns(tmp_path / "case.hdf5.cgns")
    found = _find_hdf5_sibling(adf)
    assert found == hdf5_sibling


def test_find_rejects_pattern_match_that_is_not_hdf5(tmp_path):
    """A file named case.hdf5.cgns but containing ADF bytes must NOT be
    accepted — content has to confirm the magic."""
    adf = _write_adf_cgns(tmp_path / "case.cgns")
    fake_hdf5 = _write_adf_cgns(tmp_path / "case.hdf5.cgns")  # ADF content
    found = _find_hdf5_sibling(adf)
    assert found is None


# ─── End-to-end identify() redirect behavior ──────────────────────────────────


def test_identify_redirects_adf_to_hdf5_sibling(tmp_path):
    adf = _write_adf_cgns(tmp_path / "case.cgns")
    hdf5_sibling = _write_minimal_hdf5_cgns(tmp_path / "case.hdf5.cgns")

    result = identify(adf)
    assert result is not None
    assert result["format"] == "cgns"
    assert result["sub_format"] == "hdf5"   # not 'adf' — redirected
    assert Path(result["file_path"]) == hdf5_sibling
    assert result.get("redirected_from") == str(adf)
    # Warning explains the redirect
    assert any("redirected" in w.lower() for w in result.get("_warnings", []))


def test_identify_returns_adf_when_no_sibling(tmp_path):
    adf = _write_adf_cgns(tmp_path / "case.cgns")
    result = identify(adf)
    assert result is not None
    assert result["format"] == "cgns"
    assert result["sub_format"] == "adf"
    assert "redirected_from" not in result
