"""CGNS Tier 4 — Field statistics."""
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
    iterate_times: bool = False,
) -> dict | None:
    """Compute Tier 4 stats. By default reads only the representative file
    (latest time). When `iterate_times=True` AND identity carries a
    `sequence_files` list (set by Tier 1 sequence-detection), iterates
    every file in the sequence and stores per-time stats in
    `time_series_data` — answering "how does T_max evolve over time?"
    questions.

    Cost note: iterate_times reads every file fully — multi-GB CGNS
    sequences can take minutes. Pass `fields=[...]` to subset which
    variables to read so the per-file VTK Update only loads what you need.
    """
    out = init_tier_output("A")

    file_path = identity.get("file_path")
    sub_format = identity.get("sub_format", "hdf5")
    if not file_path:
        add_warning(out, "no file_path in identity")
        return out

    try:
        vtk, dsa = require_vtk()
    except RuntimeError as e:
        add_warning(out, f"VTK unavailable: {e}")
        return out

    # Time-series mode: iterate every file in the sequence and accumulate
    # per-time stats. Returns early with time_series_data — the regular
    # representative-only path is bypassed because the user explicitly
    # asked for the evolution view.
    if iterate_times and identity.get("sequence_files"):
        return _iterate_time_series(
            out, identity, vtk, dsa, fields=fields,
        )

    # Try Romtek first — its ReadFiles call loads everything in one shot,
    # so selective array enabling (`fields` arg) is bypassed. Acceptable
    # trade-off: when fields=None (the default), we want all arrays anyway.
    # When fields is specified, we still load all but filter post-hoc — a
    # bit wasteful but avoids forking the code path.
    from sim_parse.adapters.vtk_io import load_via_romtek
    romtek_output = load_via_romtek([file_path], "CGNSReader")

    if romtek_output is not None:
        output = romtek_output
        # Mark which arrays were actually enabled (for verified-kind tracking)
        cell_arrays: list[str] = []
        pt_arrays: list[str] = []
        # Walk the output once to harvest array names from sub-blocks
        for _path_t, _block in iterate_named_blocks(output):
            if _block is None:
                continue
            cd = _block.GetCellData()
            pd = _block.GetPointData()
            if cd is not None:
                for j in range(cd.GetNumberOfArrays()):
                    nm = cd.GetArrayName(j)
                    if nm and nm not in cell_arrays:
                        cell_arrays.append(nm)
            if pd is not None:
                for j in range(pd.GetNumberOfArrays()):
                    nm = pd.GetArrayName(j)
                    if nm and nm not in pt_arrays:
                        pt_arrays.append(nm)
            if cell_arrays or pt_arrays:
                # Stop after first non-empty block — schema repeats across
                # MPI-style blocks
                break
    else:
        # Fallback: standard vtkCGNSReader. Handles both HDF5 and ADF via
        # bundled CGNSlib in VTK 9.x.
        reader = vtk.vtkCGNSReader()
        reader.SetFileName(str(file_path))
        safe_call(reader.Update, default=None)

        n_cell = safe_call(reader.GetNumberOfCellArrays, default=0) or 0
        n_pt = safe_call(reader.GetNumberOfPointArrays, default=0) or 0
        if n_cell == 0 and n_pt == 0 and sub_format == "adf":
            add_warning(out,
                "vtkCGNSReader exposed no cell/point arrays on this ADF file "
                "— your VTK build may lack CGIO ADF support. Convert with: "
                f'cgnsconvert -h "{file_path}" "{Path(file_path).with_name(Path(file_path).stem + ".hdf5.cgns")}"')
            return out
        cell_arrays = [reader.GetCellArrayName(i) for i in range(n_cell)]
        pt_arrays = [reader.GetPointArrayName(i) for i in range(n_pt)]

        # Enable arrays selectively (Romtek path doesn't support this — it
        # already loaded everything). Standard VTK reader can save load time
        # by only enabling wanted arrays.
        if fields is None:
            for v in cell_arrays:
                safe_call(reader.SetCellArrayStatus, v, 1, default=None)
            for v in pt_arrays:
                safe_call(reader.SetPointArrayStatus, v, 1, default=None)
        else:
            wanted = set(fields)
            for v in cell_arrays:
                safe_call(reader.SetCellArrayStatus, v, 1 if v in wanted else 0,
                          default=None)
            for v in pt_arrays:
                safe_call(reader.SetPointArrayStatus, v, 1 if v in wanted else 0,
                          default=None)

        safe_call(reader.Update, default=None)
        output = reader.GetOutput()
        if output is None:
            add_warning(out, "vtkCGNSReader returned no output")
            return out

    variable_ranges: dict[str, dict] = {}
    rank_by_name: dict[str, str] = {}
    bbox_x_all, bbox_y_all, bbox_z_all = [], [], []
    available: set[str] = set()

    for path_tuple, block in iterate_named_blocks(output):
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
                  FieldProvenance("A", "vtkCGNSReader cell+point arrays"))
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
                  FieldProvenance("A", "vtkCGNSReader rank verification"))

    set_field(out, "available_variables_count", len(available),
              FieldProvenance("A", "len(cell_arrays ∪ point_arrays)"))
    set_field(out, "exported_variable_count", len(variable_ranges),
              FieldProvenance("A", "len(variable_ranges)"))

    return out


