"""Fluent Tier 1 (Identify) tests using synthetic .cas files.

These tests don't need real Fluent data — we exercise the identification logic
(extension matching, magic-byte verification, gzip/HDF5 sub-format detection,
.dat pairing) using files we construct in tmp_path.

Tier 3+ tests would need real Fluent data and live in
tests/integration/test_fluent_real.py (skipped if no real case present).
"""
from __future__ import annotations

import gzip
import struct

import pytest

from sim_parse import parse_case
from sim_parse.solvers.fluent.tier1_identify import identify


# ─── Synthetic fixture builders ───────────────────────────────────────────────


def _make_legacy_cas(d, name="case.cas"):
    """Make a synthetic legacy Fluent .cas file (not parseable by VTK, but
    identifiable by extension + non-gzip / non-HDF5 magic)."""
    p = d / name
    p.write_bytes(b"(0 \"Synthetic Fluent test case\")\n(2 3)\n")
    return p


def _make_cff_cas(d, name="case.cas.h5"):
    """Make a synthetic CFF .cas.h5 (HDF5-magic-prefixed; not a real one)."""
    p = d / name
    p.write_bytes(b"\x89HDF\r\n\x1a\n" + b"\x00" * 100)
    return p


def _make_gzip_cas(d, name="case.cas.gz"):
    """Make a synthetic gzipped legacy .cas.gz."""
    p = d / name
    raw = b"(0 \"Synthetic Fluent test case\")\n(2 3)\n"
    p.write_bytes(gzip.compress(raw))
    return p


# ─── Tests: directory case_root, all 3 sub-formats ────────────────────────────


def test_identify_legacy_cas_in_dir(tmp_path):
    cas = _make_legacy_cas(tmp_path)
    result = identify(tmp_path)
    assert result is not None
    assert result["format"] == "fluent"
    assert result["sub_format"] == "legacy"
    assert result["compressed"] is False
    assert result["cas_path"] == str(cas)
    assert result["has_paired_results"] is False  # no .dat


def test_identify_cff_cas_in_dir(tmp_path):
    cas = _make_cff_cas(tmp_path)
    result = identify(tmp_path)
    assert result is not None
    assert result["format"] == "fluent"
    assert result["sub_format"] == "cff"
    assert result["compressed"] is False


def test_identify_gz_cas_in_dir(tmp_path):
    cas = _make_gzip_cas(tmp_path)
    result = identify(tmp_path)
    assert result is not None
    assert result["format"] == "fluent"
    assert result["sub_format"] == "legacy_gz"
    assert result["compressed"] is True


# ─── .dat pairing ─────────────────────────────────────────────────────────────


def test_identify_with_paired_dat(tmp_path):
    _make_legacy_cas(tmp_path)
    (tmp_path / "case.dat").write_bytes(b"synthetic dat")
    result = identify(tmp_path)
    assert result["has_paired_results"] is True
    assert result["dat_path"].endswith("case.dat")


def test_identify_cff_pair_dat_h5(tmp_path):
    _make_cff_cas(tmp_path)
    (tmp_path / "case.dat.h5").write_bytes(b"\x89HDF\r\n\x1a\n" + b"\x00" * 100)
    result = identify(tmp_path)
    assert result["has_paired_results"] is True
    assert result["dat_path"].endswith("case.dat.h5")


def test_identify_gz_pair_dat_gz(tmp_path):
    _make_gzip_cas(tmp_path)
    (tmp_path / "case.dat.gz").write_bytes(gzip.compress(b"data"))
    result = identify(tmp_path)
    assert result["has_paired_results"] is True
    assert result["dat_path"].endswith("case.dat.gz")


# ─── Single file as case_root ─────────────────────────────────────────────────


def test_identify_single_cas_file(tmp_path):
    cas = _make_legacy_cas(tmp_path)
    result = identify(cas)
    assert result is not None
    assert result["format"] == "fluent"
    assert result["case_root_kind"] == "file"


