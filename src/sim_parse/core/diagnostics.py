"""Cross-solver diagnostic helpers.

These were originally inlined inside solvers/openfoam/tier4_field_stats.py;
extracted so any new solver (Fluent, CGNS, EnSight, ...) can produce the
same structured outputs without copy-pasting:

    - Field kind verification (mechanism #1):
        _array_rank, _VTK_CELL_TYPE_NAMES, _vtk_cell_type_name,
        verify_variable_kinds.

    - NaN/Inf surveillance (mechanism #1 + #4 stability):
        _is_finite_number, detect_nonfinite_fields.

    - Convergence-order classification (mechanism #4):
        CONVERGENCE_THRESHOLDS, convergence_status_label,
        classify_convergence_orders, summarize_convergence_status.

The schema of each output dict is exactly what `docs/adding_a_solver.md`
specifies — adding a new solver means populating these via this module,
not re-inventing them.
"""
from __future__ import annotations

import math
from typing import Any


# ─── Field kind verification (mechanism #1) ───────────────────────────────────


# Map VTK cell-type IDs → human-readable names. Hardcoded set covers what
# the readers in this codebase actually emit; unknown IDs render as
# 'vtkCellTypeId<n>' so they remain identifiable in logs/diffs.
VTK_CELL_TYPE_NAMES = {
    1: "vtkVertex",
    3: "vtkLine",
    5: "vtkTriangle",
    7: "vtkPolygon",
    9: "vtkQuad",
    10: "vtkTetra",
    12: "vtkHexahedron",
    13: "vtkWedge",
    14: "vtkPyramid",
    42: "vtkPolyhedron",
}


def vtk_cell_type_name(type_id: int) -> str:
    return VTK_CELL_TYPE_NAMES.get(int(type_id), f"vtkCellTypeId{int(type_id)}")


def array_rank(arr) -> str:
    """Classify a cell-data numpy array by tensor rank.

    Returns one of: 'scalar' | 'vector' | 'tensor' | 'unknown'.
    Accepts anything with .ndim/.shape (numpy arrays, dsa wrappers, ...).
    """
    try:
        ndim = arr.ndim
        shape = arr.shape
    except AttributeError:
        return "unknown"
    if ndim == 1:
        return "scalar"
    if ndim == 2 and len(shape) == 2 and shape[1] == 3:
        return "vector"
    if ndim == 2 and len(shape) == 2 and shape[1] in (6, 9):
        return "tensor"
    return "unknown"


def verify_variable_kinds(
    *,
    tier2_meta: dict | None,
    vtk_cell_arrays: list[str],
    rank_by_name: dict[str, str],
) -> dict[str, dict]:
    """Build a per-variable verified-kind dict (mechanism #1).

    Two orthogonal axes:
      physical_kind: 'physical' | 'diagnostic' | 'template' | 'face_flux'
        — Intent / role from Tier 2 heuristic.
      data_shape:    'cell_scalar' | 'cell_vector' | 'cell_tensor'
                   | 'cell_unknown' | 'not_in_vtk'
        — Verified by the reader.

    Plus:
      phantom: True iff Tier 2 listed it as a probable physical field but
               the reader exposes no cell array — the alarm condition.

    `tier2_meta` may be None (e.g. Fluent's Tier 2 can't pre-classify
    because cell arrays aren't visible until reader.Update()). In that
    case every name's tier2_kind is recorded as None and physical_kind
    defaults to 'physical' — perfectly honest, just less informative.
    """
    out: dict[str, dict] = {}
    reader_set = set(vtk_cell_arrays)
    tier2_meta = tier2_meta or {}
    all_names = set(tier2_meta.keys()) | reader_set

    physical_intent_to_label = {
        "face_flux": "face_flux",
        "diagnostic": "diagnostic",
        "template": "template",
        # cell_field / ic_only default to 'physical'; Tier 4 has the final say.
    }

    for name in sorted(all_names):
        tier2_kind = (tier2_meta.get(name) or {}).get("kind") if tier2_meta else None
        physical_kind = physical_intent_to_label.get(tier2_kind, "physical")

        if name in reader_set:
            rank = rank_by_name.get(name, "unknown")
            data_shape = {
                "scalar": "cell_scalar",
                "vector": "cell_vector",
                "tensor": "cell_tensor",
            }.get(rank, "cell_unknown")
            phantom = False
            evidence = (
                f"reader exposes '{name}' as a cell array; rank = {rank}"
            )
        else:
            data_shape = "not_in_vtk"
            phantom = (physical_kind == "physical")
            if phantom:
                evidence = (
                    f"Tier 2 listed '{name}' as a probable physical field but "
                    f"the reader does not expose any cell array for it. Likely "
                    f"an empty placeholder, dictionary, or non-cell-defined quantity."
                )
            else:
                evidence = (
                    f"Tier 2 heuristic kind '{tier2_kind}' is non-physical; "
                    f"absence from the reader is consistent."
                )

        out[name] = {
            "physical_kind": physical_kind,
            "data_shape": data_shape,
            "phantom": phantom,
            "tier2_kind": tier2_kind,
            "evidence": evidence,
        }
    return out


# ─── NaN / Inf surveillance (mechanism #1 + #4 stability) ────────────────────


def _is_finite_number(v: Any) -> bool:
    """True iff v is a finite int/float (not NaN/Inf, not non-numeric).

    bool is excluded (not a stat number even though Python bool is int subclass).
    """
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        if isinstance(v, float):
            return math.isfinite(v)
        return True
    return False


# ─── Unit-range sanity (mechanism #5) ─────────────────────────────────────────


