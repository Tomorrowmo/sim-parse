"""OpenFOAM Tier 4 — FieldStats (uses VTK).

Reads internal field data via vtkOpenFOAMReader, computes statistics:
    - variable_ranges (per-variable min/max/mean)
    - variables_kind_verified (cross-check Tier 2 classification with VTK)
    - bounding_box (from internal mesh points)
    - residual_history (regex from log files; pure text, no VTK)
    - convergence_orders (computed from residual history)

Coverage: all cell arrays exposed by vtkOpenFOAMReader at the selected time.
This is intentionally NOT a hand-curated subset — VTK already filters
non-cell-defined fields (face fluxes, dictionaries) out, so reading
everything VTK reports is both safe and verifiable. Callers that want to
restrict the set can pass `fields=[...]`.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from sim_parse.adapters.vtk_io import (
    ensure_foam_marker,
    iterate_blocks,
    iterate_named_blocks,
    require_vtk,
)
from sim_parse.core.diagnostics import (
    array_rank,
    classify_convergence_orders,
    detect_nonfinite_fields,
    summarize_convergence_status,
    verify_variable_kinds,
    vtk_cell_type_name,
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
    iterate_times: bool = False,
) -> dict | None:
    case_root = Path(case_root)
    out = init_tier_output("A")

    # Always: residual history (text only, no VTK)
    log_files = _find_log_files(case_root)
    if log_files:
        residuals = _parse_residuals(log_files)
        if residuals:
            set_field(out, "residual_history", residuals,
                      FieldProvenance("A", "log files (regex 'Solving for ...')"))
            orders = {var: _convergence_orders(history)
                      for var, history in residuals.items()}
            orders = {k: v for k, v in orders.items() if v is not None}
            if orders:
                set_field(out, "convergence_orders", orders,
                          FieldProvenance("A", "log10(residual_initial / residual_last)"))
                # Structured per-variable status + a roll-up summary, so
                # downstream consumers can flag non-converged variables
                # without re-implementing the threshold logic.
                cstatus = classify_convergence_orders(orders)
                set_field(out, "convergence_status", cstatus,
                          FieldProvenance("A",
                              "per-variable status from convergence_orders; "
                              "thresholds: diverged<0, marginal in [0,3), converged>=3"))
                summary = summarize_convergence_status(cstatus)
                set_field(out, "convergence_summary", summary,
                          FieldProvenance("A", "roll-up of convergence_status counts + lists"))
                if summary.get("diverged_vars"):
                    add_warning(out,
                        f"{len(summary['diverged_vars'])} variable(s) have residuals "
                        f"that INCREASED during the run (convergence_order < 0): "
                        f"{summary['diverged_vars']}. Treat results with caution.")
                if summary.get("marginal_vars"):
                    add_warning(out,
                        f"{len(summary['marginal_vars'])} variable(s) dropped fewer than "
                        f"3 orders of magnitude (marginal): {summary['marginal_vars']}.")

    # Field stats via VTK
    try:
        vtk, dsa = require_vtk()
    except RuntimeError as e:
        add_warning(out, f"VTK unavailable, skipping field stats: {e}")
        return out

    foam_marker = ensure_foam_marker(case_root)

    reader = vtk.vtkOpenFOAMReader()
    reader.SetFileName(str(foam_marker))
    reader.SetCreateCellToPoint(0)
    reader.UpdateInformation()

    # Available time steps
    times_arr = reader.GetTimeValues()
    available_times = (
        [times_arr.GetValue(i) for i in range(times_arr.GetNumberOfTuples())]
        if times_arr else []
    )
    if not available_times:
        add_warning(out, "vtkOpenFOAMReader exposes no time values")
        return out

    # Choose target time (default: latest)
    if time is None:
        target_time = available_times[-1]
    else:
        # closest available
        target_time = min(available_times, key=lambda t: abs(t - time))
    reader.SetTimeValue(target_time)

    # Time-series mode: keep the same reader (re-uses geometry across times),
    # iterate every available time, populate time_series_data, and return
    # without doing the heavy 'representative-only' work below. This is
    # MUCH cheaper than the CGNS branch because OpenFOAM stores field data
    # in per-time subdirs sharing one polyMesh.
    if iterate_times and len(available_times) >= 2:
        return _iterate_openfoam_times(
            out, reader, dsa, available_times,
            fields=fields,
        )

    # Enable all boundary patches so that mesh_zones geometry enrichment
    # (per-patch bounding box / point count / element type histogram) can
    # see the patch sub-blocks. This does not affect cell-data reads.
    n_patches = safe_call(reader.GetNumberOfPatchArrays, default=0) or 0
    for pi in range(n_patches):
        pname = safe_call(reader.GetPatchArrayName, pi, default=None)
        if pname:
            safe_call(reader.SetPatchArrayStatus, pname, 1, default=None)

    # Available cell arrays — these are the verified-by-VTK cell-defined fields.
    # Anything in Tier 2's `variables` list NOT here is non-cell (face flux,
    # diagnostic, template, IC-only, ...) and will be cross-flagged below.
    n_arrs = reader.GetNumberOfCellArrays()
    available_vars = [reader.GetCellArrayName(i) for i in range(n_arrs)]

    # Choose which variables to actually read.
    # Default: read every cell array VTK reports — this is the verifiable,
    # case-derived set, NOT a hand-picked priority list.
    if fields is None:
        target_vars = list(available_vars)
    else:
        target_vars = [v for v in fields if v in available_vars]

    # Disable all then enable target
    for v in available_vars:
        reader.SetCellArrayStatus(v, 0)
    for v in target_vars:
        reader.SetCellArrayStatus(v, 1)

    reader.Update()

    output = reader.GetOutput()  # vtkMultiBlockDataSet

    # Find the internal mesh block (first non-null block typically)
    internal_block = None
    for block in iterate_blocks(output):
        # internal mesh has CellData with the requested variables
        cd = block.GetCellData()
        if cd is None:
            continue
        if cd.GetNumberOfArrays() > 0:
            internal_block = block
            break

    if internal_block is None:
        add_warning(out, "vtkOpenFOAMReader produced no internal mesh block")
        return out

    # Wrap and compute stats
    wrapped = dsa.WrapDataObject(internal_block)
    cell_data = wrapped.CellData

    variable_ranges: dict = {}
    rank_by_name: dict[str, str] = {}  # name → "scalar" | "vector" | "tensor" | "unknown"
    for v in target_vars:
        arr = safe_call(_array_to_numpy, cell_data, v, default=None)
        if arr is None or arr.size == 0:
            continue
        variable_ranges[v] = _compute_stats(v, arr)
        rank_by_name[v] = array_rank(arr)

    if variable_ranges:
        set_field(out, "variable_ranges", variable_ranges,
                  FieldProvenance("A", f"vtkOpenFOAMReader at time={target_time}"))
        # NaN/Inf surveillance — any non-finite min/max/mean across all
        # variables indicates a numerically broken solution. Surface the
        # boolean + list of offending fields so consumers can flag the
        # case without re-iterating variable_ranges.
        nan_fields = detect_nonfinite_fields(variable_ranges)
        set_field(out, "has_nan_field", bool(nan_fields),
                  FieldProvenance("A",
                      "True iff any variable_ranges entry has a non-finite "
                      "min/max/mean/component value (NaN or +/-Inf)"))
        set_field(out, "nan_fields", sorted(nan_fields),
                  FieldProvenance("A", "names of variables with non-finite stats"))
        if nan_fields:
            add_warning(out,
                f"{len(nan_fields)} variable(s) contain NaN or Inf values: "
                f"{sorted(nan_fields)}. Solution is likely numerically broken.")

    # Cross-check Tier 2 kind classification against what VTK reported.
    # We don't mutate Tier 2's output; we add a `variables_kind_verified` here
    # that downstream consumers can prefer over the Tier 2 heuristic guess.
    tier2_meta = (inventory or {}).get("variables_meta") if isinstance(inventory, dict) else None
    verified = verify_variable_kinds(
        tier2_meta=tier2_meta,
        vtk_cell_arrays=available_vars,
        rank_by_name=rank_by_name,
    )
    if verified:
        set_field(out, "variables_kind_verified", verified,
                  FieldProvenance("A",
                      "VTK-confirmed kind: cross-check Tier 2 heuristic vs "
                      "vtkOpenFOAMReader.GetCellArrayName + array rank"))

    # Bounding box from points
    points = safe_call(lambda: np.asarray(wrapped.Points), default=None)
    if points is not None and points.size > 0:
        bbox = {
            "x": [float(points[:, 0].min()), float(points[:, 0].max())],
            "y": [float(points[:, 1].min()), float(points[:, 1].max())],
            "z": [float(points[:, 2].min()), float(points[:, 2].max())],
        }
        set_field(out, "bounding_box", bbox,
                  FieldProvenance("A", "internal mesh points min/max"))

    set_field(out, "field_stats_time", target_time,
              FieldProvenance("A", "Tier 4 selected time"))
    set_field(out, "available_times_count", len(available_times),
              FieldProvenance("A", "vtkOpenFOAMReader.GetTimeValues"))
    set_field(out, "available_variables_count", len(available_vars),
              FieldProvenance("A", "vtkOpenFOAMReader.GetNumberOfCellArrays"))

    # Per-zone geometry enrichment for the Tier 3 mesh_zones skeleton.
    # We walk the MultiBlock tree by name, collecting bounds / n_points /
    # element-type histogram per named block. Tier 5 / downstream consumers
    # merge this with Tier 3's mesh_zones via the zone name as join key.
    geom = safe_call(_extract_mesh_zones_geometry, output, dsa, default=None)
    if geom:
        set_field(out, "mesh_zones_geometry", geom,
                  FieldProvenance("A",
                      "vtkOpenFOAMReader MultiBlock walk: per-block bounds + "
                      "point-count + cell-type histogram, keyed by block name"))

    return out


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _array_to_numpy(cell_data, name: str):
    """Extract a numpy array from a wrapped vtkDataObject's CellData."""
    arr = cell_data[name]
    return np.asarray(arr)


