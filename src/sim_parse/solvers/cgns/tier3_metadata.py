"""CGNS Tier 3 — Metadata (mesh_zones via vtkCGNSReader)."""
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
    sub_format = identity.get("sub_format", "hdf5")
    if not file_path:
        add_warning(out, "no file_path in identity")
        return out

    set_field(out, "cgns_sub_format", sub_format,
              FieldProvenance("A", "from Tier 1"))

    try:
        import vtk
        from sim_parse.adapters.vtk_io import iterate_named_blocks
    except ImportError as e:
        add_warning(out, f"VTK unavailable: {e}")
        return out

    # Try Romtek first (faster on company-internal env); fall back to standard
    # VTK if Romtek not available / refuses the file.
    from sim_parse.adapters.vtk_io import load_via_romtek
    output = load_via_romtek([file_path], "CGNSReader")
    if output is None:
        # Standard VTK fallback. vtkCGNSReader handles both HDF5 and ADF
        # via CGNSlib bundled with VTK 9.x.
        reader = vtk.vtkCGNSReader()
        reader.SetFileName(str(file_path))
        safe_call(reader.Update, default=None)
        output = safe_call(reader.GetOutput, default=None)
    if output is None or output.GetNumberOfBlocks() == 0:
        if sub_format == "adf":
            add_warning(out,
                "vtkCGNSReader returned no blocks on this ADF-format file "
                "— your VTK build may lack CGIO ADF support. Convert with: "
                f'cgnsconvert -h "{file_path}" "{Path(file_path).with_name(Path(file_path).stem + ".hdf5.cgns")}"')
        else:
            add_warning(out,
                "vtkCGNSReader returned no blocks — file may have "
                "non-standard or empty CGNS structure. Tier 1+2 still "
                "identify the file.")
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

        zone = _build_zone(block, path, i, n_cells, n_points)
        if zone:
            mesh_zones.append(zone)
            if zone["role"] == "volume":
                n_volume_cells += n_cells

    if mesh_zones:
        set_field(out, "mesh_zones", mesh_zones,
                  FieldProvenance("A",
                      "vtkCGNSReader MultiBlock walk: per-zone role from "
                      "cell-type (3D→volume, 2D→boundary)"))
        set_field(out, "mesh_cells", n_volume_cells,
                  FieldProvenance("A", "sum of n_cells over volume zones"))
        if n_blocks_total_points > 0:
            set_field(out, "mesh_points", n_blocks_total_points,
                      FieldProvenance("A", "max n_points over zones"))

    return out


_VTK_3D_CELL_TYPES = {10, 11, 12, 13, 14, 42}  # Tetra, Voxel, Hex, Wedge, Pyramid, Polyhedron
_VTK_2D_CELL_TYPES = {5, 7, 9}


def _build_zone(block, path: tuple, block_index: int,
                n_cells: int, n_points: int) -> dict | None:
    if block is None:
        return None
    name = path[-1] if path and path[-1] else f"zone_{block_index}"

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
        role = "volume"
        nc, nf = n_cells, None
    elif has_2d:
        role = "boundary"
        nc, nf = None, n_cells
    else:
        # CGNS can have 1D / 0D zones (BC patches w/o data) — leave as unknown.
        role = "unknown"
        nc, nf = n_cells if n_cells else None, None

    return {
        "name": name,
        "role": role,
        "n_cells": nc,
        "n_faces": nf,
        "n_points": n_points,
        "bounding_box": bbox,
        "element_types": None,
        "patch_type": None,
        "_source": f"vtkCGNSReader block_index={block_index}",
        "block_path": list(path),
    }
