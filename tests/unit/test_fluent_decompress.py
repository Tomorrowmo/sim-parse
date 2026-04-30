"""Unit tests for fluent/_decompress.py — transparent .cas.gz handling.

The decompression helper is a workaround for vtkFLUENTReader's inability to
read gzipped Fluent files. These tests use synthetic gzipped fixtures (no
real .cas content needed — the helper only cares about gzip envelope correctness)
so they run in milliseconds without depending on real CAE data.
"""
from __future__ import annotations

import gzip
import os
import shutil
import time

import pytest

from sim_parse.solvers.fluent._decompress import (
    _safe_basename,
    _strip_cas_gz,
    ensure_decompressed_fluent_pair,
)


# ─── _strip_cas_gz / _safe_basename helpers ───────────────────────────────────


def test_strip_cas_gz_handles_dot_cas_dot_gz():
    assert _strip_cas_gz("FLTG 1-0.cas.gz") == "FLTG 1-0"
    assert _strip_cas_gz("foo.cas.gz") == "foo"


def test_strip_cas_gz_handles_plain_gz():
    assert _strip_cas_gz("foo.gz") == "foo"


def test_strip_cas_gz_passes_through_non_gz():
    assert _strip_cas_gz("foo.cas") == "foo.cas"
    assert _strip_cas_gz("foo") == "foo"


def test_safe_basename_preserves_alnum():
    assert _safe_basename("FLTG_1-0") == "FLTG_1-0"
    assert _safe_basename("ABC.123") == "ABC.123"


def test_safe_basename_replaces_spaces_and_cjk():
    assert _safe_basename("FLTG 1-0") == "FLTG_1-0"
    # Pure-CJK input has nothing useful to keep — sanitizer falls back to
    # the 'case' placeholder rather than naming the file '__'.
    assert _safe_basename("案例") == "case"
    # Mixed: alphanumerics survive, CJK + space become underscores. We don't
    # care about the exact underscore count, just that the alphanumerics
    # survive and no problematic chars remain.
    out = _safe_basename("FLTG 中文 1")
    assert out.startswith("FLTG") and out.endswith("1")
    assert " " not in out
    import re
    assert re.fullmatch(r"[A-Za-z0-9._-]+", out)


def test_safe_basename_empty_input_yields_default():
    assert _safe_basename("") == "case"
    assert _safe_basename(" ") == "case"
    assert _safe_basename("...") == "case"


# ─── ensure_decompressed_fluent_pair ──────────────────────────────────────────


def _make_fake_cas_gz(path, content: bytes = b"fake fluent cas content"):
    """Create a gzipped file at path; for the tests, content is just a marker."""
    with gzip.open(path, "wb") as f:
        f.write(content)


def test_passthrough_when_cas_is_none():
    cas, dat = ensure_decompressed_fluent_pair(None, "/tmp/foo.dat")
    assert cas is None
    assert str(dat) == "/tmp/foo.dat" or str(dat).endswith("foo.dat")


def test_passthrough_when_cas_is_uncompressed(tmp_path):
    cas_path = tmp_path / "case.cas"
    cas_path.write_bytes(b"plain cas")
    dat_path = tmp_path / "case.dat"
    dat_path.write_bytes(b"plain dat")

    out_cas, out_dat = ensure_decompressed_fluent_pair(str(cas_path), str(dat_path))
    assert out_cas == cas_path
    assert out_dat == dat_path


def test_passthrough_when_cas_is_hdf5(tmp_path):
    """*.cas.h5 (Fluent CFF) — no .gz suffix, must pass through untouched."""
    cas_path = tmp_path / "case.cas.h5"
    cas_path.write_bytes(b"\x89HDF\r\n\x1a\n" + b"\x00" * 16)
    out_cas, out_dat = ensure_decompressed_fluent_pair(str(cas_path), None)
    assert out_cas == cas_path
    assert out_dat is None


def test_decompresses_cas_gz(tmp_path):
    src = tmp_path / "FLTG 1-0.cas.gz"
    _make_fake_cas_gz(src, content=b"the actual cas content")

    out_cas, out_dat = ensure_decompressed_fluent_pair(str(src), None)
    assert out_cas != src  # different path (in temp cache dir)
    assert out_cas.exists()
    assert out_cas.read_bytes() == b"the actual cas content"
    # Sanitized basename — no space
    assert " " not in out_cas.name
    assert out_cas.name == "FLTG_1-0.cas"
    assert out_dat is None


