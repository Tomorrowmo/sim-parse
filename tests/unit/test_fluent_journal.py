"""Unit tests for the Fluent .jou (TUI journal) parser.

Tests use synthetic .jou files written to tmp_path so they don't depend
on real Fluent test data. End-to-end coverage on a real case happens
in tests/integration/ (when fixture data is available).
"""
from __future__ import annotations

import textwrap

from sim_parse.solvers.fluent._journal_parser import (
    _try_match_bc,
    _try_match_export,
    _try_match_file_read,
    _try_match_file_write,
    _try_match_solve,
    _try_match_viscous,
    parse_fluent_journals,
)


# ─── Per-pattern matcher tests ────────────────────────────────────────────────


def _empty_out():
    """Fresh output dict matching what _parse_one_journal builds."""
    return {
        "boundary_conditions": [], "case_lineage_reads": [],
        "case_lineage_writes": [], "exports": [], "_lines_recognized": 0,
    }


def test_match_viscous_spalart_allmaras():
    out = _empty_out()
    assert _try_match_viscous("/define/models/viscous/spalart-allmaras", 1, out)
    assert out["viscous_model"]["model"] == "spalart-allmaras"
    assert out["viscous_model"]["_source_line"] == 1


def test_match_viscous_with_space_navigation():
    """Fluent TUI accepts space OR slash as menu separator."""
    out = _empty_out()
    assert _try_match_viscous("/define models viscous kw-sst", 5, out)
    assert out["viscous_model"]["model"] == "kw-sst"


def test_match_viscous_unknown_model_skipped():
    """Unrecognized model identifier doesn't pollute output (defensive
    against /define/models/viscous/turb-coeffs-default and similar
    sub-commands that aren't model selectors)."""
    out = _empty_out()
    assert not _try_match_viscous("/define/models/viscous/turb-coeffs-default", 1, out)
    assert "viscous_model" not in out


def test_match_bc_pressure_far_field_with_mach():
    """Real-world case: /define/boundary-conditions/set/pressure-far-field
    tria-1-inlet() mach number no 3 q"""
    line = "/define/boundary-conditions/set/pressure-far-field tria-1-inlet() mach number no 3 q"
    out = _empty_out()
    assert _try_match_bc(line, 2, out)
    bc = out["boundary_conditions"][0]
    assert bc["zone"] == "tria-1-inlet"
    assert bc["type"] == "pressure-far-field"
    assert bc["param"] == "mach number"
    assert bc["value"] == 3.0


def test_match_bc_velocity_inlet_single_param():
    line = "/define/boundary-conditions/set/velocity-inlet inlet() vmag no 50 q"
    out = _empty_out()
    assert _try_match_bc(line, 1, out)
    bc = out["boundary_conditions"][0]
    assert bc["param"] == "vmag"
    assert bc["value"] == 50.0


def test_match_bc_negative_value():
    line = "/define/boundary-conditions/set/wall body() temperature no -273.15 q"
    out = _empty_out()
    assert _try_match_bc(line, 1, out)
    assert out["boundary_conditions"][0]["value"] == -273.15


def test_match_bc_scientific_notation():
    line = "/define/boundary-conditions/set/wall body() heat-flux no 1.5e6 q"
    out = _empty_out()
    assert _try_match_bc(line, 1, out)
    assert out["boundary_conditions"][0]["value"] == 1.5e6


def test_match_solve_iterate():
    out = _empty_out()
    assert _try_match_solve("/solve iterate 200", 4, out)
    assert out["solve_settings"]["iterations_target"] == 200


def test_match_solve_iterate_accumulates():
    """Multiple /solve iterate commands sum (e.g. user did 100, then 100 more)."""
    out = _empty_out()
    _try_match_solve("/solve iterate 100", 1, out)
    _try_match_solve("/solve iterate 50", 5, out)
    assert out["solve_settings"]["iterations_target"] == 150


def test_match_solve_dual_time_iterate():
    out = _empty_out()
    assert _try_match_solve("/solve/dual-time-iterate 25", 1, out)
    assert out["solve_settings"]["iterations_target"] == 25


def test_match_export_cgns_with_field_list():
    """Real-world: /file/export cgns hb2steady.cgns no pressure x-velocity ... quit
    (the `no` answers a "include solid zones?" prompt — must be filtered out)."""
    line = ("/file/export cgns hb2steady.cgns no pressure x-velocity y-velocity "
            "z-velocity mach-number temperature density quit")
    out = _empty_out()
    assert _try_match_export(line, 6, out)
    exp = out["exports"][0]
    assert exp["format"] == "cgns"
    assert exp["output_file"] == "hb2steady.cgns"
    assert "pressure" in exp["fields"]
    assert "no" not in exp["fields"]      # answer keywords filtered
    assert len(exp["fields"]) == 7


def test_match_file_read():
    out = _empty_out()
    assert _try_match_file_read("/file/read steadyhb2.cas", 1, out)
    assert out["case_lineage_reads"] == ["steadyhb2.cas"]