# Variables whose values must lie in a known range by definition. Naming
# is canonical: the qoi_engine alias map handles solver-native equivalents
# (e.g. Fluent X_VELOCITY → U) so these checks fire cross-solver.
#
# Range form: (min, max, name_pattern_regex_or_set, label).
# Empty list means: no range constraint (default — most physical fields
# don't have a closed-form bound).
UNIT_RANGE_RULES = [
    # Mass fractions of species in OpenFOAM/Fluent reactingMixture: ∈ [0, 1]
    # Match common species naming. Tolerance 1.01 to allow tiny float overshoot.
    {
        "label": "mass_fraction_out_of_range",
        "lo": 0.0, "hi": 1.01,
        "names_exact": {
            "CH4", "CO", "CO2", "H2", "H2O", "H2O2", "HO2", "OH",
            "N2", "O", "O2", "H",
            "C", "C2H", "C2H2", "C2H3", "C2H4", "C2H5", "C2H6",
            "C3H7", "C3H8",
            "CH", "CH2", "CH3", "CH2O", "CH2OH", "CH3O", "CH3OH",
            "CH3CHO", "CH2CHO", "CH2CO", "HCO", "HCCO", "HCCOH",
            "Ydefault",
        },
        "stat_keys": ("max", "min"),  # check both max and min
        "reason": (
            "mass fraction must be in [0, 1] by definition; values outside "
            "this range usually mean the field stored is molar concentration "
            "(kmol/m³), volume fraction × molecular weight, or the solver "
            "did not normalize. Sim-knowledge rules using *_mass_fraction "
            "may be misleading on this case."
        ),
    },
    # Probability-like fields
    {
        "label": "kappa_out_of_range",
        "lo": 0.0, "hi": 1.0,
        "names_exact": {"EDC_psiReactionThermo__kappa"},
        "stat_keys": ("max", "min"),
        "reason": "EDC kappa is a fraction in [0, 1] by formulation",
    },
]


def check_unit_ranges(variable_ranges: dict) -> list[dict]:
    """Scan variable_ranges and return list of unit-range violations.

    Returns: [{variable, label, value, expected_min, expected_max, reason}, ...]
    """
    violations: list[dict] = []
    for rule in UNIT_RANGE_RULES:
        names = rule.get("names_exact") or set()
        for var_name, stats in variable_ranges.items():
            if var_name not in names:
                continue
            if not isinstance(stats, dict):
                continue
            lo, hi = rule["lo"], rule["hi"]
            for stat_key in rule.get("stat_keys", ("max",)):
                if stat_key not in stats:
                    continue
                v = stats[stat_key]
                if not isinstance(v, (int, float)) or not _is_finite_number(v):
                    continue
                if v < lo or v > hi:
                    violations.append({
                        "variable": var_name,
                        "label": rule["label"],
                        "stat": stat_key,
                        "value": float(v),
                        "expected_min": lo,
                        "expected_max": hi,
                        "reason": rule["reason"],
                    })
                    # Don't double-report the same variable for both min/max
                    break
    return violations


def detect_nonfinite_fields(variable_ranges: dict) -> set[str]:
    """Return names of variables with at least one non-finite stat value.

    Walks every numeric leaf in variable_ranges (including nested lists like
    component_max[3]). Strings, dicts, missing keys, and other non-numeric
    types are ignored.
    """
    bad: set[str] = set()

    def has_bad(value) -> bool:
        if isinstance(value, list):
            return any(has_bad(x) for x in value)
        if isinstance(value, (int, float)):
            return not _is_finite_number(value)
        return False

    for var_name, stats in variable_ranges.items():
        if not isinstance(stats, dict):
            continue
        for leaf in stats.values():
            if has_bad(leaf):
                bad.add(var_name)
                break
    return bad


# ─── Convergence-order classification (mechanism #4) ──────────────────────────


# Threshold bands. Tunable in future via sim-knowledge thresholds.yaml;
# hardcoded here as the self-contained default.
CONVERGENCE_THRESHOLDS = {
    "diverged": 0.0,    # x < 0   → residual increased
    "converged": 3.0,   # x >= 3  → standard success criterion
    # marginal is the [0, 3) band by exclusion.
}


def convergence_status_label(order: float) -> str:
    if order < CONVERGENCE_THRESHOLDS["diverged"]:
        return "diverged"
    if order < CONVERGENCE_THRESHOLDS["converged"]:
        return "marginal"
    return "converged"


def classify_convergence_orders(orders: dict[str, float]) -> dict[str, dict]:
    """Build {var: {value, status, threshold_used}} from a flat orders dict.

    Each entry carries the thresholds that produced it for traceability
    (mechanism #5: every output must be reproducible).
    """
    out: dict[str, dict] = {}
    for var, value in orders.items():
        out[var] = {
            "value": value,
            "status": convergence_status_label(value),
            "threshold_used": dict(CONVERGENCE_THRESHOLDS),
        }
    return out


def summarize_convergence_status(cstatus: dict[str, dict]) -> dict:
    """Roll up per-variable status into counts + sorted lists."""
    converged: list[str] = []
    marginal: list[str] = []
    diverged: list[str] = []
    for var, info in cstatus.items():
        st = info.get("status")
        if st == "converged":
            converged.append(var)
        elif st == "marginal":
            marginal.append(var)
        elif st == "diverged":
            diverged.append(var)
    return {
        "n_converged": len(converged),
        "n_marginal": len(marginal),
        "n_diverged": len(diverged),
        "n_total": len(cstatus),
        "converged_vars": sorted(converged),
        "marginal_vars": sorted(marginal),
        "diverged_vars": sorted(diverged),
    }
