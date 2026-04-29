"""Tier output schemas (documented; not enforced at runtime).

Every Tier function takes (case_root: Path, [identity: dict, ...]) and returns a dict.
The dict shape is documented per Tier below. Missing fields are `None`, never absent.

Tier 1 — Identify
    {
        "format":     str | None,    # "openfoam" / "fluent" / "cgns" / "hdf5" / None
        "solver":     str | None,    # solver-specific name (reactingFoam / ...)
        "version":    str | None,    # solver version (v2212 / 2024R2 / ...)
        "layout":     str | None,    # "single-region" / "multi-region" / ...
        "decomposed": bool | None,   # parallel decomposition present
        "lagrangian": bool | None,   # Lagrangian phase detected
        "_path": "A" | "B" | "C",
        "_errors": list[str],
        "_warnings": list[str],
    }

Tier 2 — Inventory
    {
        "time_steps":      list[float],   # sorted ascending
        "variables":       list[str],     # union over all time steps
        "scripts":         list[str],     # paths relative to case_root
        "log_files":       list[str],
        "system_files":    list[str],     # known dictionary files in system/
        "auxiliary":       list[str],     # chemkin/, triSurface/, ...
        "post_processing": list[str],     # postProcessing/ subdirectories
        "regions":         list[str],     # for multi-region; ["fluid"] if single
        "lagrangian_clouds": list[str],
        ... (solver-specific extras OK)
    }

Tier 3 — Metadata
    Flat dict of metadata fields. Schema is solver-specific but recommended keys:
    {
        "application":       str,        # e.g. "reactingFoam"
        "mesh_cells":        int,
        "mesh_points":       int,
        "mesh_faces":        int,
        "boundaries":        list[dict],  # [{name, type, nFaces, startFace}]
        "turbulence_model":  str | None,
        "thermophysics":     dict | None,
        "combustion_model":  str | None,
        "chemistry":         dict | None,
        "time_control":      dict,
        "decomposition":     dict | None,
        ...
    }

Tier 4 — FieldStats
    {
        "variable_ranges": {
            "<var>": {"min": float, "max": float, "mean": float}
        },
        "bounding_box": {"x": [min, max], "y": [...], "z": [...]},
        "monitors": {"<name>": {"path": str, "n_rows": int, "columns": list[str]}},
        "residual_history": {"<var>": list[float]},
        ...
    }

Tier 5 — QOI (long-format records)
    [
        {"variable": str, "value": float | bool, "unit": str,
         "time": float | None, "region": str | None,
         "source": str, "rule_version": str | None, "evidence": dict | None}
    ]

Tier 6 — FullData
    Path to a VTU file (or list of VTU paths for multi-time/multi-block).

Tier 7 — Semantic
    {
        "physics_class":     {"value": str, "confidence": str, "source": str},
        "nl_summary":        {"value": str, "confidence": str, "source": str},
        ...
    }
"""
from __future__ import annotations

# Field-name conventions — keys starting with underscore are meta
META_KEYS = ("_path", "_provenance", "_errors", "_warnings")


def is_field_present(value) -> bool:
    """True iff this value is "actually extracted", i.e. not None and not empty list/dict."""
    if value is None:
        return False
    if isinstance(value, (list, dict, str)) and len(value) == 0:
        return False
    return True


def count_present_fields(tier_output: dict) -> int:
    """Count non-meta non-empty fields (for verification: ≥ 30 fields)."""
    return sum(
        1 for k, v in tier_output.items()
        if k not in META_KEYS and is_field_present(v)
    )
