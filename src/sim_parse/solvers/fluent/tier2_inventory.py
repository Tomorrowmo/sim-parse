"""Fluent Tier 2 — Inventory.

Lists what's in the case directory:
    - cas/dat file pair (with sub-format)
    - report .out files
    - .trn transcript log
    - .jou journal (TUI commands)
    - UDF library directories
    - Any companion files
"""
from __future__ import annotations

from pathlib import Path

from sim_parse.core.errors import never_raise
from sim_parse.core.provenance import (
    FieldProvenance,
    init_tier_output,
    set_field,
)


@never_raise(default=None)
def inventory(case_root: Path, identity: dict) -> dict | None:
    case_root = Path(case_root)
    out = init_tier_output("A")

    # If case_root was a single file, parent dir is where to look for siblings
    if identity.get("case_root_kind") == "file":
        scan_dir = Path(identity["cas_path"]).parent
    else:
        scan_dir = case_root

    if not scan_dir.is_dir():
        return out

    # 1. Files in scan_dir
    set_field(out, "cas_path", identity.get("cas_path"),
              FieldProvenance("A", "from Tier 1"))
    set_field(out, "dat_path", identity.get("dat_path"),
              FieldProvenance("A", "from Tier 1"))
    set_field(out, "sub_format", identity.get("sub_format"),
              FieldProvenance("A", "from Tier 1"))

    # 2. Report files (Fluent writes report-X.out for each report definition)
    report_files = sorted(p.name for p in scan_dir.glob("report-*.out"))
    if not report_files:
        report_files = sorted(p.name for p in scan_dir.glob("*.out"))
    set_field(out, "report_files", report_files,
              FieldProvenance("A", "*.out / report-*.out in case dir"))

    # 3. Transcript files (.trn)
    trn_files = sorted(p.name for p in scan_dir.glob("*.trn"))
    set_field(out, "transcript_files", trn_files,
              FieldProvenance("A", "*.trn"))

    # 4. Journal files (.jou — TUI command history)
    jou_files = sorted(p.name for p in scan_dir.glob("*.jou"))
    set_field(out, "journal_files", jou_files,
              FieldProvenance("A", "*.jou"))

    # 5. UDF directories
    udf_dirs = sorted(d.name for d in scan_dir.iterdir()
                      if d.is_dir() and d.name.lower() in ("libudf", "udf"))
    set_field(out, "udf_directories", udf_dirs,
              FieldProvenance("A", "libudf/ or udf/ subdirectories"))

    # 6. All cas/dat pairs found (multi-case scenarios)
    cas_files = sorted(set(
        list(scan_dir.glob("*.cas")) +
        list(scan_dir.glob("*.cas.gz")) +
        list(scan_dir.glob("*.cas.h5"))
    ))
    set_field(out, "case_files_found", [p.name for p in cas_files],
              FieldProvenance("A", "*.cas* in scan dir"))
    set_field(out, "n_case_files", len(cas_files),
              FieldProvenance("A", "len(case_files_found)"))

    return out
