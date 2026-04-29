"""Fluent Tier 4 — FieldStats (uses VTK + report file parsing + .trn residual parsing).

Reads:
    - cell data via vtkFLUENTReader / vtkFLUENTCFFReader → per-variable stats + bbox
    - *-rfile.out report files → time series (lift, drag, forces, moments)
    - *.trn transcript files → per-iteration residuals (continuity / x-velocity / ...)

Cross-solver invariants honored (see docs/adding_a_solver.md §2.4):
    variable_ranges, has_nan_field, nan_fields,
    variables_kind_verified,
    convergence_orders, convergence_status, convergence_summary.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from sim_parse.adapters.vtk_io import iterate_blocks, require_vtk
from sim_parse.core.diagnostics import (
    array_rank,
    classify_convergence_orders,
    detect_nonfinite_fields,
    summarize_convergence_status,
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
    inventory: dict | None = None,  # currently unused by Fluent; reserved for future kind-verification work
) -> dict | None:
    out = init_tier_output("A")

    cas_path = identity.get("cas_path")
    if not cas_path:
        add_warning(out, "No cas_path in identity")
        return out

    sub_format = identity.get("sub_format", "legacy")

    try:
        vtk, dsa = require_vtk()
    except RuntimeError as e:
        add_warning(out, f"VTK unavailable: {e}")
        return out

    reader = _make_reader(vtk, sub_format)
    if reader is None:
        add_warning(out, f"No VTK reader for sub_format={sub_format}")
        return out

    reader.SetFileName(cas_path)

    # vtkFLUENTReader quirk vs vtkOpenFOAMReader: UpdateInformation() does
    # NOT populate the cell-array enumeration. We must Update() first
    # (which reads all arrays), then enumerate via GetCellArrayName.
    # For an explicit `fields=[...]` subset we toggle and re-Update —
    # otherwise just keep what was already loaded.
    safe_call(reader.Update, default=None)

    n_arrs = safe_call(reader.GetNumberOfCellArrays, default=0)
    available = [reader.GetCellArrayName(i) for i in range(n_arrs or 0)
                 if reader.GetCellArrayName(i)]  # filter blank-name slots

    if fields is None:
        target_vars = list(available)
    else:
        target_vars = [v for v in fields if v in available]
        # Re-Update with filter (only worth doing if user explicitly asked)
        for v in available:
            safe_call(reader.SetCellArrayStatus, v, 0, default=None)
        for v in target_vars:
            safe_call(reader.SetCellArrayStatus, v, 1, default=None)
        safe_call(reader.Update, default=None)

    output = reader.GetOutput()

    variable_ranges: dict = {}
    rank_by_name: dict[str, str] = {}
    bbox_x_all, bbox_y_all, bbox_z_all = [], [], []

    for block in iterate_blocks(output):
        if block is None:
            continue
        cd = block.GetCellData()
        if cd is None:
            continue

        wrapped = dsa.WrapDataObject(block)
        for v in target_vars:
            if v in variable_ranges:
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
            variable_ranges[v] = _compute_stats(arr_np)
            rank_by_name[v] = array_rank(arr_np)

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
                  FieldProvenance("A", f"vtkFLUENT{'CFF' if sub_format=='cff' else ''}Reader"))
        # Mechanism #1 — NaN/Inf surveillance
        nan_fields = detect_nonfinite_fields(variable_ranges)
        set_field(out, "has_nan_field", bool(nan_fields),
                  FieldProvenance("A",
                      "True iff any variable_ranges entry has a non-finite stat"))
        set_field(out, "nan_fields", sorted(nan_fields),
                  FieldProvenance("A", "names of variables with non-finite stats"))
        if nan_fields:
            add_warning(out,
                f"{len(nan_fields)} variable(s) contain NaN or Inf values: "
                f"{sorted(nan_fields)}. Solution is likely numerically broken.")

    if bbox_x_all:
        set_field(out, "bounding_box", {
            "x": [float(min(p[0] for p in bbox_x_all)), float(max(p[1] for p in bbox_x_all))],
            "y": [float(min(p[0] for p in bbox_y_all)), float(max(p[1] for p in bbox_y_all))],
            "z": [float(min(p[0] for p in bbox_z_all)), float(max(p[1] for p in bbox_z_all))],
        }, FieldProvenance("A", "min/max over all blocks' points"))

    # Mechanism #1 — verified kinds (Fluent has no Tier 2 pre-classification,
    # so tier2_meta=None; physical_kind defaults to 'physical' for everything
    # the reader exposes, which is honest for a solver where the reader IS the
    # authoritative source of cell-array names).
    if rank_by_name:
        verified = verify_variable_kinds(
            tier2_meta=None,
            vtk_cell_arrays=available,
            rank_by_name=rank_by_name,
        )
        set_field(out, "variables_kind_verified", verified,
                  FieldProvenance("A",
                      f"vtkFLUENT{'CFF' if sub_format=='cff' else ''}Reader: "
                      "GetCellArrayName + array rank"))

    set_field(out, "available_variables_count", len(available),
              FieldProvenance("A", "vtkFLUENTReader.GetNumberOfCellArrays"))
    set_field(out, "exported_variable_count", len(variable_ranges),
              FieldProvenance("A", "len(variable_ranges)"))

    case_dir = Path(cas_path).parent

    # Force/coefficient time series from rfile.out
    reports = _read_fluent_report_files(case_dir)
    if reports:
        set_field(out, "report_series", reports,
                  FieldProvenance("A", "*-rfile.out / report-*.out CSV-like time series"))
        normalized = _normalize_report_names(reports)
        if normalized:
            set_field(out, "report_qoi", normalized,
                      FieldProvenance("A", "report file name + column heuristic"))

    # Mechanism #4 — convergence from .trn transcript residuals
    trn_files = sorted(case_dir.glob("*.trn"))
    if trn_files:
        residuals = _parse_fluent_trn_residuals(trn_files)
        if residuals:
            set_field(out, "residual_history", residuals,
                      FieldProvenance("A", "*.trn iteration table parsing"))
            orders = {var: _convergence_orders(history)
                      for var, history in residuals.items()}
            orders = {k: v for k, v in orders.items() if v is not None}
            if orders:
                set_field(out, "convergence_orders", orders,
                          FieldProvenance("A",
                              "log10(initial_residual / final_residual) per .trn variable"))
                cstatus = classify_convergence_orders(orders)
                set_field(out, "convergence_status", cstatus,
                          FieldProvenance("A",
                              "per-variable status; thresholds: "
                              "diverged<0, marginal in [0,3), converged>=3"))
                summary = summarize_convergence_status(cstatus)
                set_field(out, "convergence_summary", summary,
                          FieldProvenance("A",
                              "roll-up of convergence_status counts + lists"))
                if summary.get("diverged_vars"):
                    add_warning(out,
                        f"{len(summary['diverged_vars'])} Fluent variable(s) "
                        f"have residuals that INCREASED during the run "
                        f"(convergence_order < 0): {summary['diverged_vars']}.")
                if summary.get("marginal_vars"):
                    add_warning(out,
                        f"{len(summary['marginal_vars'])} Fluent variable(s) "
                        f"dropped fewer than 3 orders of magnitude (marginal): "
                        f"{summary['marginal_vars']}.")

    return out


# ─── Fluent report-file parser ────────────────────────────────────────────────


def _read_fluent_report_files(case_dir: Path) -> dict | None:
    """Parse Fluent *-rfile.out / report-*.out files into time series.

    Format (typical):
        "liftc-rfile"
        "Iteration" "liftc"
        ("Iteration" "liftc")
        1 1.207725885286939e-05
        2 -4.195748578988283e-06
        ...

    Returns:
        {
            "liftc-rfile.out": {
                "column_name": "liftc",
                "n_iterations": 15,
                "first_value": 1.21e-5,
                "last_value": 6.89e-3,
                "max_value": ...,
                "min_value": ...,
                "abs_max_value": ...,
            },
            ...
        }
    """
    if not case_dir.is_dir():
        return None

    out: dict = {}
    patterns = ["*-rfile.out", "report-*.out"]
    seen = set()

    for pattern in patterns:
        for f in sorted(case_dir.glob(pattern)):
            if f.name in seen or not f.is_file():
                continue
            seen.add(f.name)
            parsed = safe_call(_parse_one_report, f, default=None)
            if parsed:
                out[f.name] = parsed
    return out if out else None


_REPORT_HEADER_RE = re.compile(r'^\s*\(?\s*"([^"]*)"\s+"([^"]*)"\s*\)?\s*$')


# Canonical QOI mappings — keyed by column_name pattern (lowercase regex)
# Each entry: (regex pattern, canonical name)
_REPORT_NAME_CANONICAL = [
    (re.compile(r"^lift.*c?$|^c[._-]?l$"),       "lift_coefficient"),
    (re.compile(r"^drag.*c?$|^c[._-]?d$"),       "drag_coefficient"),
    (re.compile(r"^moment.*c?$|^c[._-]?m$"),     "moment_coefficient"),
    (re.compile(r"^f[._-]?x$|^force[._-]?x$"),   "force_x"),
    (re.compile(r"^f[._-]?y$|^force[._-]?y$"),   "force_y"),
    (re.compile(r"^f[._-]?z$|^force[._-]?z$"),   "force_z"),
    (re.compile(r"^m[._-]?x$|^moment[._-]?x$"),  "moment_x"),
    (re.compile(r"^m[._-]?y$|^moment[._-]?y$"),  "moment_y"),
    (re.compile(r"^m[._-]?z$|^moment[._-]?z$"),  "moment_z"),
    (re.compile(r"^mass[._-]?flow"),             "mass_flow_rate"),
    (re.compile(r"^heat[._-]?flux"),             "heat_flux_total"),
]


def _normalize_report_names(reports: dict) -> dict:
    """Map ad-hoc report names → canonical QOI names.

    Inputs:
        reports = {"liftc-rfile.out": {column_name, last_value, ...}, ...}

    Outputs:
        {"lift_coefficient": {last_value, max_value, source_file, ...}, ...}
    """
    out: dict = {}
    for filename, summary in reports.items():
        col = (summary.get("column_name") or "").lower()
        # also try the filename stem
        stem = filename.lower().replace("-rfile.out", "").replace(".out", "")
        stem_clean = stem.replace("report-", "")

        canonical = None
        for pattern, canon in _REPORT_NAME_CANONICAL:
            if pattern.match(col) or pattern.match(stem_clean):
                canonical = canon
                break
        if canonical is None:
            continue

        # First-seen wins for duplicates (e.g. user has both liftc and lift report)
        if canonical not in out:
            out[canonical] = {
                "last_value": summary.get("last_value"),
                "first_value": summary.get("first_value"),
                "min_value": summary.get("min_value"),
                "max_value": summary.get("max_value"),
                "abs_max_value": summary.get("abs_max_value"),
                "mean_value": summary.get("mean_value"),
                "n_iterations": summary.get("n_iterations"),
                "source_file": filename,
                "raw_column_name": summary.get("column_name"),
            }
    return out


def _parse_one_report(path: Path) -> dict | None:
    """Parse a single Fluent report-file.out.

    Header format varies but usually has 3 lines:
        Line 1: "<name>-rfile"
        Line 2: "Iteration" "<column_name>"
        Line 3: ("Iteration" "<column_name>")     ← S-expression style
    Then data lines: integer iteration + float value.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    iterations: list[int] = []
    values: list[float] = []
    column_name: str | None = None

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        # Try header line: extract column name
        if column_name is None:
            m = _REPORT_HEADER_RE.match(line)
            if m and m.group(1).lower() in ("iteration", "time"):
                column_name = m.group(2)
                continue

        # Try data line: int + float
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            it = int(parts[0])
            v = float(parts[1])
        except ValueError:
            continue
        iterations.append(it)
        values.append(v)

    if not values:
        return None

    arr = np.asarray(values)
    return {
        "column_name": column_name or "value",
        "n_iterations": len(values),
        "first_value": float(values[0]),
        "last_value": float(values[-1]),
        "max_value": float(arr.max()),
        "min_value": float(arr.min()),
        "abs_max_value": float(np.abs(arr).max()),
        "mean_value": float(arr.mean()),
    }