def test_identify_single_cff_file(tmp_path):
    cas = _make_cff_cas(tmp_path)
    result = identify(cas)
    assert result["sub_format"] == "cff"


# ─── Reject false positives ───────────────────────────────────────────────────


def test_reject_empty_dir(tmp_path):
    """Empty directory must return None."""
    assert identify(tmp_path) is None


def test_reject_unrelated_file(tmp_path):
    """A .txt file should not be identified as Fluent."""
    (tmp_path / "notes.txt").write_text("hello")
    assert identify(tmp_path) is None


def test_reject_cas_with_wrong_magic_for_cff(tmp_path):
    """A .cas.h5 file without HDF5 magic must be rejected."""
    p = tmp_path / "case.cas.h5"
    p.write_bytes(b"NOT HDF5 MAGIC" + b"\x00" * 100)
    assert identify(tmp_path) is None


def test_reject_gz_marked_cas_without_gzip_magic(tmp_path):
    """A .cas.gz that doesn't actually start with gzip magic — reject."""
    p = tmp_path / "case.cas.gz"
    p.write_bytes(b"NOT GZIPPED CONTENT" + b"\x00" * 100)
    assert identify(tmp_path) is None


def test_reject_garbage_path(tmp_path):
    """Truly non-existent path returns None gracefully."""
    assert identify(tmp_path / "does_not_exist") is None


# ─── Integration via parse_case dispatcher ────────────────────────────────────


def test_parse_case_routes_to_fluent_in_dir(tmp_path):
    """parse_case dispatcher should route a Fluent dir to fluent solver Path A."""
    _make_legacy_cas(tmp_path)
    result = parse_case(tmp_path, target_tier=1)
    t1 = result["tier_1_identify"]
    assert t1["format"] == "fluent"
    assert t1["sub_format"] == "legacy"
    assert t1["_path"] == "A"


def test_parse_case_routes_to_fluent_single_file(tmp_path):
    """Single .cas file as case_root should also route correctly."""
    cas = _make_legacy_cas(tmp_path)
    result = parse_case(cas, target_tier=2)
    t1 = result["tier_1_identify"]
    assert t1["format"] == "fluent"
    # Tier 2 inventory should also work
    t2 = result["tier_2_inventory"]
    assert t2["sub_format"] == "legacy"


def test_parse_case_fluent_inventory_lists_companion_files(tmp_path):
    """Tier 2 should pick up .trn / report-*.out / .jou alongside the .cas."""
    _make_legacy_cas(tmp_path)
    (tmp_path / "case.dat").write_bytes(b"data")
    (tmp_path / "report-mass-flow.out").write_text("iter mass-flow\n1 0.5\n")
    (tmp_path / "transcript.trn").write_text("transcript content")
    (tmp_path / "session.jou").write_text("/file/read-case ...")
    result = parse_case(tmp_path, target_tier=2)
    t2 = result["tier_2_inventory"]
    assert "report-mass-flow.out" in t2["report_files"]
    assert "transcript.trn" in t2["transcript_files"]
    assert "session.jou" in t2["journal_files"]


# ─── OpenFOAM and Fluent in same dir — priority resolution ────────────────────


def test_openfoam_priority_over_fluent_when_both_present(tmp_path):
    """If a directory has BOTH OpenFOAM markers AND a .cas file, OpenFOAM wins
    because its priority is higher (80 vs 70)."""
    # Make it look like OpenFOAM
    (tmp_path / "system").mkdir()
    (tmp_path / "system" / "controlDict").write_text("""
FoamFile { version 2.0; }
application     simpleFoam;
""")
    (tmp_path / "constant").mkdir()
    # AND drop a .cas file
    (tmp_path / "case.cas").write_bytes(b"(0 fake)")

    result = parse_case(tmp_path, target_tier=1)
    assert result["tier_1_identify"]["format"] == "openfoam"
