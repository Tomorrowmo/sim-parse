"""VTK XML Tier 3 — Metadata (mesh_zones via reader-specific Update)."""
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

    file_path = identity.get("file_path")
    reader_class = identity.get("reader_class")
    sub_format = identity.get("sub_format", "")
    if not (file_path and reader_class):
        add_warning(out, "missing file_path/reader_class in identity")
        return out

    set_field(out, "vtk_xml_sub_format", sub_format,
              FieldProvenance("A", "from Tier 1"))

    try:
        import vtk
        from sim_parse.adapters.vtk_io import iterate_named_blocks
    except ImportError as e:
        add_warning(out, f"VTK unavailable: {e}")
        return out

    # Try Romtek first; fall back to standard VTK XML reader.
    from sim_parse.adapters.vtk_io import ROMTEK_READER_FOR, load_via_romtek
    sub_format_lower = (sub_format or "").lower()
    romtek_name = ROMTEK_READER_FOR.get(sub_format_lower)
    output = None
    if romtek_name:
        output = load_via_romtek([file_path], romtek_name)

    if output is None:
        klass = getattr(vtk, reader_class, None)
        if klass is None:
            add_warning(out, f"VTK build missing {reader_class}")
            return out
        reader = klass()
        reader.SetFileName(str(file_path))
        safe_call(reader.Update, default=None)
        output = safe_call(reader.GetOutput, default=None)
        if output is None:
            add_warning(out, f"{reader_class} returned no output")
            return out

    mesh_zones: list[dict] = []
    n_volume_cells = 0
    n_blocks_total_points = 0

    if hasattr(output, "GetNumberOfBlocks"):
        # Multi-block (.vtm / .pvtu collection)
        for i, (path, block) in enumerate(iterate_named_blocks(output)):
            if block is None:
                continue
            n_cells = safe_call(block.GetNumberOfCells, default=0) or 0
            n_points = safe_call(block.GetNumberOfPoints, default=0) or 0
            n_blocks_total_points = max(n_blocks_total_points, n_points)
            zone = _build_zone(block, path[-1] if path else f"block_{i}",
                               i, n_cells, n_points)
            if zone:
                mesh_zones.append(zone)
                if zone["role"] == "volume":
                    n_volume_cells += n_cells
    else:
        # Single dataset (.vtu / .vtp / .vti / .vts / .vtr)
        n_cells = safe_call(output.GetNumberOfCells, default=0) or 0
        n_points = safe_call(output.GetNumberOfPoints, default=0) or 0
        n_blocks_total_points = n_points
        zone = _build_zone(output, "dataset", 0, n_cells, n_points)
        if zone:
            mesh_zones.append(zone)
            if zone["role"] == "volume":
                n_volume_cells = n_cells

    if mesh_zones:
        set_field(out, "mesh_zones", mesh_zones,
                  FieldProvenance("A", f"{reader_class} block walk"))
        set_field(out, "mesh_cells", n_volume_cells,
                  FieldProvenance("A", "sum of n_cells over volume zones"))
        if n_blocks_total_points > 0:
            set_field(out, "mesh_points", n_blocks_total_points,
                      FieldProvenance("A", "max n_points over blocks"))

    return out


_VTK_3D_CELL_TYPES = {10, 11, 12, 13, 14, 42}  # Tetra, Voxel, Hex, Wedge, Pyramid, Polyhedron
_VTK_2D_CELL_TYPES = {5, 7, 9}


def _build_zone(block, name: str, idx: int, n_cells: int, n_points: int) -> dict | None:
    bbox = None
    try:
        bnds = [0.0] * 6
        block.GetBounds(bnds)
        if all(abs(b) < 1e100 for b in bnds):
            bbox = [float(b) for b in bnds]
    except Exception:
        pass

    has_3d = False
    has_2d = False
    try:
        n = block.GetNumberOfCells()
        for i in range(min(n, 50_000)):
            t = int(block.GetCellType(i))
            if t in _VTK_3D_CELL_TYPES:
                has_3d = True
            elif t in _VTK_2D_CELL_TYPES:
                has_2d = True
            if has_3d and has_2d:
                break
    except Exception:
        pass

    if has_3d:
        role, nc, nf = "volume", n_cells, None
    elif has_2d:
        role, nc, nf = "boundary", None, n_cells
    else:
        role, nc, nf = "unknown", n_cells if n_cells else None, None

    return {
        "name": name,
        "role": role,
        "n_cells": nc,
        "n_faces": nf,
        "n_points": n_points,
        "bounding_box": bbox,
        "element_types": None,
        "patch_type": None,
        "_source": f"VTK XML reader block_index={idx}",
    }