# ─── Fluent .trn transcript parser (per-iteration residuals) ──────────────────


# Matches a Fluent residual header row, e.g.
#   "  iter  continuity  x-velocity  y-velocity  z-velocity      energy     time/iter"
# Column count is variable across solver settings, so we capture the
# header tokens and use them as variable names for subsequent data rows.
_TRN_HEADER_RE = re.compile(
    r"^\s*iter\s+(?P<cols>[a-zA-Z0-9_\-/\s]+?)(?:\s+time/iter)?\s*$",
    re.MULTILINE,
)
# A data row: leading int, then >=1 floating-point residuals.
_TRN_DATA_FLOAT_RE = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")


def _parse_fluent_trn_residuals(trn_files: list[Path]) -> dict[str, list]:
    """Parse Fluent .trn residual tables into per-variable history.

    Returns:
        {var_name: [[initial, final], ...]} where each entry corresponds
        to ONE iteration (initial == final because Fluent reports a single
        residual per iteration, not initial/final like OpenFOAM does).

    Header convention:
        "  iter  continuity  x-velocity  y-velocity  z-velocity  energy  time/iter"
    Skip 'iter' (first column) and 'time/iter' (last column) — those are
    metadata, not residuals.

    The header may repeat (Fluent re-prints columns periodically); each
    repeat starts a fresh data section that we accumulate.
    """
    residuals: dict[str, list] = {}

    for tf in trn_files:
        try:
            text = tf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not text:
            continue

        current_cols: list[str] | None = None
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            # Header line?
            m = _TRN_HEADER_RE.match(line)
            if m:
                tokens = m.group("cols").split()
                # Drop the trailing 'time/iter' column if present in tokens.
                if tokens and tokens[-1].lower() == "time/iter":
                    tokens = tokens[:-1]
                current_cols = tokens
                continue

            if current_cols is None:
                continue
            # Data row: starts with iteration number, then residuals.
            # Skip non-numeric prefixes (Fluent occasionally emits status text).
            tokens = stripped.split()
            if not tokens:
                continue
            try:
                int(tokens[0])
            except ValueError:
                continue
            floats = _TRN_DATA_FLOAT_RE.findall(line)
            # First match is the iteration number; rest are values.
            if len(floats) < 1 + len(current_cols):
                continue
            values = floats[1 : 1 + len(current_cols)]
            for col_name, val_str in zip(current_cols, values):
                try:
                    v = float(val_str)
                except ValueError:
                    continue
                # Each iteration: store (val, val) so the OpenFOAM-style
                # _convergence_orders helper (which expects [initial, final]
                # pairs per iteration) keeps working.
                residuals.setdefault(col_name, []).append([v, v])

    return residuals


