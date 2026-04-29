"""EnSight Gold Tier 2 — Inventory.

Lists what's referenced in the .case file (model, variables, time set)
plus what's actually present in the case directory.
"""
from __future__ import annotations

from pathlib import Path

from sim_parse.core.errors import never_raise
from sim_parse.core.provenance import (
    FieldProvenance,
    init_tier_output,
    set_field,
)
from sim_parse.solvers.ensight._case_parser import parse_case_file


@never_raise(default=None)
def inventory(case_root: Path, identity: dict) -> dict | None:
    out = init_tier_output("A")

    case_path = Path(identity.get("case_path", ""))
    case_dir = Path(identity.get("case_dir", case_path.parent if case_path else "."))

    parsed = parse_case_file(case_path) if case_path.is_file() else {}

    # Core fields from the .case parse
    set_field(out, "case_file", str(case_path),
              FieldProvenance("A", "from Tier 1"))
    set_field(out, "case_dir", str(case_dir),
              FieldProvenance("A", "from Tier 1"))

    # Variables — list of {role, scope, name, data_file}
    variables = parsed.get("variables") or []
    set_field(out, "variables_meta_ensight", variables,
              FieldProvenance("A", ".case VARIABLE section"))
    # Cross-solver `variables` field: just the list of names (the playbook
    # contract). Kind classification lives in Tier 4 once VTK can verify
    # data_shape — Tier 2 here only knows the EnSight role tag.
    var_names = [v["name"] for v in variables if v.get("name")]
    set_field(out, "variables", sorted(var_names),
              FieldProvenance("A", "names from .case VARIABLE section"))

    # variables_meta — playbook §2.2 contract. EnSight role/scope translate
    # to physical vs face_flux distinctions.
    vmeta: dict[str, dict] = {}
    for v in variables:
        role = v.get("role", "")
        scope = v.get("scope", "")
        # All EnSight-declared variables are physical fields by definition;
        # the only kind classification we need is whether they're per-node
        # vs per-element (cell). Both map to 'cell_field' in our schema —
        # Tier 4 will refine via VTK rank.
        vmeta[v["name"]] = {
            "kind": "cell_field",
            "evidence": f"declared in .case as '{role} {scope}' with data file '{v.get('data_file')}'",
            "ensight_role": role,
            "ensight_scope": scope,
            "data_file": v.get("data_file"),
        }
    if vmeta:
        set_field(out, "variables_meta", vmeta,
                  FieldProvenance("A", ".case VARIABLE entries with ensight_role/scope tags"))

    # Model (geometry) file
    if parsed.get("model_file"):
        set_field(out, "model_file", parsed["model_file"],
                  FieldProvenance("A", ".case GEOMETRY model:"))

    # Time set (optional)
    if parsed.get("time_set"):
        set_field(out, "time_set", parsed["time_set"],
                  FieldProvenance("A", ".case TIME section"))
        ts = parsed["time_set"]
        if ts.get("time_values"):
            set_field(out, "time_steps", ts["time_values"],
                      FieldProvenance("A", "time_set.time_values"))
            set_field(out, "n_time_steps", len(ts["time_values"]),
                      FieldProvenance("A", "len(time_steps)"))
            set_field(out, "latest_time", max(ts["time_values"]),
                      FieldProvenance("A", "max(time_steps)"))

    # Files actually present in the case_dir (cross-reference)
    if case_dir.is_dir():
        files_present = sorted(p.name for p in case_dir.iterdir() if p.is_file())
        set_field(out, "files_present", files_present,
                  FieldProvenance("A", "ls of case directory"))

        # Sanity: which declared variables have their data file actually present?
        if variables:
            missing = [v for v in variables
                       if v.get("data_file") and v["data_file"] not in files_present]
            if missing:
                from sim_parse.core.provenance import add_warning
                add_warning(out,
                    f"{len(missing)} declared variable(s) have data files NOT "
                    f"present in case_dir: {[v['name'] for v in missing]}")

    return out
