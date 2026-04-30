"""Plot3D Tier 3 — Metadata.

vtkMultiBlockPLOT3DReader has many configuration knobs (binary vs ASCII,
endianness, single vs multi-grid, has-iblanking) that are not stored in
the file itself. We attempt sensible defaults; if probing fails we
gracefully degrade.
"""
from __future__ import annotations

from pathlib import Path

from sim_parse.core.errors import never_raise, safe_call
from sim_parse.core.provenance import (
    FieldProvenance,
    add_warning,
    init_tier_output,
    set_field,
)


@never_raise(default=None)
def metadata(case_root: Path, identity: dict, inventory: dict) -> dict | None:
    out = init_tier_output("A")
    xyz_path = identity.get("xyz_path")
    q_path = identity.get("q_path")
    if not xyz_path:
        add_warning(out, "no xyz_path in identity")
        return out

    try:
        import vtk
        from sim_parse.adapters.vtk_io import iterate_named_blocks
    except ImportError as e:
        add_warning(out, f"VTK unavailable: {e}")
        return out

    # Try Romtek first; pass both files when available, just xyz otherwise.
    from sim_parse.adapters.vtk_io import load_via_romtek
    paths = [xyz_path, q_path] if q_path else [xyz_path]
    output = load_via_romtek(paths, "Plot3DReader")

    if output is None:
        reader = _make_reader(vtk, xyz_path, q_path)
        if reader is None:
            add_warning(out,
                "vtkMultiBlockPLOT3DReader could not parse the .xyz / .q files. "
                "Plot3D format has no header — try setting endianness manually.")
            return out
        output = safe_call(reader.GetOutput, default=None)
        if output is None or output.GetNumberOfBlocks() == 0:
            add_warning(out, "Plot3D reader returned no grid blocks")
            return out

    n_volume_cells = 0
    n_blocks_total_points = 0
    mesh_zones: list[dict] = []
    for i, (path, block) in enumerate(iterate_named_blocks(output)):
        if block is None:
            continue
        n_cells = safe_call(block.GetNumberOfCells, default=0) or 0
        n_points = safe_call(block.GetNumberOfPoints, default=0) or 0
        n_blocks_total_points = max(n_blocks_total_points, n_points)
        zone = _build_zone(block, path[-1] if path else f"grid_{i}",
                           i, n_cells, n_points)
        if zone:
            mesh_zones.append(zone)
            if zone["role"] == "volume":
                n_volume_cells += n_cells

    if mesh_zones:
        set_field(out, "mesh_zones", mesh_zones,
                  FieldProvenance("A",
                      "vtkMultiBlockPLOT3DReader block walk; structured grids "
                      "treated as volume zones (3D hexahedral)"))
        set_field(out, "mesh_cells", n_volume_cells,
                  FieldProvenance("A", "sum of n_cells over grid blocks"))
        if n_blocks_total_points > 0:
            set_field(out, "mesh_points", n_blocks_total_points,
                      FieldProvenance("A", "max n_points over grid blocks"))

    return out


def _make_reader(vtk_module, xyz: str, q: str | None):
    """Configure the reader with conservative defaults."""
    try:
        r = vtk_module.vtkMultiBlockPLOT3DReader()
    except AttributeError:
        return None
    r.SetXYZFileName(str(xyz))
    if q:
        r.SetQFileName(str(q))
    # Common defaults: binary, multi-grid, no iblanking
    safe_call(r.SetBinaryFile, 1, default=None)
    safe_call(r.SetMultiGrid, 1, default=None)
    safe_call(r.SetByteOrderToLittleEndian, default=None)
    safe_call(r.SetIBlanking, 0, default=None)
    safe_call(r.SetDoublePrecision, 0, default=None)
    safe_call(r.Update, default=None)
    return r


_VTK_3D_CELL_TYPES = {10, 11, 12, 13, 14, 42}  # incl. vtkVoxel (11)


def _build_zone(block, name: str, idx: int, n_cells: int, n_points: int) -> dict | None:
    bbox = None
    try:
        bnds = [0.0] * 6
        block.GetBounds(bnds)
        if all(abs(b) < 1e100 for b in bnds):
            bbox = [float(b) for b in bnds]
    except Exception:
        pass

    # Plot3D structured grids are always volume zones (3D hexahedra)
    return {
        "name": name,
        "role": "volume",
        "n_cells": n_cells if n_cells else None,
        "n_faces": None,
        "n_points": n_points,
        "bounding_box": bbox,
        "element_types": None,
        "patch_type": None,
        "_source": f"vtkMultiBlockPLOT3DReader block_index={idx}",
    }