def _convergence_orders(history: list) -> float | None:
    """log10(initial_first / final_last). Mirrors OpenFOAM's helper but
    we keep it local so the Fluent module is self-contained."""
    if not history:
        return None
    initial_first = history[0][0]
    final_last = history[-1][1]
    if initial_first <= 0 or final_last <= 0:
        return None
    return float(np.log10(initial_first / final_last))


def _make_reader(vtk_module, sub_format: str):
    if sub_format == "cff":
        try:
            from vtkmodules.vtkIOFLUENTCFF import vtkFLUENTCFFReader
            return vtkFLUENTCFFReader()
        except ImportError:
            return None
    try:
        from vtkmodules.vtkIOGeometry import vtkFLUENTReader
        return vtkFLUENTReader()
    except ImportError:
        return None


def _compute_stats(arr: "np.ndarray") -> dict:
    if arr.ndim == 1:
        return {
            "min": float(arr.min()),
            "max": float(arr.max()),
            "mean": float(arr.mean()),
        }
    if arr.ndim == 2 and arr.shape[1] == 3:
        mag = np.linalg.norm(arr, axis=1)
        return {
            "magnitude_min": float(mag.min()),
            "magnitude_max": float(mag.max()),
            "magnitude_mean": float(mag.mean()),
            "component_min": [float(arr[:, i].min()) for i in range(3)],
            "component_max": [float(arr[:, i].max()) for i in range(3)],
        }
    return {"shape": list(arr.shape)}
