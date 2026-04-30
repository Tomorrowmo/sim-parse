"""CGNS Tier 1 — Identify (HDF5-CGNS + ADF-CGNS).

Smart redirect: when an ADF-format .cgns is given, scan sibling files for
a converted HDF5-CGNS version (e.g. produced by `cgnsconvert -h`). If
found and content-confirmed, transparently switch the identity to the
HDF5 sibling so all downstream tiers work end-to-end. The redirect is
recorded in `_warnings` so the user knows what happened.

Sibling-file naming patterns recognized (matches what cgnsconvert / users
typically produce):
    <base>.hdf5.cgns
    <base>.h5.cgns
    <base>_hdf5.cgns
    <base>-hdf5.cgns
    <base>.cgns.h5
"""
from __future__ import annotations

from pathlib import Path

from sim_parse.core.errors import never_raise
from sim_parse.core.provenance import (
    FieldProvenance,
    add_warning,
    init_tier_output,
    set_field,
)


@never_raise(default=None)
def identify(case_root: Path) -> dict | None:
    case_root = Path(case_root)

    if case_root.is_file():
        return _check_file(case_root, case_root_kind="file", case_dir=case_root.parent)

    if case_root.is_dir():
        cgns_files = sorted(case_root.glob("*.cgns"))
        cgns_files = [c for c in cgns_files if c.is_file()]
        if not cgns_files:
            return None
        if len(cgns_files) == 1:
            return _check_file(cgns_files[0], case_root_kind="directory",
                               case_dir=case_root)
        # Multiple .cgns files — treat as a time/iter sequence, pick the
        # last (typically latest time) as the representative for T3/T4
        # analysis, and surface the full list in `sequence_files` so the
        # inventory tier can list them.
        from sim_parse.core.sequence import detect_file_sequence
        seq = detect_file_sequence(cgns_files)
        if seq is None:
            return None
        identity = _check_file(seq["representative"],
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
            f"directory contains {seq['n_files']} .cgns files; using "
            f"{seq['representative'].name} as representative (last in "
            f"{seq['pattern_description']} order). All files listed in "
            f"`sequence_files`. Pass a specific file path to parse_case() "
            f"if you want a different one."
        ]
        return identity

    return None


def _hdf5_sibling_patterns(adf_path: Path) -> list[Path]:
    """Generate candidate HDF5 sibling paths for an ADF .cgns file.

    Strips trailing `.cgns` and re-suffixes with the patterns above.
    """
    name = adf_path.name
    if not name.lower().endswith(".cgns"):
        return []
    base_str = name[: -len(".cgns")]
    parent = adf_path.parent
    candidates_names = [
        f"{base_str}.hdf5.cgns",
        f"{base_str}.h5.cgns",
        f"{base_str}_hdf5.cgns",
        f"{base_str}-hdf5.cgns",
        f"{base_str}.cgns.h5",
    ]
    # Drop self if any pattern coincides with the input.
    return [parent / n for n in candidates_names if (parent / n) != adf_path]


def _find_hdf5_sibling(adf_path: Path) -> Path | None:
    """Return the first HDF5-CGNS sibling whose magic bytes match, else None."""
    for cand in _hdf5_sibling_patterns(adf_path):
        if not cand.is_file():
            continue
        try:
            head = cand.read_bytes()[:8]
        except OSError:
            continue
        if head.startswith(b"\x89HDF"):
            # Defense: also confirm CGNS-shaped HDF5
            if _hdf5_smells_like_cgns(cand):
                return cand
    return None


def _check_file(path: Path, *, case_root_kind: str, case_dir: Path) -> dict | None:
    """Confirm content + classify HDF5 vs ADF subtype."""
    try:
        head = path.read_bytes()[:64]
    except OSError:
        return None

    if head.startswith(b"\x89HDF"):
        # HDF5-CGNS: confirm via h5py for a top-level CGNSBase_* or
        # CGNSLibraryVersion group. (We're lenient — different vendors
        # arrange the root differently.)
        sub_format = "hdf5"
        if not _hdf5_smells_like_cgns(path):
            return None
        return _build_identity(path, sub_format=sub_format, version=None,
                               case_root_kind=case_root_kind, case_dir=case_dir)

    if path.suffix.lower() != ".cgns":
        # Don't accept arbitrary files — extension required for non-HDF5.
        return None

    if b"ADF Database Version" in head:
        # Smart redirect: prefer a converted HDF5 sibling if one exists.
        # User runs `cgnsconvert -h foo.cgns foo.hdf5.cgns` ONCE; thereafter
        # every parse_case() on the original ADF path automatically uses
        # the HDF5 version without re-typing the conversion step.
        sibling = _find_hdf5_sibling(path)
        if sibling is not None:
            redirected = _build_identity(
                sibling,
                sub_format="hdf5",
                version=None,
                case_root_kind=case_root_kind,
                case_dir=case_dir,
            )
            add_warning(redirected,
                f"original input {path.name} is ADF-format CGNS; "
                f"automatically redirected to converted HDF5 sibling "
                f"{sibling.name} (found alongside).")
            redirected["redirected_from"] = str(path)
            return redirected

        # No sibling — emit ADF identity. Tier 2 will give the user the
        # exact cgnsconvert command to produce a sibling.
        try:
            idx = head.find(b"ADF Database Version")
            version_bytes = head[idx + 21: idx + 28]
            version = version_bytes.decode("ascii", errors="replace").strip()
        except Exception:
            version = None
        return _build_identity(path, sub_format="adf", version=version,
                               case_root_kind=case_root_kind, case_dir=case_dir)

    return None


def _hdf5_smells_like_cgns(path: Path) -> bool:
    """Quick HDF5 introspection: any top-level group whose name starts with
    CGNSBase / CGNSLibraryVersion / Base — these are the standard CGNS
    root markers across vendor exports."""
    try:
        import h5py
    except ImportError:
        # Without h5py we can't introspect — accept on magic bytes alone.
        return True
    try:
        with h5py.File(path, "r") as f:
            keys = list(f.keys())
            for k in keys:
                if k.startswith("CGNSBase") or k.startswith("CGNSLibraryVersion") \
                        or k == "Base":
                    return True
            # Some vendors store CGNS markers in attrs
            attrs = dict(f.attrs)
            for v in attrs.values():
                if isinstance(v, (bytes, bytearray)) and b"CGNS" in v:
                    return True
    except Exception:
        return False
    return False


def _build_identity(path: Path, *, sub_format: str, version: str | None,
                    case_root_kind: str, case_dir: Path) -> dict:
    out = init_tier_output("A")
    set_field(out, "format", "cgns",
              FieldProvenance("A",
                  f"{sub_format.upper()} signature ({'0x89HDF' if sub_format=='hdf5' else 'ADF Database Version'})"))
    set_field(out, "sub_format", sub_format,
              FieldProvenance("A", "HDF5 or ADF subtype"))
    if version:
        set_field(out, "version", version,
                  FieldProvenance("A", "ADF version stamp"))
    set_field(out, "file_path", str(path),
              FieldProvenance("A", "located .cgns file"))
    set_field(out, "case_dir", str(case_dir),
              FieldProvenance("A", "directory containing the .cgns"))
    set_field(out, "case_root_kind", case_root_kind,
              FieldProvenance("A", "input was directory or file"))
    set_field(out, "solver", "cgns",
              FieldProvenance("A", "no upstream solver named in file"))
    return out