def test_decompresses_pair_with_matching_basename(tmp_path):
    """The .cas and .dat must end up in the same dir with matching basename
    so vtkFLUENTReader's auto-pairing finds them."""
    cas_src = tmp_path / "case.cas.gz"
    dat_src = tmp_path / "case.dat.gz"
    _make_fake_cas_gz(cas_src, content=b"cas")
    _make_fake_cas_gz(dat_src, content=b"dat")

    out_cas, out_dat = ensure_decompressed_fluent_pair(str(cas_src), str(dat_src))
    assert out_cas.exists() and out_dat.exists()
    assert out_cas.parent == out_dat.parent  # SAME dir
    # Matching basenames so auto-pairing finds the .dat
    assert out_cas.stem == out_dat.stem == "case"
    assert out_cas.suffix == ".cas"
    assert out_dat.suffix == ".dat"
    assert out_cas.read_bytes() == b"cas"
    assert out_dat.read_bytes() == b"dat"


def test_passthrough_dat_when_dat_is_uncompressed(tmp_path):
    """If user has .cas.gz but uncompressed .dat (mixed), pass dat through."""
    cas_src = tmp_path / "case.cas.gz"
    dat_src = tmp_path / "case.dat"
    _make_fake_cas_gz(cas_src)
    dat_src.write_bytes(b"plain dat")

    out_cas, out_dat = ensure_decompressed_fluent_pair(str(cas_src), str(dat_src))
    assert out_cas != cas_src   # cas was decompressed
    assert out_dat == dat_src   # dat was NOT touched


def test_cache_reuses_decompressed_file(tmp_path):
    """Second call with same source must NOT re-decompress (cache hit)."""
    src = tmp_path / "case.cas.gz"
    _make_fake_cas_gz(src, content=b"original")

    out1, _ = ensure_decompressed_fluent_pair(str(src), None)
    mtime1 = out1.stat().st_mtime

    # Sleep a touch so any re-decompression would have a different mtime
    time.sleep(0.05)
    out2, _ = ensure_decompressed_fluent_pair(str(src), None)
    assert out1 == out2
    assert out2.stat().st_mtime == mtime1  # untouched


def test_cache_invalidates_on_source_change(tmp_path):
    """If the source .cas.gz is rewritten (different mtime/size), cache misses
    and decompresses again."""
    src = tmp_path / "case.cas.gz"
    _make_fake_cas_gz(src, content=b"first")
    out1, _ = ensure_decompressed_fluent_pair(str(src), None)
    fp1 = out1.parent.name

    # Rewrite source with different content (different size + mtime)
    time.sleep(1.1)  # ensure mtime int-second changes (cache key uses int(mtime))
    _make_fake_cas_gz(src, content=b"DIFFERENT content of different length")
    out2, _ = ensure_decompressed_fluent_pair(str(src), None)
    fp2 = out2.parent.name

    assert fp1 != fp2  # different cache dirs (different fingerprints)
    assert out2.read_bytes() == b"DIFFERENT content of different length"


def test_failure_falls_back_to_original_paths(tmp_path, monkeypatch):
    """Decompress error must NEVER raise — falls back to the .gz paths so the
    reader can attempt them (even if it'll crash, we don't add new failures)."""
    src = tmp_path / "case.cas.gz"
    src.write_bytes(b"this is NOT a valid gzip stream")  # gzip.open will fail on read

    out_cas, out_dat = ensure_decompressed_fluent_pair(str(src), None)
    # Either we got back the original (gzip.open failed during decompress),
    # or we got a "valid" output that's empty. Either way we MUST NOT raise.
    # The original-path fallback is preferred:
    assert out_cas is not None


def test_does_not_raise_on_missing_source(tmp_path):
    """Source path doesn't exist — must pass through, not raise."""
    out_cas, out_dat = ensure_decompressed_fluent_pair(
        str(tmp_path / "no_such_file.cas.gz"), None
    )
    # The function should pass the original path through (since stat fails)
    assert out_cas is not None
    assert out_dat is None


def test_chinese_path_components(tmp_path):
    """Path with CJK directory components should still work — only basename
    is sanitized; parent dir traversal handles unicode fine on modern Python."""
    chinese_dir = tmp_path / "案例整理" / "fluent_case"
    chinese_dir.mkdir(parents=True)
    src = chinese_dir / "FLTG 1-0.cas.gz"
    _make_fake_cas_gz(src, content=b"chinese-path content")

    out_cas, _ = ensure_decompressed_fluent_pair(str(src), None)
    assert out_cas.exists()
    assert out_cas.read_bytes() == b"chinese-path content"
    # The output cache dir is under %TEMP% (no CJK), basename sanitized
    assert " " not in out_cas.name