def test_match_file_read_with_space_navigation():
    out = _empty_out()
    assert _try_match_file_read("/file read foo.cas", 1, out)
    assert out["case_lineage_reads"] == ["foo.cas"]


def test_match_file_write_case_dat():
    """Compact form: /file write-case-dat <name> writes both .cas and .dat."""
    out = _empty_out()
    assert _try_match_file_write("/file write-case-dat out.cas", 5, out)
    assert out["case_lineage_writes"] == ["out.cas"]


# ─── End-to-end on a synthetic .jou (the real-case pattern) ──────────────────


def test_parse_real_world_journal_pattern(tmp_path):
    """Mirrors the exact runfluent.jou we found in fluent_TUI命令自动计算算例
    — confirms all 6 high-value commands extract correctly in one parse."""
    jou_text = textwrap.dedent("""\
        /file/read steadyhb2.cas
        /define/boundary-conditions/set/pressure-far-field tria-1-inlet() mach number no 3 q
        /define/boundary-conditions/set/pressure-far-field tria-2-outlet() mach number no 3 q
        /solve iterate 200
        /file write-case-dat out.cas
        /file/export cgns hb2steady.cgns no pressure x-velocity y-velocity z-velocity mach-number velocity-magnitude temperature density quit
        /exit
    """)
    (tmp_path / "runfluent.jou").write_text(jou_text, encoding="utf-8")

    result = parse_fluent_journals(tmp_path, ["runfluent.jou"])
    assert result is not None

    # BCs
    assert len(result["boundary_conditions"]) == 2
    zones = {bc["zone"] for bc in result["boundary_conditions"]}
    assert zones == {"tria-1-inlet", "tria-2-outlet"}
    assert all(bc["value"] == 3.0 for bc in result["boundary_conditions"])

    # solve
    assert result["solve_settings"]["iterations_target"] == 200

    # case lineage (read + write order preserved, deduped)
    assert result["case_lineage"] == ["steadyhb2.cas", "out.cas"]

    # exports
    assert len(result["exports"]) == 1
    exp = result["exports"][0]
    assert exp["format"] == "cgns"
    assert exp["output_file"] == "hb2steady.cgns"
    assert "pressure" in exp["fields"]

    # 6/7 lines recognized (the /exit isn't a tracked command)
    assert result["_total_commands_recognized"] >= 6


def test_parse_journal_with_viscous_model(tmp_path):
    """A journal that selects a turbulence model — should populate viscous_model
    so Tier 3 can upgrade physics_setup.turbulence from NotExtracted."""
    jou_text = textwrap.dedent("""\
        /file/read mesh.cas
        /define/models/viscous/spalart-allmaras
        /solve iterate 1000
    """)
    (tmp_path / "init.jou").write_text(jou_text, encoding="utf-8")

    result = parse_fluent_journals(tmp_path, ["init.jou"])
    assert result["viscous_model"]["model"] == "spalart-allmaras"
    assert result["viscous_model"]["_source_file"] == "init.jou"


def test_parse_journal_skips_blank_and_comments(tmp_path):
    """Blank lines + ; comments don't count as commands; recognized counter
    only increments on real matches."""
    jou_text = textwrap.dedent("""\
        ; This is a Fluent journal with comments
        ; Author: someone

        /file/read foo.cas

        ; the actual solve
        /solve iterate 100
    """)
    (tmp_path / "x.jou").write_text(jou_text, encoding="utf-8")
    result = parse_fluent_journals(tmp_path, ["x.jou"])
    assert result["_total_commands_recognized"] == 2
    # ~7 lines total but only 2 commands; comments + blank lines counted in _total_lines
    assert result["_total_lines"] >= 6


def test_parse_journal_returns_none_for_empty_file(tmp_path):
    (tmp_path / "empty.jou").write_text("", encoding="utf-8")
    assert parse_fluent_journals(tmp_path, ["empty.jou"]) is None


def test_parse_journal_returns_none_for_missing_file(tmp_path):
    """Inventory listed a file that's no longer there — silent None, no crash."""
    assert parse_fluent_journals(tmp_path, ["nonexistent.jou"]) is None


def test_parse_journal_multifile_ordering(tmp_path):
    """When multiple .jou files exist, they're processed in given order;
    later viscous_model wins; case_lineage accumulates."""
    (tmp_path / "init.jou").write_text(
        "/define/models/viscous/laminar\n/file/read base.cas\n",
        encoding="utf-8")
    (tmp_path / "run.jou").write_text(
        "/define/models/viscous/kw-sst\n/file write-case-dat result.cas\n",
        encoding="utf-8")

    result = parse_fluent_journals(tmp_path, ["init.jou", "run.jou"])
    # Last-write wins for scalar
    assert result["viscous_model"]["model"] == "kw-sst"
    assert result["viscous_model"]["_source_file"] == "run.jou"
    # Lineage accumulates in order
    assert result["case_lineage"] == ["base.cas", "result.cas"]