def _iterate_openfoam_times(out: dict, reader, dsa,
                             available_times: list[float],
                             *, fields: list[str] | None) -> dict:
    """For OpenFOAM cases with multiple time-step subdirs, re-use the same
    reader (which keeps the mesh in memory) and SetTimeValue + Update for
    each time. Cheap compared to opening N readers.
    """
    n_arrs = reader.GetNumberOfCellArrays()
    available_vars = [reader.GetCellArrayName(i) for i in range(n_arrs)]
    wanted = set(fields) if fields else set(available_vars)

    # Disable everything, enable only what user asked for
    for v in available_vars:
        reader.SetCellArrayStatus(v, 1 if v in wanted else 0)

    series: dict[str, list[dict]] = {}

    for t in available_times:
        reader.SetTimeValue(t)
        reader.Update()
        output = reader.GetOutput()
        if output is None:
            continue

        # Find the internal mesh block (first block with cell data)
        internal_block = None
        for block in iterate_blocks(output):
            cd = block.GetCellData()
            if cd is not None and cd.GetNumberOfArrays() > 0:
                internal_block = block
                break
        if internal_block is None:
            continue

        wrapped = dsa.WrapDataObject(internal_block)
        for v in wanted:
            if v not in available_vars:
                continue
            try:
                arr = wrapped.CellData[v]
            except KeyError:
                continue
            if arr is None:
                continue
            arr_np = np.asarray(arr)
            if arr_np.size == 0:
                continue
            stats = _compute_stats(v, arr_np)
            stats["time"] = t
            stats["data_scope"] = "cell"
            series.setdefault(v, []).append(stats)

    if series:
        set_field(out, "time_series_data", series,
                  FieldProvenance("A",
                      f"per-time stats over {len(available_times)} time steps "
                      f"via vtkOpenFOAMReader.SetTimeValue loop"))
        set_field(out, "time_series_times", list(available_times),
                  FieldProvenance("A", "vtkOpenFOAMReader.GetTimeValues"))
        set_field(out, "time_series_n_files", len(available_times),
                  FieldProvenance("A", "len(available_times)"))

    return out


