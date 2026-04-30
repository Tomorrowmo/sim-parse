"""EnSight Gold Tier 3 — Metadata (mesh + zones)."""
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

    case_path = identity.get("case_path")
    if not case_path:
        add_warning(out, "no case_path in identity")
        return out

    # Carry forward the format type (ensight gold)
    set_field(out, "ensight_format", "ensight_gold",
              FieldProvenance("A", "from Tier 1"))

    # Cross-forward time info (Tier 2 already collected time_set).
    if inventory.get("time_set"):
        set_field(out, "time_set", inventory["time_set"],
                  FieldProvenance("A", "from Tier 2"))

    # Use VTK's vtkEnSightGoldReader to count cells/points/zones.
    # If VTK isn't installed, leave counts unset and warn — Tier 4 will
    # warn similarly. Path B fallback in cascade still has a generic
    # listing.
    try:
        import vtk
        from sim_parse.adapters.vtk_io import iterate_named_blocks
    except ImportError as e:
        add_warning(out, f"VTK unavailable, no mesh metadata: {e}")
        return out

    # Try Romtek first; fall back to standard vtkEnSightGoldReader / Binary.
    from sim_parse.adapters.vtk_io import load_via_romtek
    output = load_via_romtek([case_path], "EnsightReader")

    if output is None:
        reader = safe_call(_make_ensight_reader, vtk, case_path, default=None)
        if reader is None:
            add_warning(out, "could not instantiate vtkEnSightGoldReader / BinaryReader")
            return out
        safe_call(reader.Update, default=None)
        output = safe_call(reader.GetOutput, default=None)
        if output is None:
            add_warning(out, "EnSight reader returned no output")
            return out

    # Walk blocks: each block is a "part" (zone in EnSight terminology).
    # Volume vs boundary classified by 3D vs 2D cell types.
    n_volume_cells = 0
    mesh_zones: list[dict] = []
    n_blocks_total_points = 0

    for i, (path, block) in enumerate(iterate_named_blocks(output)):
        if block is None:
            continue
        n_cells = safe_call(block.GetNumberOfCells, default=0) or 0
        n_points = safe_call(block.GetNumberOfPoints, default=0) or 0
        n_blocks_total_points = max(n_blocks_total_points, n_points)

        zone = _build_ensight_zone(block, path, i, n_cells, n_points)
        if zone:
            mesh_zones.append(zone)
            if zone["role"] == "volume":
                n_volume_cells += n_cells

    if mesh_zones:
        set_field(out, "mesh_zones", mesh_zones,
                  FieldProvenance("A",
                      "vtkEnSightGoldReader block walk: per-part role from "
                      "cell-type (3D→volume, 2D→boundary)"))
        set_field(out, "mesh_cells", n_volume_cells,
                  FieldProvenance("A", "sum of n_cells over volume zones"))
        if n_blocks_total_points > 0:
            set_field(out, "mesh_points", n_blocks_total_points,
                      FieldProvenance("A", "max n_points over blocks (EnSight reader can share points)"))

    return out


def _make_ensight_reader(vtk_module, case_path: str):
    """Pick the right EnSight reader by trying ASCII first, then Binary.

    vtkEnSightGoldReader handles ASCII; vtkEnSightGoldBinaryReader handles
    binary. They do NOT auto-detect each other — calling the wrong one on
    a mismatched case yields 0 blocks (silently). We probe by trying ASCII
    first and falling through to Binary if it produces empty output.
    """
    candidates = []
    try:
        candidates.append(("ascii", vtk_module.vtkEnSightGoldReader()))
    except AttributeError:
        pass
    try:
        candidates.append(("binary", vtk_module.vtkEnSightGoldBinaryReader()))
    except AttributeError:
        pass

    for kind, r in candidates:
        try:
            r.SetCaseFileName(str(case_path))
            r.Update()
            out = r.GetOutput()
            if out is not None and out.GetNumberOfBlocks() > 0:
                return r
        except Exception:
            continue
    return None


# Same VTK type-id sets used in OpenFOAM / Fluent — kept in sync deliberately.
_VTK_3D_CELL_TYPES = {10, 11, 12, 13, 14, 42}  # Tetra, Voxel, Hex, Wedge, Pyramid, Polyhedron
_VTK_2D_CELL_TYPES = {5, 7, 9}


def _build_ensight_zone(block, path: tuple, block_index: int,
                        n_cells: int, n_points: int) -> dict | None:
    """Build one mesh_zones entry from a vtkEnSight block."""
    if block is None:
        return None
    name = path[-1] if path and path[-1] else f"part_{block_index}"

    bbox = None
    try:
        bnds = [0.0] * 6
        block.GetBounds(bnds)
        if all(abs(b) < 1e100 for b in bnds):
            bbox = [float(b) for b in bnds]
    except Exception:
        pass

    # Sniff cell types to infer role
    has_3d = False
    has_2d = False
    try:
        n = block.GetNumberOfCells()
        limit = min(n, 50_000)
        for i in range(limit):
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
        n_cells_field, n_faces_field = n_cells, None
    elif has_2d:
        role = "boundary"
        n_cells_field, n_faces_field = None, n_cells
    else:
        role = "unknown"
        n_cells_field, n_faces_field = n_cells if n_cells else None, None

    return {
        "name": name,
        "role": role,
        "n_cells": n_cells_field,
        "n_faces": n_faces_field,
        "n_points": n_points,
        "bounding_box": bbox,
        "element_types": None,    # detailed histogram not yet computed for ensight
        "patch_type": None,
        "_source": f"vtkEnSightGoldReader block_index={block_index}",
        "block_path": list(path),
    }
