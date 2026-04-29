"""Fluent Tier 1 — Identify.

Three sub-format variants handled here:
    legacy  : <case>.cas (+ optional <case>.dat)
    legacy_gz: <case>.cas.gz (+ optional <case>.dat.gz)
    cff     : <case>.cas.h5 (+ optional <case>.dat.h5) — V19+ HDF5

Detection rules:
    - case_root is a directory: scan for .cas / .cas.gz / .cas.h5 files
    - case_root is a file: check its extension + magic bytes
"""
from __future__ import annotations

import gzip
from pathlib import Path

from sim_parse.core.errors import never_raise
from sim_parse.core.provenance import (
    FieldProvenance,
    init_tier_output,
    set_field,
)


HDF5_MAGIC = b"\x89HDF\r\n\x1a\n"
GZIP_MAGIC = b"\x1f\x8b"


@never_raise(default=None)
def identify(case_root: Path) -> dict | None:
    case_root = Path(case_root)

    # Case 1: directory — find a .cas-like file inside
    if case_root.is_dir():
        cas_path = _find_case_file_in_dir(case_root)
        if cas_path is None:
            return None
        return _build_identity(cas_path, case_root_kind="directory", case_root=case_root)

    # Case 2: single file — check extension + magic
    if case_root.is_file():
        return _build_identity(case_root, case_root_kind="file", case_root=case_root.parent)

    return None


def _find_case_file_in_dir(d: Path) -> Path | None:
    """Find a .cas / .cas.gz / .cas.h5 in directory `d`.

    Priority: .cas.h5 > .cas > .cas.gz (newer formats first).
    """
    candidates_priority = [
        ("*.cas.h5", "cff"),
        ("*.cas",    "legacy"),
        ("*.cas.gz", "legacy_gz"),
    ]
    for pattern, _kind in candidates_priority:
        matches = sorted(d.glob(pattern))
        if matches:
            return matches[0]
    return None


def _build_identity(cas_path: Path, case_root_kind: str, case_root: Path) -> dict | None:
    """Verify the file looks like Fluent + extract sub-format + check pairing."""
    name = cas_path.name.lower()

    # Determine sub-format from extension chain
    if name.endswith(".cas.h5"):
        sub_format = "cff"
        is_hdf5 = True
        is_gzip = False
        base = cas_path.with_name(cas_path.name[:-7])  # strip ".cas.h5"
    elif name.endswith(".cas.gz"):
        sub_format = "legacy_gz"
        is_hdf5 = False
        is_gzip = True
        base = cas_path.with_name(cas_path.name[:-7])  # strip ".cas.gz"
    elif name.endswith(".cas"):
        sub_format = "legacy"
        is_hdf5 = False
        is_gzip = False
        base = cas_path.with_name(cas_path.name[:-4])  # strip ".cas"
    else:
        return None

    # Verify magic bytes
    if not _verify_magic(cas_path, expect_hdf5=is_hdf5, expect_gzip=is_gzip):
        return None

    # Look for paired .dat file
    dat_path = _find_paired_dat(base, sub_format)

    out = init_tier_output("A")
    set_field(out, "format", "fluent",
              FieldProvenance("A", f"extension={cas_path.suffix.lower()} + magic verified"))
    set_field(out, "sub_format", sub_format,
              FieldProvenance("A", "extension chain"))
    set_field(out, "compressed", is_gzip,
              FieldProvenance("A", "extension chain"))
    set_field(out, "cas_path", str(cas_path),
              FieldProvenance("A", "file or dir scan"))
    set_field(out, "dat_path", str(dat_path) if dat_path else None,
              FieldProvenance("A", "paired-file search"))
    set_field(out, "has_paired_results", dat_path is not None,
              FieldProvenance("A", ".dat / .dat.h5 / .dat.gz pairing"))
    set_field(out, "case_root_kind", case_root_kind,
              FieldProvenance("A", "input was directory or file"))

    # Solver classification: density-based vs pressure-based, steady vs transient,
    # would need reader access — defer to Tier 3
    set_field(out, "solver", "fluent",
              FieldProvenance("A", "fluent (sub-classification deferred to Tier 3)"))

    return out


def _verify_magic(path: Path, expect_hdf5: bool, expect_gzip: bool) -> bool:
    """Read first 16 bytes; verify they match expected magic."""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return False

    if expect_hdf5:
        return head[:8] == HDF5_MAGIC
    if expect_gzip:
        return head[:2] == GZIP_MAGIC
    # legacy: no fixed magic, but check it's NOT gzip / HDF5 (would be wrong sub-format)
    if head[:2] == GZIP_MAGIC or head[:8] == HDF5_MAGIC:
        return False
    return True


def _find_paired_dat(base: Path, sub_format: str) -> Path | None:
    """Find <base>.dat / .dat.gz / .dat.h5 alongside the .cas file."""
    if sub_format == "cff":
        candidate = base.with_name(base.name + ".dat.h5")
    elif sub_format == "legacy_gz":
        candidate = base.with_name(base.name + ".dat.gz")
    else:
        candidate = base.with_name(base.name + ".dat")
    return candidate if candidate.is_file() else None
