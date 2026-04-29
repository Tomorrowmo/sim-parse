"""Tecplot Tier 3 — Metadata (mesh counts via VTK if compatible).

When VTK's vtkTecplotReader / vtkTecplotBinaryReader can read the file
(version-dependent), we extract mesh_cells / mesh_points / mesh_zones.
When they can't, Tier 2's header info is the best we can do — Tier 3
returns empty + a clear warning.
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

    file_path = identity.get("file_path")
    if not file_path:
        add_warning(out, "no file_path in identity")
        return out

    set_field(out, "tecplot_sub_format", identity.get("sub_format"),
              FieldProvenance("A", "from Tier 1"))

    # Carry forward zone count from Tier 2 (header-based) so users get
    # SOMETHING even when VTK can't load.
    if inventory.get("n_zones") is not None:
        set_field(out, "tecplot_zones_in_header", inventory["n_zones"],
                  FieldProvenance("A", "from Tier 2 header parse"))

    try:
        import vtk
        from sim_parse.adapters.vtk_io import iterate_named_blocks
    except ImportError as e:
        add_warning(out, f"VTK unavailable, no mesh metadata: {e}")
        return out

    reader, reader_kind = _make_tecplot_reader(vtk, file_path,
                                               identity.get("sub_format", "ascii"))
    if reader is None:
        add_warning(out,
            f"VTK could not read this Tecplot file (sub_format="
            f"{identity.get('sub_format')}). Tier 1+2 header info is still "
            f"available; Tier 3+4 mesh statistics require VTK reader support.")
        return out

    output = safe_call(reader.GetOutput, default=None)
    if output is None:
        add_warning(out, f"vtk{reader_kind}TecplotReader returned no output")
        return out

    set_field(out, "tecplot_reader_used", reader_kind,
              FieldProvenance("A", "which VTK reader actually parsed it"))

    n_volume_cells = 0
    n_blocks_total_points = 0
    mesh_zones: list[dict] = []
    blocks = list(iterate_named_blocks(output))
    if not blocks and output is not None:
        # Single block (not multi)
        n_cells = safe_call(output.GetNumberOfCells, default=0) or 0
        n_points = safe_call(output.GetNumberOfPoints, default=0) or 0
        if n_cells > 0:
            mesh_zones.append(_simple_zone(output, "zone_0", 0, n_cells, n_points))
            n_volume_cells = n_cells
            n_blocks_total_points = n_points
    else:
        for i, (path, block) in enumerate(blocks):
            if block is None:
                continue
            n_cells = safe_call(block.GetNumberOfCells, default=0) or 0
            n_points = safe_call(block.GetNumberOfPoints, default=0) or 0
            n_blocks_total_points = max(n_blocks_total_points, n_points)
            zone = _simple_zone(block, path[-1] if path else f"zone_{i}", i, n_cells, n_points)
            if zone:
                mesh_zones.append(zone)
                if zone["role"] == "volume":
                    n_volume_cells += n_cells

    if mesh_zones:
        set_field(out, "mesh_zones", mesh_zones,
                  FieldProvenance("A", f"vtk{reader_kind}TecplotReader block walk"))
        set_field(out, "mesh_cells", n_volume_cells,
                  FieldProvenance("A", "sum of n_cells over volume zones"))
        if n_blocks_total_points > 0:
            set_field(out, "mesh_points", n_blocks_total_points,
                      FieldProvenance("A", "max n_points over blocks"))

    return out


def _make_tecplot_reader(vtk_module, file_path: str, sub_format: str):
    """Try a Tecplot reader; return (reader, kind_str) or (None, None).

    Safety: vtkTecplotBinaryReader (vtkRomtekIODriver) SEGFAULTs on
    multiple Tecplot binary versions in the VTK builds we've tested
    (TDV75 big-endian, TDV111+ from Tecplot 360 EX). Calling it can
    take down the entire process — fatal for an MCP server. The honest
    move: refuse to attempt it. Tier 1+2 already give sub_format +
    variable names + zone count from the pure-Python header parser,
    which is enough metadata for most chat-style questions.

    For ASCII Tecplot files, vtkTecplotReader is reasonably stable
    across versions and IS attempted.
    """
    if sub_format != "ascii":
        # Binary path: do NOT call vtkTecplotBinaryReader.
        return None, None

    try_order = [("Ascii", "vtkTecplotReader")]

    for kind_str, klass_name in try_order:
        klass = getattr(vtk_module, klass_name, None)
        if klass is None:
            continue
        try:
            r = klass()
            r.SetFileName(str(file_path))
            r.Update()
            out = r.GetOutput()
            if out is None:
                continue
            if hasattr(out, "GetNumberOfBlocks"):
                if out.GetNumberOfBlocks() > 0:
                    return r, kind_str
            else:
                if out.GetNumberOfCells() > 0 or out.GetNumberOfPoints() > 0:
                    return r, kind_str
        except Exception:
            continue
    return None, None


_VTK_3D_CELL_TYPES = {10, 12, 13, 14, 42}
_VTK_2D_CELL_TYPES = {5, 7, 9}


def _simple_zone(block, name: str, idx: int, n_cells: int, n_points: int) -> dict | None:
    """Build a mesh_zones entry from a generic VTK dataset."""
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
        "_source": f"vtk Tecplot reader block_index={idx}",
    }
