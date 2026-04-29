"""OpenFOAM Tier 6 — Full data export to VTU.

Reads the case via vtkOpenFOAMReader at a chosen time + variable subset,
extracts the internal mesh block, writes a `.vtu` file.

Output dict shape:
    {
        "vtu_path": "<absolute path>",
        "exported_time": float,
        "exported_variables": [list],
        "n_points": int,
        "n_cells": int,
        "_path": "A",
    }
"""
from __future__ import annotations

from pathlib import Path

from sim_parse.adapters.vtk_io import ensure_foam_marker, iterate_blocks, require_vtk
from sim_parse.core.errors import never_raise
from sim_parse.core.provenance import (
    FieldProvenance,
    add_warning,
    init_tier_output,
    set_field,
)


_DEFAULT_VARS = (
    "U", "p", "T", "rho", "k", "epsilon", "omega", "nut",
    "Qdot", "OH",
)


@never_raise(default=None)
def export_vtu(
    case_root: Path,
    identity: dict,
    *,
    time: float | None = None,
    fields: list[str] | None = None,
    output: str | Path | None = None,
) -> dict | None:
    """Export OpenFOAM case to a VTU file.

    Args:
        case_root: case directory
        identity: Tier 1 output (used for solver-aware logic)
        time: select this time step (default: latest)
        fields: variable subset (default: common ones from _DEFAULT_VARS that exist)
        output: output VTU path (default: <case>/.simparse_cache/<case_name>_<time>.vtu)

    Returns:
        dict with vtu_path + metadata, or None on failure.
    """
    case_root = Path(case_root)
    out = init_tier_output("A")

    try:
        vtk, dsa = require_vtk()
    except RuntimeError as e:
        add_warning(out, f"VTK unavailable: {e}")
        return out

    foam_marker = ensure_foam_marker(case_root)

    reader = vtk.vtkOpenFOAMReader()
    reader.SetFileName(str(foam_marker))
    reader.SetCreateCellToPoint(0)
    reader.UpdateInformation()

    # Time selection
    times_arr = reader.GetTimeValues()
    available_times = (
        [times_arr.GetValue(i) for i in range(times_arr.GetNumberOfTuples())]
        if times_arr else []
    )
    if not available_times:
        add_warning(out, "No time steps available")
        return out
    target_time = available_times[-1] if time is None else min(
        available_times, key=lambda t: abs(t - time)
    )
    reader.SetTimeValue(target_time)

    # Variable selection
    n_arrs = reader.GetNumberOfCellArrays()
    available_vars = [reader.GetCellArrayName(i) for i in range(n_arrs)]
    if fields is None:
        target_vars = [v for v in _DEFAULT_VARS if v in available_vars]
    else:
        target_vars = [v for v in fields if v in available_vars]
    for v in available_vars:
        reader.SetCellArrayStatus(v, 0)
    for v in target_vars:
        reader.SetCellArrayStatus(v, 1)

    reader.Update()

    # Find internal mesh block
    output_data = reader.GetOutput()
    internal_block = None
    for block in iterate_blocks(output_data):
        if hasattr(block, "GetNumberOfPoints") and block.GetNumberOfPoints() > 0:
            internal_block = block
            break
    if internal_block is None:
        add_warning(out, "vtkOpenFOAMReader produced no usable internal mesh")
        return out

    # Determine output path
    if output is None:
        cache_dir = case_root / ".simparse_cache"
        cache_dir.mkdir(exist_ok=True)
        # Format time without trailing zeros for filename hygiene
        time_str = (str(int(target_time))
                    if target_time == int(target_time) else str(target_time))
        vtu_path = cache_dir / f"{case_root.name}_{time_str}.vtu"
    else:
        vtu_path = Path(output)
        vtu_path.parent.mkdir(parents=True, exist_ok=True)

    # Write VTU
    writer = vtk.vtkXMLUnstructuredGridWriter()
    writer.SetFileName(str(vtu_path))
    writer.SetInputData(internal_block)
    writer.SetCompressorTypeToZLib()
    writer.Write()

    set_field(out, "vtu_path", str(vtu_path),
              FieldProvenance("A", "vtkXMLUnstructuredGridWriter"))
    set_field(out, "exported_time", target_time,
              FieldProvenance("A", "vtkOpenFOAMReader.SetTimeValue"))
    set_field(out, "exported_variables", target_vars,
              FieldProvenance("A", "reader.SetCellArrayStatus enabled"))
    set_field(out, "n_points", int(internal_block.GetNumberOfPoints()),
              FieldProvenance("A", "internal_block.GetNumberOfPoints"))
    set_field(out, "n_cells", int(internal_block.GetNumberOfCells()),
              FieldProvenance("A", "internal_block.GetNumberOfCells"))
    set_field(out, "supported", True,
              FieldProvenance("A", "successful export"))

    return out
