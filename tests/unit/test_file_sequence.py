"""Tests for the cross-solver file-sequence detection helper."""
from __future__ import annotations

from pathlib import Path

import pytest

from sim_parse.core.sequence import detect_file_sequence, collect_sequence_files
from sim_parse.solvers.cgns.tier1_identify import identify as cgns_identify
from sim_parse.solvers.tecplot.tier1_identify import identify as tecplot_identify


# ─── detect_file_sequence ─────────────────────────────────────────────────────


def test_returns_none_for_empty_list():
    assert detect_file_sequence([]) is None


def test_returns_none_for_single_file(tmp_path):
    f = tmp_path / "case.cgns"
    f.touch()
    assert detect_file_sequence([f]) is None


def test_picks_last_in_decimal_sorted_order(tmp_path):
    """The user's exact case: 0.003 / 0.004 / 0.005 → pick 0.005."""
    files = []
    for v in ("0.003", "0.004", "0.005"):
        p = tmp_path / f"{v}.cgns"
        p.touch()
        files.append(p)
    seq = detect_file_sequence(files)
    assert seq is not None
    assert seq["representative"].name == "0.005.cgns"
    assert seq["n_files"] == 3
    assert seq["time_values"] == [0.003, 0.004, 0.005]


def test_picks_last_with_zero_padded_suffix(tmp_path):
    """flow_0001.cgns / flow_0050.cgns / ... — works with lex sort and numeric sort alike."""
    files = []
    for n in (1, 5, 50, 100):
        p = tmp_path / f"flow_{n:04d}.cgns"
        p.touch()
        files.append(p)
    seq = detect_file_sequence(files)
    assert seq["representative"].name == "flow_0100.cgns"
    assert seq["time_values"] == [1.0, 5.0, 50.0, 100.0]


def test_handles_unpadded_numeric_via_value_sort(tmp_path):
    """case_2.cgns and case_10.cgns — numeric sort puts 10 last,
    lex sort would put '2' last. We must use numeric."""
    files = []
    for n in (2, 10, 100):
        p = tmp_path / f"case_{n}.cgns"
        p.touch()
        files.append(p)
    seq = detect_file_sequence(files)
    assert seq["representative"].name == "case_100.cgns"
    assert seq["time_values"] == [2.0, 10.0, 100.0]


def test_falls_back_to_lex_when_no_numeric(tmp_path):
    files = [tmp_path / "alpha.cgns", tmp_path / "bravo.cgns", tmp_path / "charlie.cgns"]
    for f in files:
        f.touch()
    seq = detect_file_sequence(files)
    assert seq["representative"].name == "charlie.cgns"
    assert seq["time_values"] is None
    assert seq["pattern_description"] == "lexicographic_fallback"


def test_handles_scientific_notation(tmp_path):
    """Time stamps like 1.5e-3 should parse correctly."""
    files = []
    for v in ("1.0e-3", "5.0e-3", "1.0e-2"):
        p = tmp_path / f"snap_{v}.cgns"
        p.touch()
        files.append(p)
    seq = detect_file_sequence(files)
    # 1e-2 = 0.01 > 5e-3 = 0.005 > 1e-3 = 0.001
    assert seq["representative"].name == "snap_1.0e-2.cgns"
    assert seq["time_values"] == [0.001, 0.005, 0.01]


# ─── collect_sequence_files ───────────────────────────────────────────────────


def test_collect_dedupes_across_overlapping_globs(tmp_path):
    """If two globs match the same file, it should appear only once."""
    (tmp_path / "data.cgns").touch()
    (tmp_path / "data.h5").touch()
    files = collect_sequence_files(tmp_path, ["*.cgns", "*.h5", "*"])
    # data.cgns + data.h5 = 2 unique files
    paths = sorted(f.name for f in files)
    assert paths == ["data.cgns", "data.h5"]


# ─── End-to-end: CGNS multi-file sequence identification ──────────────────────


def _make_minimal_hdf5_cgns(path: Path):
    """Real HDF5 file with a CGNSBase_t group. Requires h5py."""
    h5py = pytest.importorskip("h5py")
    with h5py.File(path, "w") as f:
        f.create_group("CGNSBase_t")
    return path


def test_cgns_identify_handles_multi_file_directory(tmp_path):
    """The original bug: 3 .cgns files in one dir → identify returns sequence."""
    for v in ("0.003", "0.004", "0.005"):
        _make_minimal_hdf5_cgns(tmp_path / f"{v}.cgns")
    result = cgns_identify(tmp_path)
    assert result is not None
    assert result["format"] == "cgns"
    assert result["case_root_kind"] == "directory_sequence"
    assert Path(result["file_path"]).name == "0.005.cgns"  # representative
    assert result["n_sequence_files"] == 3
    assert result["sequence_time_values"] == [0.003, 0.004, 0.005]
    # Warning explains the choice
    assert any("3 .cgns files" in w for w in result.get("_warnings", []))


def test_cgns_single_file_dir_still_uses_simple_path(tmp_path):
    """Don't break the existing single-file behavior."""
    _make_minimal_hdf5_cgns(tmp_path / "lonely.cgns")
    result = cgns_identify(tmp_path)
    assert result is not None
    assert result["format"] == "cgns"
    assert result["case_root_kind"] == "directory"   # NOT "directory_sequence"
    assert "n_sequence_files" not in result


# ─── End-to-end: Tecplot multi-file sequence ──────────────────────────────────


_TDV_HEADER = b"#!TDV112\x01\x00\x00\x00"


def test_tecplot_identify_handles_multi_file_directory(tmp_path):
    for v in (1, 2, 3):
        p = tmp_path / f"flow_{v:04d}.plt"
        p.write_bytes(_TDV_HEADER + b"\x00" * 32)
    result = tecplot_identify(tmp_path)
    assert result is not None
    assert result["format"] == "tecplot"
    assert result["case_root_kind"] == "directory_sequence"
    assert Path(result["file_path"]).name == "flow_0003.plt"
    assert result["n_sequence_files"] == 3
    assert result["sequence_time_values"] == [1.0, 2.0, 3.0]
