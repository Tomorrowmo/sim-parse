"""Plot3D Tier 1 — Identify.

Plot3D has no magic header. We require the .xyz extension at minimum
(mesh file) AND look for a companion .q (solution) — that's the
canonical Plot3D pair.
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
def identify(case_root: Path) -> dict | None:
    case_root = Path(case_root)

    if case_root.is_file():
        if case_root.suffix.lower() != ".xyz":
            return None
        # The .q file is optional — a mesh-only Plot3D is still valid
        q_file = case_root.with_suffix(".q")
        return _build_identity(case_root, q_file if q_file.is_file() else None,
                               case_root_kind="file", case_dir=case_root.parent)

    if case_root.is_dir():
        xyz_files = sorted(case_root.glob("*.xyz"))
        xyz_files = [x for x in xyz_files if x.is_file()]
        if len(xyz_files) != 1:
            return None
        xyz = xyz_files[0]
        q_file = xyz.with_suffix(".q")
        return _build_identity(xyz, q_file if q_file.is_file() else None,
                               case_root_kind="directory", case_dir=case_root)

    return None


def _build_identity(xyz: Path, q: Path | None, *,
                    case_root_kind: str, case_dir: Path) -> dict:
    out = init_tier_output("A")
    set_field(out, "format", "plot3d",
              FieldProvenance("A", "*.xyz extension; canonical Plot3D mesh file"))
    set_field(out, "xyz_path", str(xyz),
              FieldProvenance("A", "located mesh file"))
    if q:
        set_field(out, "q_path", str(q),
                  FieldProvenance("A", "paired *.q solution file"))
    set_field(out, "has_paired_solution", q is not None,
              FieldProvenance("A", "<base>.q paired-file search"))
    set_field(out, "case_dir", str(case_dir),
              FieldProvenance("A", "directory containing .xyz / .q"))
    set_field(out, "case_root_kind", case_root_kind,
              FieldProvenance("A", "input was directory or file"))
    set_field(out, "solver", "plot3d",
              FieldProvenance("A", "no upstream solver named in file"))
    return out
