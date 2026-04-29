"""Tecplot Tier 4 — Field statistics (when VTK reader compatible).

If a Tecplot reader version-incompatible (e.g. TDV112), Tier 4 produces
empty `variable_ranges` plus a clear warning — it does NOT silently
return zeros. Tier 1+2 header parse still gave variable names.
"""
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

    file_path = identity.get("file_path")
    if not file_path:
        add_warning(out, "no file_path in identity")
        return out

    try:
        vtk, dsa = require_vtk()
    except RuntimeError as e:
        add_warning(out, f"VTK unavailable: {e}")
        return out

    sub_format = identity.get("sub_format", "ascii")
    reader, reader_kind = _make_tecplot_reader(vtk, file_path, sub_format)
    if reader is None:
        add_warning(out,
            f"VTK could not read this Tecplot file (sub_format={sub_format}). "
            f"variable_ranges left empty. Tier 1+2 still have file metadata "
            f"and variable name list from the header parse.")
        return out

    output = reader.GetOutput()
    if output is None:
        add_warning(out, f"vtk{reader_kind}TecplotReader returned no output")
        return out

    variable_ranges: dict[str, dict] = {}
    rank_by_name: dict[str, str] = {}
    bbox_x_all, bbox_y_all, bbox_z_all = [], [], []
    available: set[str] = set()

    def _process_block(block):
        if block is None:
            return
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

    blocks = list(iterate_named_blocks(output))
    if not blocks:
        _process_block(output)
    else:
        for _path, block in blocks:
            _process_block(block)

    if variable_ranges:
        set_field(out, "variable_ranges", variable_ranges,
                  FieldProvenance("A", f"vtk{reader_kind}TecplotReader cell+point arrays"))
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
                  FieldProvenance("A", f"vtk{reader_kind}TecplotReader rank verification"))

    set_field(out, "available_variables_count", len(available),
              FieldProvenance("A", "union over blocks' cell+point arrays"))
    set_field(out, "exported_variable_count", len(variable_ranges),
              FieldProvenance("A", "len(variable_ranges)"))

    return out


def _make_tecplot_reader(vtk_module, file_path: str, sub_format: str):
    """Mirror Tier 3 — only attempt ASCII reader.

    Same rationale as tier3: vtkTecplotBinaryReader segfaults on multiple
    versions and is unsafe to call from a long-running server.
    """
    if sub_format != "ascii":
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