def _iterate_time_series(out: dict, identity: dict, vtk, dsa,
                         *, fields: list[str] | None) -> dict:
    """Loop over sequence_files, compute stats for `fields` per file, store
    result in tier_4.time_series_data. Each file is opened with a fresh
    vtkCGNSReader (CGNS sequence files are independent — no shared state).

    Output schema (consumed by qoi_engine for temporal_requirement rules):
        time_series_data: {
            "T":  [{"time": 0.003, "min": ..., "max": ..., "mean": ...,
                    "data_scope": "cell" | "point"}, ...],
            "p":  [...],
            ...
        }
        time_series_files: [str, ...]   # which files were read
        time_series_times: [float, ...] # parsed time values (if available)
    """
    files = [Path(p) for p in identity.get("sequence_files") or []]
    time_values = identity.get("sequence_time_values")
    n_files = len(files)

    if n_files == 0:
        add_warning(out, "iterate_times=True but no sequence_files in identity")
        return out

    # Build time_series_data progressively. Each variable maps to a list of
    # per-file records.
    series: dict[str, list[dict]] = {}

    for idx, fp in enumerate(files):
        time_v = time_values[idx] if time_values and idx < len(time_values) else None
        reader = vtk.vtkCGNSReader()
        reader.SetFileName(str(fp))
        safe_call(reader.Update, default=None)

        # Enable wanted variables only (cuts VTK Update cost dramatically
        # on big files when user asks for just T/p/U)
        n_cell = safe_call(reader.GetNumberOfCellArrays, default=0) or 0
        n_pt = safe_call(reader.GetNumberOfPointArrays, default=0) or 0
        cell_arrays = [reader.GetCellArrayName(i) for i in range(n_cell)]
        pt_arrays = [reader.GetPointArrayName(i) for i in range(n_pt)]

        wanted = set(fields) if fields else None
        if wanted:
            for v in cell_arrays:
                safe_call(reader.SetCellArrayStatus, v, 1 if v in wanted else 0,
                          default=None)
            for v in pt_arrays:
                safe_call(reader.SetPointArrayStatus, v, 1 if v in wanted else 0,
                          default=None)
        else:
            for v in cell_arrays:
                safe_call(reader.SetCellArrayStatus, v, 1, default=None)
            for v in pt_arrays:
                safe_call(reader.SetPointArrayStatus, v, 1, default=None)

        safe_call(reader.Update, default=None)
        output = reader.GetOutput()
        if output is None:
            continue

        # Walk blocks, collect stats for each requested variable. We dedup
        # within a single file: take the FIRST block where the var appears.
        per_file_seen: set[str] = set()
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
                    if not name or name in per_file_seen:
                        continue
                    if wanted is not None and name not in wanted:
                        continue
                    container = wrapped.CellData if source_kind == "cell" else wrapped.PointData
                    arr = container[name]
                    if arr is None:
                        continue
                    arr_np = np.asarray(arr)
                    if arr_np.size == 0:
                        continue
                    stats = _compute_stats(arr_np)
                    record = {"time": time_v, "file_index": idx,
                              "data_scope": source_kind, **stats}
                    series.setdefault(name, []).append(record)
                    per_file_seen.add(name)

    if series:
        set_field(out, "time_series_data", series,
                  FieldProvenance("A",
                      f"per-file stats over {n_files} sequence files"))
        set_field(out, "time_series_times", time_values,
                  FieldProvenance("A", "from Tier 1 sequence detection"))
        set_field(out, "time_series_n_files", n_files,
                  FieldProvenance("A", "len(sequence_files)"))

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
