"""Unit tests for Fluent Tier 4 — verifying the same 5 mechanisms apply.

Mechanism #1 (kind verification) — verify_variable_kinds with tier2_meta=None.
Mechanism #4 (convergence) — _parse_fluent_trn_residuals from .trn text.
Other mechanisms (#2 / #3 / #5) covered by cross-solver / qoi_engine tests.
"""
from __future__ import annotations

import textwrap

from sim_parse.core.diagnostics import verify_variable_kinds
from sim_parse.solvers.fluent.tier4_field_stats import (
    _convergence_orders,
    _parse_fluent_trn_residuals,
    _parse_monitor_types_from_cas,
)


# ─── .trn residual parser ─────────────────────────────────────────────────────


def _write_trn(tmp_path, text: str):
    """Write a fake Fluent .trn file under tmp_path and return the Path list."""
    p = tmp_path / "fake.trn"
    p.write_text(text, encoding="utf-8")
    return [p]


def test_parse_trn_extracts_named_columns(tmp_path):
    """Header columns 'continuity / x-velocity / energy' must each get a
    history list. The 'iter' and 'time/iter' columns are metadata and must
    NOT appear as residual variables."""
    sample = textwrap.dedent("""\

      iter  continuity  x-velocity  y-velocity  z-velocity      energy     time/iter
         1  1.0000e+00  0.0000e+00  2.0888e-15  2.3211e-15  4.9717e-04  0:23:13  199
         2  1.0000e+00  4.0283e-04  2.1161e-05  1.9773e-05  2.1115e-04  0:25:05  198
         3  1.0000e+00  2.8910e-04  3.3212e-05  2.9039e-05  1.3479e-04  0:23:15  197
    """)
    files = _write_trn(tmp_path, sample)
    res = _parse_fluent_trn_residuals(files)

    assert "iter" not in res
    assert "time/iter" not in res
    assert set(res.keys()) == {"continuity", "x-velocity", "y-velocity", "z-velocity", "energy"}
    # Each column has 3 iterations recorded
    for col, hist in res.items():
        assert len(hist) == 3, f"column {col!r} has {len(hist)} iters"


def test_parse_trn_repeated_headers_accumulate(tmp_path):
    """Fluent re-prints headers periodically. The parser should treat each
    header repeat as a continuation, not a reset, and accumulate residuals."""
    sample = textwrap.dedent("""\
      iter  continuity  x-velocity      time/iter
         1  1.0e+00     2.0e-15         0:23:13  199
         2  1.0e+00     4.0e-04         0:25:05  198

      iter  continuity  x-velocity      time/iter
         3  9.0e-01     5.0e-04         0:23:15  197
    """)
    files = _write_trn(tmp_path, sample)
    res = _parse_fluent_trn_residuals(files)
    assert len(res["continuity"]) == 3
    assert len(res["x-velocity"]) == 3


def test_parse_trn_returns_empty_for_no_header(tmp_path):
    """File with no header line yields empty result, doesn't crash."""
    sample = "some random text\nno residual table here\n"
    files = _write_trn(tmp_path, sample)
    res = _parse_fluent_trn_residuals(files)
    assert res == {}


def test_parse_trn_returns_empty_for_missing_files(tmp_path):
    """A non-existent .trn file is gracefully skipped (never_raise contract)."""
    res = _parse_fluent_trn_residuals([tmp_path / "missing.trn"])
    assert res == {}


# ─── _convergence_orders ──────────────────────────────────────────────────────


def test_convergence_orders_drop():
    """Residual that dropped 6 orders → returns 6.0."""
    history = [[1.0, 1.0], [0.5, 0.5], [1e-6, 1e-6]]
    assert _convergence_orders(history) == 6.0


def test_convergence_orders_increased():
    """Residual that grew → negative number (diverged)."""
    history = [[1e-3, 1e-3], [1.0, 1.0]]
    val = _convergence_orders(history)
    assert val < 0


def test_convergence_orders_handles_zero_safely():
    """Zero or negative values must NOT divide-by-zero or log NaN."""
    assert _convergence_orders([[0.0, 0.0], [1e-3, 1e-3]]) is None
    assert _convergence_orders([[1.0, 1.0], [0.0, 0.0]]) is None


def test_convergence_orders_empty_returns_none():
    assert _convergence_orders([]) is None


# ─── verify_variable_kinds for Fluent (no Tier 2 pre-classification) ──────────


