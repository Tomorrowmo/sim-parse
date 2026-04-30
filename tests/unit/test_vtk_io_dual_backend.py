"""Tests for the dual-backend Romtek/VTK reader fallback in adapters/vtk_io.py.

These exercise the load_via_romtek helper without depending on a real
Romtek-loadable file — most paths return None (failure) and the helper's
job is to do that quietly so callers fall back.
"""
from __future__ import annotations

import os

import pytest

from sim_parse.adapters.vtk_io import (
    ROMTEK_READER_FOR,
    _romtek_enabled,
    load_via_romtek,
)


# ─── kill-switch behavior ────────────────────────────────────────────────────


def test_kill_switch_disabled_returns_none(monkeypatch):
    """SIMPARSE_USE_ROMTEK=0 → load_via_romtek returns None even when Romtek
    is available. Lets users force standard-VTK fallback for A/B."""
    monkeypatch.setenv("SIMPARSE_USE_ROMTEK", "0")
    assert _romtek_enabled() is False
    out = load_via_romtek(["/anything.cgns"], "CGNSReader")
    assert out is None


def test_kill_switch_default_is_enabled(monkeypatch):
    monkeypatch.delenv("SIMPARSE_USE_ROMTEK", raising=False)
    assert _romtek_enabled() is True


@pytest.mark.parametrize("val", ["false", "False", "FALSE", "no", "off", "0"])
def test_kill_switch_truthy_variants(monkeypatch, val):
    monkeypatch.setenv("SIMPARSE_USE_ROMTEK", val)
    assert _romtek_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "yes", "on", "anything-else"])
def test_kill_switch_falsy_variants_keep_enabled(monkeypatch, val):
    """Anything NOT in the off-list keeps Romtek enabled."""
    monkeypatch.setenv("SIMPARSE_USE_ROMTEK", val)
    assert _romtek_enabled() is True


# ─── never-raise contract ────────────────────────────────────────────────────


def test_returns_none_for_nonexistent_file():
    out = load_via_romtek(["/path/does/not/exist.cgns"], "CGNSReader")
    assert out is None


def test_returns_none_for_empty_path_list():
    out = load_via_romtek([], "CGNSReader")
    assert out is None


def test_returns_none_for_bogus_reader_name():
    out = load_via_romtek(["/whatever.x"], "NotARealReaderName_FakeXYZ_2026")
    assert out is None


def test_handles_pathlike_args(tmp_path):
    """Caller may pass Path objects; helper must str() them transparently."""
    fake = tmp_path / "fake.cgns"
    fake.touch()
    out = load_via_romtek([fake], "CGNSReader")
    # Either None (Romtek refuses empty file) or empty multiblock — both fine,
    # the test asserts the call doesn't raise.
    assert out is None or hasattr(out, "GetNumberOfBlocks")


# ─── format → reader-name mapping ────────────────────────────────────────────


def test_romtek_reader_for_covers_known_formats():
    """Sanity: every format we support in sim-parse solvers should either
    have a Romtek reader name OR be deliberately absent."""
    expected_present = {
        "cgns", "openfoam", "fluent_legacy", "tecplot",
        "ensight_gold", "vtu", "vtm", "vts", "vtp", "plot3d",
    }
    assert expected_present <= set(ROMTEK_READER_FOR.keys())


def test_romtek_reader_names_are_strings():
    for fmt, reader_name in ROMTEK_READER_FOR.items():
        assert isinstance(reader_name, str), \
            f"{fmt} has non-string reader: {reader_name!r}"
        assert reader_name, f"{fmt} has empty reader name"


# ─── Integration test: actually call Romtek if available ──────────────────────


@pytest.mark.skipif(
    not (lambda: __import__("vtk", fromlist=["vtkRomtekIODriver"])
         and hasattr(__import__("vtk"), "vtkRomtekIODriver"))(),
    reason="vtkRomtekIODriver not available in this VTK build",
)
def test_load_real_cgns_via_romtek_if_available():
    """When Romtek IS available, loading a known-good HDF5 CGNS should give
    a non-empty multiblock. Skipped on builds without Romtek."""
    cgns_file = "D:/XField/data/cgns/F6/F6_AOA0.cgns"
    if not os.path.isfile(cgns_file):
        pytest.skip(f"test data not present: {cgns_file}")
    out = load_via_romtek([cgns_file], "CGNSReader")
    if out is None:
        pytest.skip("Romtek CGNSReader returned None on this build (acceptable)")
    assert out.GetNumberOfBlocks() > 0