def _extract_mesh_zones_geometry(multi_block, dsa) -> dict[str, dict] | None:
    """Walk vtkOpenFOAMReader's MultiBlock output and produce per-zone geometry.

    Returns a dict keyed by zone name. Each value carries:
        bounding_box: [xmin,xmax,ymin,ymax,zmin,zmax] | None
        n_points:     int                              # vertices in this block
        n_cells:      int                              # cells/faces in this block
        element_types: {vtk_type_name: count}
        block_path:   list[str]                        # full path (root → leaf)

    Block path uses the names vtkOpenFOAMReader assigns:
        ('internalMesh',)              for the volume zone
        ('Patches', '<patchName>')     for boundary zones
        ('lagrangian', '<cloud>')      for lagrangian clouds (skipped here)

    The volume zone reports n_cells = 3D cells; boundary blocks report
    n_cells = 2D faces (since vtk treats faces as cells of a polydata).
    The mesh_zones consumer is expected to know which interpretation
    applies based on the zone's `role` from Tier 3.
    """
    if multi_block is None:
        return None

    out: dict[str, dict] = {}
    for path, block in iterate_named_blocks(multi_block):
        # Skip lagrangian clouds — they're particles, not mesh zones.
        if any(p == "lagrangian" for p in path):
            continue
        # Pick the deepest non-empty name as the zone identifier.
        name = path[-1] if path and path[-1] else "internalMesh"
        # Resolve duplicate names by appending the path.
        if name in out:
            name = "/".join(p for p in path if p) or name

        try:
            n_points = int(block.GetNumberOfPoints())
            n_cells = int(block.GetNumberOfCells())
        except Exception:
            n_points = 0
            n_cells = 0

        bbox = None
        try:
            bnds = [0.0] * 6
            block.GetBounds(bnds)
            # GetBounds returns [-inf, inf, ...] for empty blocks
            if all(abs(b) < 1e100 for b in bnds):
                bbox = [float(b) for b in bnds]
        except Exception:
            pass

        element_types: dict[str, int] = {}
        try:
            n = block.GetNumberOfCells()
            # Cap the histogram pass for very large blocks to avoid O(n) cost
            # in the parse hot path. For meshes >5M cells we sample.
            if n > 0:
                limit = min(n, 200_000)
                for i in range(limit):
                    t = block.GetCellType(i)
                    name_t = vtk_cell_type_name(int(t))
                    element_types[name_t] = element_types.get(name_t, 0) + 1
                # Annotate when sampled
                if limit < n:
                    element_types["_sampled_first_n"] = limit
                    element_types["_total_cells"] = n
        except Exception:
            element_types = {}

        out[name] = {
            "bounding_box": bbox,
            "n_points": n_points,
            "n_cells": n_cells,
            "element_types": element_types,
            "block_path": list(path),
        }

    return out if out else None


