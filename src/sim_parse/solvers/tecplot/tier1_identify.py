"""Tecplot Tier 1 — Identify (single-file primary)."""
from __future__ import annotations

from pathlib import Path

from sim_parse.core.errors import never_raise
from sim_parse.core.provenance import (
    FieldProvenance,
    init_tier_output,
    set_field,
)


@never_raise(default=None)
def identify(case_root: Path) -> dict | None:
    case_root = Path(case_root)

    # Tecplot is fundamentally file-oriented, but we accept directories
    # that contain exactly one .plt or .dat file (and content-confirms).
    if case_root.is_dir():
        candidates = sorted(
            list(case_root.glob("*.plt")) + list(case_root.glob("*.dat"))
        )
        candidates = [c for c in candidates if c.is_file()]
        # Filter to ones that look like Tecplot (binary TDV magic or ASCII keywords)
        confirmed = [c for c in candidates if _is_tecplot(c)]
        if not confirmed:
            return None
        if len(confirmed) == 1:
            return _build_identity(confirmed[0], case_root_kind="directory",
                                   case_dir=case_root)
        # Multiple Tecplot files — treat as time/iter sequence
        from sim_parse.core.sequence import detect_file_sequence
        seq = detect_file_sequence(confirmed)
        if seq is None:
            return None
        identity = _build_identity(seq["representative"],
                                   case_root_kind="directory_sequence",
                                   case_dir=case_root)
        if identity is None:
            return None
        identity["sequence_files"] = [str(f) for f in seq["all_files"]]
        identity["n_sequence_files"] = seq["n_files"]
        identity["sequence_pattern"] = seq["pattern_description"]
        if seq["time_values"] is not None:
            identity["sequence_time_values"] = seq["time_values"]
        identity["_warnings"] = identity.get("_warnings", []) + [
            f"directory contains {seq['n_files']} Tecplot files; using "
            f"{seq['representative'].name} as representative (last in "
            f"{seq['pattern_description']} order). All files listed in "
            f"`sequence_files`."
        ]
        return identity

    if case_root.is_file():
        # Don't gate on extension. Tecplot binary content is sometimes
        # shipped under .bin / .tec / .out etc. by various solvers
        # (CFD++ in particular uses .bin). Trust the magic check instead.
        if not _is_tecplot(case_root):
            return None
        return _build_identity(case_root, case_root_kind="file",
                               case_dir=case_root.parent)

    return None


def _is_tecplot(path: Path) -> bool:
    try:
        head = path.read_bytes()[:1024]
    except OSError:
        return False
    if head.startswith(b"#!TDV"):
        return True
    upper = head.decode("utf-8", errors="replace").upper()
    if "TITLE" in upper and "VARIABLES" in upper:
        return True
    if "ZONE" in upper and "VARIABLES" in upper:
        return True
    return False


def _build_identity(path: Path, *, case_root_kind: str, case_dir: Path) -> dict:
    out = init_tier_output("A")

    try:
        head = path.read_bytes()[:8]
    except OSError:
        head = b""

    if head.startswith(b"#!TDV"):
        version = head[5:8].decode("ascii", errors="replace").strip()
        sub_format = f"binary_v{version}"
    else:
        sub_format = "ascii"

    set_field(out, "format", "tecplot",
              FieldProvenance("A", "magic '#!TDV' or ASCII TITLE/VARIABLES keywords"))
    set_field(out, "sub_format", sub_format,
              FieldProvenance("A", "TDV version stamp / ASCII detection"))
    set_field(out, "file_path", str(path),
              FieldProvenance("A", "input file"))
    set_field(out, "case_dir", str(case_dir),
              FieldProvenance("A", "directory containing the .plt"))
    set_field(out, "case_root_kind", case_root_kind,
              FieldProvenance("A", "input was directory or file"))
    # Tecplot has no upstream-solver info; user knows it's a result file.
    set_field(out, "solver", "tecplot",
              FieldProvenance("A", "tecplot result file (no upstream solver name)"))
    return out
