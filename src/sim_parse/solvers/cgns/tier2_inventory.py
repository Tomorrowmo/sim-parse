"""CGNS Tier 2 — Inventory.

Lists CGNS bases / zones / variable names via vtkCGNSReader. Modern VTK
(9.x) reads BOTH HDF5-CGNS and ADF-CGNS through the bundled CGNSlib /
CGIO — so we hand the file to the reader regardless of sub_format and
only degrade if the reader actually returns nothing.
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
def inventory(case_root: Path, identity: dict) -> dict | None:
    out = init_tier_output("A")

    file_path = identity.get("file_path")
    sub_format = identity.get("sub_format", "hdf5")
    if not file_path:
        add_warning(out, "no file_path in identity")
        return out

    set_field(out, "cgns_sub_format", sub_format,
              FieldProvenance("A", "from Tier 1"))

    # If Tier 1 saw a multi-file CGNS sequence, surface it cleanly
    if identity.get("sequence_files"):
        set_field(out, "sequence_files", identity["sequence_files"],
                  FieldProvenance("A", "from Tier 1 sequence detection"))
        set_field(out, "n_sequence_files", identity.get("n_sequence_files"),
                  FieldProvenance("A", "from Tier 1"))
        if identity.get("sequence_time_values") is not None:
            tv = identity["sequence_time_values"]
            set_field(out, "time_steps", tv,
                      FieldProvenance("A",
                          "parsed from numeric tokens in filename stems"))
            set_field(out, "n_time_steps", len(tv),
                      FieldProvenance("A", "len(time_steps)"))
            set_field(out, "latest_time", max(tv),
                      FieldProvenance("A", "max(time_steps)"))

    try:
        import vtk
    except ImportError as e:
        add_warning(out, f"VTK unavailable: {e}")
        return out

    reader = safe_call(_make_reader, vtk, file_path, default=None)
    if reader is None:
        add_warning(out, "could not instantiate vtkCGNSReader")
        return out

    # UpdateInformation is enough to enumerate bases/arrays — Update would
    # also load all the data (multi-GB on real cases). Tier 4 does the load.
    safe_call(reader.UpdateInformation, default=None)

    # Enumerate
    n_bases = safe_call(reader.GetNumberOfBaseArrays, default=0) or 0
    n_cell = safe_call(reader.GetNumberOfCellArrays, default=0) or 0
    n_pt = safe_call(reader.GetNumberOfPointArrays, default=0) or 0

    if n_bases == 0 and sub_format == "adf":
        # vtkCGNSReader genuinely couldn't parse this ADF file. This shouldn't
        # happen on VTK 9.x with default-built CGIO, but older VTK builds or
        # custom builds with CGIO ADF support disabled may land here.
        adf_path = Path(file_path)
        suggested_out = adf_path.with_name(adf_path.stem + ".hdf5.cgns")
        vtk_ver = safe_call(vtk.vtkVersion.GetVTKVersion, default="?")
        add_warning(out,
            f"vtkCGNSReader (VTK {vtk_ver}) returned 0 bases on this "
            f"ADF-format file — your VTK build may lack CGIO ADF support. "
            f"Convert with: "
            f'cgnsconvert -h "{adf_path}" "{suggested_out}"  '
            f"— sim-parse auto-detects HDF5 siblings on future calls.")
        return out
    bases = [reader.GetBaseArrayName(i) for i in range(n_bases)]
    cell_arrays = [reader.GetCellArrayName(i) for i in range(n_cell)]
    pt_arrays = [reader.GetPointArrayName(i) for i in range(n_pt)]

    if bases:
        set_field(out, "cgns_bases", bases,
                  FieldProvenance("A", "vtkCGNSReader.GetBaseArrayName"))
    # variables = union of cell + point arrays. Dedupe with order preserved.
    variables = list(dict.fromkeys(list(cell_arrays) + list(pt_arrays)))
    set_field(out, "variables", variables,
              FieldProvenance("A",
                  "vtkCGNSReader cell+point arrays (union, deduped)"))

    # Variables_meta: split by cell vs point scope
    vmeta: dict[str, dict] = {}
    cell_set = set(cell_arrays)
    pt_set = set(pt_arrays)
    for name in variables:
        scope = "cell" if name in cell_set else "point"
        vmeta[name] = {
            "kind": "cell_field",
            "evidence": f"vtkCGNSReader exposes '{name}' as a {scope}-data array",
            "cgns_data_scope": scope,
        }
    if vmeta:
        set_field(out, "variables_meta", vmeta,
                  FieldProvenance("A", "scope from cell-array vs point-array list"))

    return out


def _make_reader(vtk_module, file_path: str):
    r = vtk_module.vtkCGNSReader()
    r.SetFileName(str(file_path))
    return r