def test_fluent_verified_kinds_no_tier2_meta():
    """Fluent doesn't have a Tier 2 file-presence inventory of variables
    (vtkFLUENTReader has to Update() to know cell array names). Calling
    verify_variable_kinds with tier2_meta=None must produce sensible records:
    every reader-visible array → physical / cell_scalar (or vector)
    and 0 phantoms."""
    out = verify_variable_kinds(
        tier2_meta=None,
        vtk_cell_arrays=["TEMPERATURE", "PRESSURE", "X_VELOCITY"],
        rank_by_name={"TEMPERATURE": "scalar", "PRESSURE": "scalar", "X_VELOCITY": "scalar"},
    )
    for name, info in out.items():
        assert info["physical_kind"] == "physical"
        assert info["data_shape"] == "cell_scalar"
        assert info["phantom"] is False
        assert info["tier2_kind"] is None


def test_fluent_verified_kinds_vector_field():
    out = verify_variable_kinds(
        tier2_meta=None,
        vtk_cell_arrays=["BODY_FORCES"],
        rank_by_name={"BODY_FORCES": "vector"},
    )
    assert out["BODY_FORCES"]["data_shape"] == "cell_vector"


def test_fluent_verified_kinds_unknown_rank_not_phantom():
    """Empty / weird arrays come back as cell_unknown, not phantom."""
    out = verify_variable_kinds(
        tier2_meta=None,
        vtk_cell_arrays=["MACH"],
        rank_by_name={"MACH": "unknown"},
    )
    assert out["MACH"]["data_shape"] == "cell_unknown"
    assert out["MACH"]["phantom"] is False


# ─── Monitor TYPE extraction from .cas (PostDrive Skill absorption) ───────────


def _write_synth_cas_with_monitors(tmp_path, monitors: list[tuple[str, str]]):
    """Build a fake legacy .cas with a monitor/report-definitions block.

    Real Fluent .cas files mix ASCII text segments with binary section
    payloads. We write something that's mostly ASCII (sufficient to
    exercise the bytes-regex parser) plus a handful of fake binary
    bytes to confirm the parser doesn't choke on them.
    """
    p = tmp_path / "case.cas"
    parts = [b"(0 \"Fake Fluent .cas\")\n"]
    parts.append(b"\x00\x01\x02\x03 some binary noise \xff\xfe\xfd\n")
    parts.append(b"(monitor/report-definitions ")
    for name, mtype in monitors:
        parts.append(f' name "{name}" type "{mtype}"'.encode("ascii"))
    parts.append(b")\n")
    parts.append(b"\x00\x01trailing binary section\n")
    p.write_bytes(b"".join(parts))
    return p


def test_parse_monitor_types_extracts_pairs(tmp_path):
    cas = _write_synth_cas_with_monitors(tmp_path, [
        ("cd-force", "force-monitor"),
        ("cl-force", "force-monitor"),
        ("mass-flow-outlet", "surface-monitor"),
    ])
    out = _parse_monitor_types_from_cas(cas)
    assert out == {
        "cd-force": "force-monitor",
        "cl-force": "force-monitor",
        "mass-flow-outlet": "surface-monitor",
    }


def test_parse_monitor_types_returns_none_when_no_block(tmp_path):
    """A .cas without any monitor/report-definitions marker returns None
    (not an empty dict — distinguishes 'no monitors' from 'parse failed')."""
    p = tmp_path / "case.cas"
    p.write_bytes(b"(0 \"no monitors here\")\n\x00\x01plenty of binary\n")
    assert _parse_monitor_types_from_cas(p) is None


def test_parse_monitor_types_returns_none_for_missing_file(tmp_path):
    assert _parse_monitor_types_from_cas(tmp_path / "missing.cas") is None


def test_parse_monitor_types_skips_cff(tmp_path):
    """CFF (.cas.h5) needs an h5py reader, not bytes regex — return None."""
    p = tmp_path / "case.cas.h5"
    p.write_bytes(b"\x89HDF\r\n\x1a\n" + b"\x00" * 100)
    assert _parse_monitor_types_from_cas(p) is None


def test_parse_monitor_types_first_seen_wins_on_duplicates(tmp_path):
    """If the same monitor name appears twice, keep the first type
    (Fluent shouldn't emit duplicates, but be defensive)."""
    cas = _write_synth_cas_with_monitors(tmp_path, [
        ("cd-force", "force-monitor"),
        ("cd-force", "drag-coefficient"),  # spurious second entry
    ])
    out = _parse_monitor_types_from_cas(cas)
    assert out == {"cd-force": "force-monitor"}


