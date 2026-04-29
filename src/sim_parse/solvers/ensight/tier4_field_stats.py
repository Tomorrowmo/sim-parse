"""EnSight Gold Tier 4 — Field statistics.

vtkEnSightGoldReader exposes both cell-data and point-data arrays. We
compute stats for both, but report them in `variable_ranges` keyed by
variable name with a `data_scope: cell | point` tag.

EnSight has no convergence log, so #4 (convergence_*) is left empty —
honest "data not available" rather than fake.
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

    case_path = identity.get("case_path")
    if not case_path:
        add_warning(out, "no case_path in identity")
        return out

    try:
        vtk, dsa = require_vtk()
    except RuntimeError as e:
        add_warning(out, f"VTK unavailable: {e}")
        return out

    reader = safe_call(_make_ensight_reader, vtk, case_path, default=None)
    if reader is None:
        add_warning(out, "could not instantiate vtkEnSightGoldReader")
        return out

    safe_call(reader.Update, default=None)
    output = reader.GetOutput()
    if output is None:
        add_warning(out, "EnSight reader returned no output")
        return out

    variable_ranges: dict[str, dict] = {}
    rank_by_name: dict[str, str] = {}
    bbox_x_all, bbox_y_all, bbox_z_all = [], [], []
    available: set[str] = set()

    # Iterate blocks; for each block, walk both CellData and PointData.
    for path_tuple, block in iterate_named_blocks(output):
        if block is None:
            continue
        wrapped = dsa.WrapDataObject(block)

        # Cell data
        try:
            cd = block.GetCellData()
            if cd is not None:
                for j in range(cd.GetNumberOfArrays()):
                    name = cd.GetArrayName(j)
                    if not name:
                        continue
                    available.add(name)
                    if name in variable_ranges:
                        continue
                    arr = wrapped.CellData[name]
                    arr_np = np.asarray(arr)
                    if arr_np.size == 0:
                        continue
                    stats = _compute_stats(arr_np)
                    stats["data_scope"] = "cell"
                    variable_ranges[name] = stats
                    rank_by_name[name] = array_rank(arr_np)
        except Exception:
            pass

        # Point data — only added if not already seen as cell data
        try:
            pd = block.GetPointData()
            if pd is not None:
                for j in range(pd.GetNumberOfArrays()):
                    name = pd.GetArrayName(j)
                    if not name:
                        continue
                    available.add(name)
                    if name in variable_ranges:
                        continue
                    arr = wrapped.PointData[name]
                    arr_np = np.asarray(arr)
                    if arr_np.size == 0:
                        continue
                    stats = _compute_stats(arr_np)
                    stats["data_scope"] = "point"
                    variable_ranges[name] = stats
                    rank_by_name[name] = array_rank(arr_np)
        except Exception:
            pass

        # Aggregate bounding box
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
                  FieldProvenance("A", "vtkEnSightGoldReader cell+point data per block"))
        # Mechanism #1 — NaN/Inf surveillance
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

    # Mechanism #1 — verified kinds (Tier 2 already gave us a kind hint
    # via inventory.variables_meta from the .case parser)
    tier2_meta = (inventory or {}).get("variables_meta") if isinstance(inventory, dict) else None
    if rank_by_name:
        verified = verify_variable_kinds(
            tier2_meta=tier2_meta,
            vtk_cell_arrays=sorted(available),
            rank_by_name=rank_by_name,
        )
        set_field(out, "variables_kind_verified", verified,
                  FieldProvenance("A",
                      "vtkEnSightGoldReader: GetCellArrayName / GetPointArrayName + array rank"))

    set_field(out, "available_variables_count", len(available),
              FieldProvenance("A", "union over blocks' cell+point arrays"))
    set_field(out, "exported_variable_count", len(variable_ranges),
              FieldProvenance("A", "len(variable_ranges)"))

    return out


def _make_ensight_reader(vtk_module, case_path: str):
    """Same picker logic as Tier 3: ASCII probe, then Binary fallback."""
    candidates = []
    try:
        candidates.append(vtk_module.vtkEnSightGoldReader())
    except AttributeError:
        pass
    try:
        candidates.append(vtk_module.vtkEnSightGoldBinaryReader())
    except AttributeError:
        pass

    for r in candidates:
        try:
            r.SetCaseFileName(str(case_path))
            r.Update()
            out = r.GetOutput()
            if out is not None and out.GetNumberOfBlocks() > 0:
                return r
        except Exception:
            continue
    return None


def _compute_stats(arr: np.ndarray) -> dict:
    """Same shape contract as openfoam/fluent: scalars give min/max/mean,
    vectors give magnitude_* + component_*, tensors just shape."""
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