def _compute_stats(var_name: str, arr: np.ndarray) -> dict:
    """Compute min/max/mean for a scalar or vector array.

    For vectors: report magnitude stats AND per-component.
    """
    out: dict = {}
    if arr.ndim == 1:
        # scalar
        out["min"] = float(arr.min())
        out["max"] = float(arr.max())
        out["mean"] = float(arr.mean())
    elif arr.ndim == 2 and arr.shape[1] == 3:
        # vector — magnitude stats
        mag = np.linalg.norm(arr, axis=1)
        out["magnitude_min"] = float(mag.min())
        out["magnitude_max"] = float(mag.max())
        out["magnitude_mean"] = float(mag.mean())
        out["component_min"] = [float(arr[:, i].min()) for i in range(3)]
        out["component_max"] = [float(arr[:, i].max()) for i in range(3)]
    else:
        # tensor or unusual — just shape
        out["shape"] = list(arr.shape)
    return out


def _find_log_files(case_root: Path) -> list[Path]:
    """Find all log.* files in the case (top-level + processor*)."""
    found = []
    for f in case_root.iterdir():
        if f.is_file() and f.name.startswith("log."):
            found.append(f)
    for d in case_root.iterdir():
        if d.is_dir() and d.name.startswith("processor"):
            for f in d.iterdir():
                if f.is_file() and f.name.startswith("log."):
                    found.append(f)
    return found


_RES_PATTERN = re.compile(
    r"Solving for\s+(?P<var>\w+).*?"
    r"Initial residual\s*=\s*(?P<initial>[\d.eE+-]+).*?"
    r"Final residual\s*=\s*(?P<final>[\d.eE+-]+)"
)


def _parse_residuals(log_files: list[Path]) -> dict:
    """Parse 'Solving for <var> ... Initial residual = X ...' lines.

    Returns:
        {"<var>": [(initial_residual_at_step, final_residual_at_step), ...]}
    """
    residuals: dict[str, list] = {}
    for lf in log_files:
        try:
            text = lf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _RES_PATTERN.finditer(text):
            var = m.group("var")
            try:
                ini = float(m.group("initial"))
                fin = float(m.group("final"))
            except ValueError:
                continue
            residuals.setdefault(var, []).append([ini, fin])
    return residuals


def _convergence_orders(history: list) -> float | None:
    """Compute log10(initial_first / final_last) — orders of magnitude dropped."""
    if not history or len(history) == 0:
        return None
    initial_first = history[0][0]
    final_last = history[-1][1]
    if initial_first <= 0 or final_last <= 0:
        return None
    return float(np.log10(initial_first / final_last))
