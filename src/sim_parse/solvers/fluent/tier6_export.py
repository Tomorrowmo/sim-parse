"""Fluent Tier 6 — VTU export.

Reads via vtkFLUENTReader/vtkFLUENTCFFReader, writes vtkXMLMultiBlockDataWriter
(vtkMultiBlock for multi-zone) or vtkXMLUnstructuredGridWriter (single-zone).
"""
from __future__ import annotations

from pathlib import Path

from sim_parse.adapters.vtk_io import require_vtk
from sim_parse.core.errors import never_raise, safe_call
from sim_parse.core.provenance import (
    FieldProvenance,
    add_warning,
    init_tier_output,
    set_field,
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
    out = init_tier_output("A")

    cas_path = identity.get("cas_path")
    if not cas_path:
        add_warning(out, "No cas_path in identity")
        return out

    sub_format = identity.get("sub_format", "legacy")

    try:
        vtk, _dsa = require_vtk()
    except RuntimeError as e:
        add_warning(out, f"VTK unavailable: {e}")
        return out

    if sub_format == "cff":
        try:
            from vtkmodules.vtkIOFLUENTCFF import vtkFLUENTCFFReader
            reader = vtkFLUENTCFFReader()
        except ImportError:
            add_warning(out, "vtkFLUENTCFFReader unavailable")
            return out
    else:
        try:
            from vtkmodules.vtkIOGeometry import vtkFLUENTReader
            reader = vtkFLUENTReader()
        except ImportError:
            add_warning(out, "vtkFLUENTReader unavailable")
            return out

    reader.SetFileName(cas_path)
    reader.UpdateInformation()

    n_arrs = reader.GetNumberOfCellArrays()
    available = [reader.GetCellArrayName(i) for i in range(n_arrs)]
    target_vars = available if fields is None else [v for v in fields if v in available]
    for v in available:
        reader.SetCellArrayStatus(v, 0)
    for v in target_vars:
        reader.SetCellArrayStatus(v, 1)

    reader.Update()

    # Output path
    cas = Path(cas_path)
    if output is None:
        cache_dir = cas.parent / ".simparse_cache"
        cache_dir.mkdir(exist_ok=True)
        vtm_path = cache_dir / f"{cas.stem}.vtm"
    else:
        vtm_path = Path(output)
        vtm_path.parent.mkdir(parents=True, exist_ok=True)

    # Use vtkXMLMultiBlockDataWriter for multi-block (Fluent typical)
    writer = vtk.vtkXMLMultiBlockDataWriter()
    writer.SetFileName(str(vtm_path))
    writer.SetInputData(reader.GetOutput())
    writer.SetCompressorTypeToZLib()
    writer.Write()

    set_field(out, "vtu_path", str(vtm_path),
              FieldProvenance("A", "vtkXMLMultiBlockDataWriter"))
    set_field(out, "exported_variables", target_vars,
              FieldProvenance("A", "reader.SetCellArrayStatus"))
    set_field(out, "supported", True,
              FieldProvenance("A", "successful export"))

    return out
