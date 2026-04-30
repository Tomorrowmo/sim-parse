"""Plot3D Tier 4 — Field statistics."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from sim_parse.adapters.vtk_io import iterate_named_blocks, require_vtk
from sim_parse.core.diagnostics import (
    array_rank,
    detect_nonfinite_fields,
    verify_variable_kinds,
)
from sim_parse.core.errors import never_raise, safe_call
from sim_parse.core.provenance import (
    FieldProvenance,
    add_warning,
    init_tier_output,
    set_field,
)


@never_raise(default=None)
def field_stats(
    case_root: Path,
    identity: dict,
    metadata: dict,
    *,
    time: float | None = None,
    fields: list[str] | None = None,
    inventory: dict | None = None,
) -> dict | None:
    out = init_tier_output("A")
    xyz_path = identity.get("xyz_path")
    q_path = identity.get("q_path")
    if not xyz_path:
        add_warning(out, "no xyz_path in identity")
        return out
    if not q_path:
        add_warning(out, "no .q solution file paired with .xyz; mesh-only Plot3D")
        return out

    try:
        vtk, dsa = require_vtk()
    except RuntimeError as e:
        add_warning(out, f"VTK unavailable: {e}")
        return out

    # Try Romtek first
    from sim_parse.adapters.vtk_io import load_via_romtek
    output = load_via_romtek([xyz_path, q_path], "Plot3DReader")

    if output is None:
        # Fallback: standard vtkMultiBlockPLOT3DReader with derived quantities
        try:
            r = vtk.vtkMultiBlockPLOT3DReader()
        except AttributeError:
            add_warning(out, "VTK build missing vtkMultiBlockPLOT3DReader")
            return out

        r.SetXYZFileName(str(xyz_path))
        r.SetQFileName(str(q_path))
        safe_call(r.SetBinaryFile, 1, default=None)
        safe_call(r.SetMultiGrid, 1, default=None)
        safe_call(r.SetByteOrderToLittleEndian, default=None)
        safe_call(r.SetIBlanking, 0, default=None)
        # Enable derived quantities (PRESSURE / TEMPERATURE / MACH / ...)
        # via vtkMultiBlockPLOT3DReader's function-array API
        for fn_id in [110, 111, 113, 120, 130, 140, 144, 153, 163, 170, 184]:
            safe_call(r.AddFunction, fn_id, default=None)
        safe_call(r.Update, default=None)
        output = r.GetOutput()
        if output is None:
            add_warning(out, "Plot3D reader returned no output")
            return out

    variable_ranges: dict[str, dict] = {}
    rank_by_name: dict[str, str] = {}
    bbox_x_all, bbox_y_all, bbox_z_all = [], [], []
    available: set[str] = set()

    for _path, block in iterate_named_blocks(output):
        if block is None:
            continue
        wrapped = dsa.WrapDataObject(block)
        for source_kind, attr in (("cell", block.GetCellData),
                                  ("point", block.GetPointData)):
            data = attr()
            if data is None:
                continue
            for j in range(data.GetNumberOfArrays()):
                name = data.GetArrayName(j)
                if not name:
                    continue
                available.add(name)
                if name in variable_ranges:
                    continue
                container = wrapped.CellData if source_kind == "cell" else wrapped.PointData
                arr = container[name]
                if arr is None:
                    continue
                arr_np = np.asarray(arr)
                if arr_np.size == 0:
                    continue
                stats = _compute_stats(arr_np)
                stats["data_scope"] = source_kind
                variable_ranges[name] = stats
                rank_by_name[name] = array_rank(arr_np)
        try:
            pts = np.asarray(wrapped.Points)
            if pts.size > 0:
                bbox_x_all.append((pts[:, 0].min(), pts[:, 0].max()))
                bbox_y_all.append((pts[:, 1].min(), pts[:, 1].max()))
                bbox_z_all.append((pts[:, 2].min(), pts[:, 2].max()))
        except Exception:
            pass

    if variable_ranges:
        set_field(out, "variable_ranges", variable_ranges,
                  FieldProvenance("A", "vtkMultiBlockPLOT3DReader cell+point arrays + AddFunction derived"))
        nan_fields = detect_nonfinite_fields(variable_ranges)
        set_field(out, "has_nan_field", bool(nan_fields),
                  FieldProvenance("A", "non-finite stat scan"))
        set_field(out, "nan_fields", sorted(nan_fields),
                  FieldProvenance("A", "names with non-finite stats"))
        if nan_fields:
            add_warning(out,
                f"{len(nan_fields)} variable(s) contain NaN/Inf: {sorted(nan_fields)}.")

    if bbox_x_all:
        set_field(out, "bounding_box", {
            "x": [float(min(p[0] for p in bbox_x_all)), float(max(p[1] for p in bbox_x_all))],
            "y": [float(min(p[0] for p in bbox_y_all)), float(max(p[1] for p in bbox_y_all))],
            "z": [float(min(p[0] for p in bbox_z_all)), float(max(p[1] for p in bbox_z_all))],
        }, FieldProvenance("A", "min/max over per-block points"))

    tier2_meta = (inventory or {}).get("variables_meta") if isinstance(inventory, dict) else None
    if rank_by_name:
        verified = verify_variable_kinds(
            tier2_meta=tier2_meta,
            vtk_cell_arrays=sorted(available),
            rank_by_name=rank_by_name,
        )
        set_field(out, "variables_kind_verified", verified,
                  FieldProvenance("A", "vtkMultiBlockPLOT3DReader rank verification"))

    set_field(out, "available_variables_count", len(available),
              FieldProvenance("A", "Plot3D reader cell+point arrays"))
    set_field(out, "exported_variable_count", len(variable_ranges),
              FieldProvenance("A", "len(variable_ranges)"))

    return out


def _compute_stats(arr: np.ndarray) -> dict:
    out: dict = {}
    if arr.ndim == 1:
        out["min"] = float(arr.min())
        out["max"] = float(arr.max())
        out["mean"] = float(arr.mean())
    elif arr.ndim == 2 and arr.shape[1] == 3:
        mag = np.linalg.norm(arr, axis=1)
        out["magnitude_min"] = float(mag.min())
        out["magnitude_max"] = float(mag.max())
        out["magnitude_mean"] = float(mag.mean())
        out["component_min"] = [float(arr[:, i].min()) for i in range(3)]
        out["component_max"] = [float(arr[:, i].max()) for i in range(3)]
    else:
        out["shape"] = list(arr.shape)
    return out
