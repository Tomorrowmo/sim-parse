"""VTK XML Tier 1 — Identify."""
from __future__ import annotations

from pathlib import Path

from sim_parse.core.errors import never_raise
from sim_parse.core.provenance import (
    FieldProvenance,
    init_tier_output,
    set_field,
)
from sim_parse.solvers.vtk_xml._readers import reader_for_extension


@never_raise(default=None)
def identify(case_root: Path) -> dict | None:
    case_root = Path(case_root)

    if case_root.is_file():
        return _check_file(case_root, case_root_kind="file",
                           case_dir=case_root.parent)

    if case_root.is_dir():
        # Find a single VTK XML file at top level
        candidates: list[Path] = []
        for ext in (".vtu", ".vtp", ".vts", ".vti", ".vtr",
                    ".vtm", ".vtmb",
                    ".pvtu", ".pvtp", ".pvts", ".pvti", ".pvtr"):
            candidates.extend(case_root.glob(f"*{ext}"))
        candidates = sorted([c for c in candidates if c.is_file()])
        # Collection files (.vtm / .pvt*) take priority — they're entry points
        collection = [c for c in candidates
                      if c.suffix.lower() in (".vtm", ".vtmb",
                                              ".pvtu", ".pvtp", ".pvts",
                                              ".pvti", ".pvtr")]
        if len(collection) == 1:
            return _check_file(collection[0], case_root_kind="directory",
                               case_dir=case_root)
        if len(candidates) == 1:
            return _check_file(candidates[0], case_root_kind="directory",
                               case_dir=case_root)

        # Multiple VTU XML files — treat as a sequence. Group by extension
        # so we don't mix .vtu with .vtp (different data types).
        from sim_parse.core.sequence import detect_file_sequence
        if collection and len(collection) > 1:
            return _build_sequence_identity(collection, case_root)
        if candidates and len(candidates) > 1:
            # Group by extension; pick the largest group as representative
            from collections import defaultdict
            by_ext: dict[str, list[Path]] = defaultdict(list)
            for c in candidates:
                by_ext[c.suffix.lower()].append(c)
            largest_group = max(by_ext.values(), key=len)
            if len(largest_group) >= 2:
                return _build_sequence_identity(largest_group, case_root)
        return None

    return None


def _build_sequence_identity(files: list[Path], case_dir: Path) -> dict | None:
    """Helper for the multi-file VTK XML branch — pick representative,
    augment identity with sequence info.

    For .vtm collection files: each .vtm references external pieces (.vtr /
    .vtu). Sequence detection naively picks the last by time, but the user's
    last .vtm may have stale references (data dir deleted / never written).
    Walk backward from the latest until we find one with at least its first
    referenced piece existing on disk.
    """
    from sim_parse.core.sequence import detect_file_sequence
    seq = detect_file_sequence(files)
    if seq is None:
        return None

    # For collection files, validate that the chosen representative's first
    # referenced piece actually exists. If not, walk backward.
    representative = seq["representative"]
    sorted_files = list(seq["all_files"])
    if representative.suffix.lower() in (".vtm", ".vtmb",
                                          ".pvtu", ".pvtp", ".pvts",
                                          ".pvti", ".pvtr"):
        for candidate in reversed(sorted_files):
            if _vtm_first_piece_exists(candidate):
                representative = candidate
                break

    identity = _check_file(representative,
                           case_root_kind="directory_sequence",
                           case_dir=case_dir)
    if identity is None:
        return None
    identity["sequence_files"] = [str(f) for f in seq["all_files"]]
    identity["n_sequence_files"] = seq["n_files"]
    identity["sequence_pattern"] = seq["pattern_description"]
    if seq["time_values"] is not None:
        identity["sequence_time_values"] = seq["time_values"]
    note = (
        f"directory contains {seq['n_files']} VTK XML files of the same type; "
        f"using {representative.name} as representative"
    )
    if representative != seq["representative"]:
        note += (f" (originally would have picked {seq['representative'].name} "
                 f"as last-by-time, but its referenced data files are missing "
                 f"— walked backward to a complete snapshot)")
    else:
        note += f" (last in {seq['pattern_description']} order)."
    identity["_warnings"] = identity.get("_warnings", []) + [note]
    return identity


def _vtm_first_piece_exists(vtm_path: Path) -> bool:
    """Quick check: parse a .vtm / .pvt* XML and verify the first piece
    file referenced actually exists relative to the .vtm.

    Returns True if EITHER (a) the file isn't a collection (no `file=` refs)
    or (b) at least one referenced piece exists on disk.
    """
    try:
        text = vtm_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    # Look for first file="..." attribute
    import re
    m = re.search(r'file\s*=\s*"([^"]+)"', text)
    if not m:
        # No external refs — single-file VTK XML; trust it exists if the .vtm itself does
        return True
    rel = m.group(1)
    candidate = vtm_path.parent / rel
    return candidate.is_file()


def _check_file(path: Path, *, case_root_kind: str, case_dir: Path) -> dict | None:
    ext = path.suffix.lower()
    reader_class, xml_type = reader_for_extension(ext)
    if not reader_class:
        return None

    # Quick content check: file must start with '<VTKFile' (after optional BOM)
    try:
        with open(path, "rb") as f:
            head = f.read(256)
    except OSError:
        return None
    text = head.decode("utf-8", errors="replace").lstrip("﻿").lstrip()
    if not text.startswith("<VTKFile") and not text.startswith("<?xml"):
        return None

    out = init_tier_output("A")
    set_field(out, "format", "vtk_xml",
              FieldProvenance("A", "<VTKFile> XML signature + extension match"))
    set_field(out, "sub_format", ext.lstrip("."),
              FieldProvenance("A", f"file extension {ext}"))
    set_field(out, "vtk_xml_type", xml_type,
              FieldProvenance("A", "expected VTKFile type from extension"))
    set_field(out, "file_path", str(path),
              FieldProvenance("A", "input file"))
    set_field(out, "case_dir", str(case_dir),
              FieldProvenance("A", "directory containing the file"))
    set_field(out, "case_root_kind", case_root_kind,
              FieldProvenance("A", "input was directory or file"))
    set_field(out, "solver", "vtk_xml",
              FieldProvenance("A", "VTK XML result file (no upstream solver)"))
    set_field(out, "reader_class", reader_class,
              FieldProvenance("A", "matching vtkXML reader class"))
    return out
