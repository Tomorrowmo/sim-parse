"""VTK XML Tier 2 — Inventory."""
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
    reader_class = identity.get("reader_class")
    if not file_path or not reader_class:
        add_warning(out, "missing file_path/reader_class in identity")
        return out

    set_field(out, "vtk_xml_sub_format", identity.get("sub_format"),
              FieldProvenance("A", "from Tier 1"))

    try:
        import vtk
    except ImportError as e:
        add_warning(out, f"VTK unavailable: {e}")
        return out

    klass = getattr(vtk, reader_class, None)
    if klass is None:
        add_warning(out, f"VTK build does not include {reader_class}")
        return out

    reader = klass()
    reader.SetFileName(str(file_path))
    safe_call(reader.UpdateInformation, default=None)

    n_cell = safe_call(reader.GetNumberOfCellArrays, default=0) or 0
    n_pt = safe_call(reader.GetNumberOfPointArrays, default=0) or 0
    cell_arrays = [reader.GetCellArrayName(i) for i in range(n_cell) if reader.GetCellArrayName(i)]
    pt_arrays = [reader.GetPointArrayName(i) for i in range(n_pt) if reader.GetPointArrayName(i)]

    # MultiBlock readers (.vtm / .pvt*) often DON'T aggregate cell/point
    # array names at the top level — they're declared per sub-block. If
    # the top-level enumeration is empty AND we're reading a multi-block,
    # walk the loaded output and harvest array names from sub-blocks.
    # This is unavoidably slower (forces an Update) but the alternative
    # is reporting 0 variables when 50+ are sitting in the data.
    if not cell_arrays and not pt_arrays:
        from sim_parse.adapters.vtk_io import iterate_named_blocks
        safe_call(reader.Update, default=None)
        output = safe_call(reader.GetOutput, default=None)
        if output is not None and hasattr(output, "GetNumberOfBlocks"):
            cell_set: set[str] = set()
            pt_set: set[str] = set()
            n_walked = 0
            for _path, block in iterate_named_blocks(output):
                if block is None:
                    continue
                if block.GetNumberOfCells() == 0 and block.GetNumberOfPoints() == 0:
                    continue
                n_walked += 1
                cd = block.GetCellData()
                pd = block.GetPointData()
                if cd is not None:
                    for j in range(cd.GetNumberOfArrays()):
                        nm = cd.GetArrayName(j)
                        if nm:
                            cell_set.add(nm)
                if pd is not None:
                    for j in range(pd.GetNumberOfArrays()):
                        nm = pd.GetArrayName(j)
                        if nm:
                            pt_set.add(nm)
                # Stop after finding arrays — MPI partitions share schema,
                # walking 5 non-empty blocks is plenty to enumerate.
                if (cell_set or pt_set) and n_walked >= 5:
                    break
            cell_arrays = sorted(cell_set)
            pt_arrays = sorted(pt_set)

    variables = list(dict.fromkeys(list(cell_arrays) + list(pt_arrays)))
    set_field(out, "variables", variables,
              FieldProvenance("A", "VTK XML reader cell+point arrays"))

    if variables:
        cell_set = set(cell_arrays)
        vmeta = {
            n: {"kind": "cell_field",
                "evidence": f"VTK XML reader exposes '{n}' as a "
                            f"{'cell' if n in cell_set else 'point'}-data array",
                "data_scope": "cell" if n in cell_set else "point"}
            for n in variables
        }
        set_field(out, "variables_meta", vmeta,
                  FieldProvenance("A", "scope from cell vs point array list"))

    return out
