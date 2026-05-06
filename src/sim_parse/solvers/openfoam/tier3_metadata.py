"""OpenFOAM Tier 3 — Metadata.

Reads:
    - system/controlDict          -> application, time control, write format
    - system/fvSchemes            -> discretization schemes
    - system/fvSolution           -> linear solver settings
    - system/decomposeParDict     -> parallel decomposition
    - constant/polyMesh/owner     -> mesh counts (header note)
    - constant/polyMesh/boundary  -> boundary patches
    - constant/turbulenceProperties -> turbulence model
    - constant/thermophysicalProperties -> thermo type
    - constant/chemistryProperties -> chemistry settings
    - constant/combustionProperties -> combustion model
    - constant/sprayCloudProperties -> spray injection (if present)
    - constant/g                  -> gravity (if present)
    - log.<solver> first banner   -> case_origin_path

No VTK; no array decoding. Pure text reading.
"""
from __future__ import annotations

import re
from pathlib import Path

from sim_parse.core.errors import never_raise, safe_call
from sim_parse.core.provenance import (
    FieldProvenance,
    init_tier_output,
    set_field,
)
from sim_parse.solvers.openfoam._foam_dict import (
    parse_block_to_dict,
    parse_uniform_value,
    read_foam_block,
    read_foam_value,
    read_text,
)
from sim_parse.solvers.openfoam._polymesh import (
    read_boundary_patches,
    read_owner_header_note,
)


@never_raise(default=None)
def metadata(case_root: Path, identity: dict, inventory: dict) -> dict | None:
    case_root = Path(case_root)
    out = init_tier_output("A")

    # Carry forward solver from Tier 1
    if identity.get("solver"):
        set_field(out, "application", identity["solver"],
                  FieldProvenance("A", "system/controlDict::application"))

    # ─── controlDict — time control + write settings ─────────────────────────
    control_dict = case_root / "system" / "controlDict"
    if control_dict.is_file():
        time_control = _read_time_control(control_dict)
        if time_control:
            set_field(out, "time_control", time_control,
                      FieldProvenance("A", "system/controlDict"))

    # ─── polyMesh header — mesh counts ────────────────────────────────────────
    region = ""
    if identity.get("layout") == "multi-region":
        regions = identity.get("regions") or []
        region = regions[0] if regions else ""
    note = read_owner_header_note(case_root, region=region)
    if note:
        if "nCells" in note:
            set_field(out, "mesh_cells", note["nCells"],
                      FieldProvenance("A", "constant/polyMesh/owner header note"))
        if "nPoints" in note:
            set_field(out, "mesh_points", note["nPoints"],
                      FieldProvenance("A", "constant/polyMesh/owner header note"))
        if "nFaces" in note:
            set_field(out, "mesh_faces", note["nFaces"],
                      FieldProvenance("A", "constant/polyMesh/owner header note"))
        if "nInternalFaces" in note:
            set_field(out, "mesh_internal_faces", note["nInternalFaces"],
                      FieldProvenance("A", "constant/polyMesh/owner header note"))

    # ─── boundary patches ─────────────────────────────────────────────────────
    patches = read_boundary_patches(case_root, region=region)
    if patches:
        set_field(out, "boundaries", patches,
                  FieldProvenance("A", "constant/polyMesh/boundary"))

    # ─── unified mesh_zones schema (cross-solver) ─────────────────────────────
    # Skeleton built from text-only sources: one volume zone (internalMesh)
    # + N boundary zones from polyMesh/boundary. Geometry fields
    # (bounding_box, n_points, element_types) are left None and may be
    # filled in later by Tier 4 via VTK. Schema is solver-agnostic.
    mesh_zones = _build_mesh_zones_skeleton(out, patches)
    if mesh_zones:
        set_field(out, "mesh_zones", mesh_zones,
                  FieldProvenance("A",
                      "polyMesh/owner (volume zone) + polyMesh/boundary "
                      "(boundary zones); geometry fields TBD by Tier 4"))

    # ─── turbulence ──────────────────────────────────────────────────────────
    turb = _read_turbulence(case_root)
    if turb:
        set_field(out, "turbulence", turb,
                  FieldProvenance("A", "constant/turbulenceProperties"))

    # ─── thermophysics ───────────────────────────────────────────────────────
    thermo = _read_thermophysics(case_root)
    if thermo:
        set_field(out, "thermophysics", thermo,
                  FieldProvenance("A", "constant/thermophysicalProperties"))

    # ─── chemistry ────────────────────────────────────────────────────────────
    chem = _read_chemistry(case_root)
    if chem:
        set_field(out, "chemistry", chem,
                  FieldProvenance("A", "constant/chemistryProperties"))

    # ─── combustion ──────────────────────────────────────────────────────────
    comb = _read_combustion(case_root)
    if comb:
        set_field(out, "combustion", comb,
                  FieldProvenance("A", "constant/combustionProperties"))

    # ─── spray (optional) ─────────────────────────────────────────────────────
    spray = _read_spray(case_root)
    if spray:
        set_field(out, "spray", spray,
                  FieldProvenance("A", "constant/sprayCloudProperties"))

    # ─── gravity (optional) ───────────────────────────────────────────────────
    grav = _read_gravity(case_root)
    if grav is not None:
        set_field(out, "gravity", grav,
                  FieldProvenance("A", "constant/g"))

    # ─── decomposition ───────────────────────────────────────────────────────
    decomp = _read_decomposition(case_root)
    if decomp:
        set_field(out, "decomposition", decomp,
                  FieldProvenance("A", "system/decomposeParDict"))

    # ─── case origin path (from log banner) ──────────────────────────────────
    origin = _read_case_origin_path(case_root, inventory.get("log_files", []))
    if origin:
        set_field(out, "case_origin_path", origin,
                  FieldProvenance("A", "log file banner 'Case :' line"))

    # ─── BC inlet values (Phase 2 expression engine inputs) ──────────────────
    bc = _extract_inlet_bcs(case_root, vars_to_try=("T", "U", "p"))
    if bc:
        set_field(out, "bc", bc,
                  FieldProvenance("A", "0/<var> boundaryField fixedValue patches"))

    # ─── is_transient (from system/fvSchemes::ddtSchemes.default) ───────────
    transient_info = _detect_transient(case_root)
    if transient_info is not None:
        is_transient, ddt_default = transient_info
        set_field(out, "is_transient", is_transient,
                  FieldProvenance("A",
                      f"system/fvSchemes::ddtSchemes.default = {ddt_default!r} "
                      f"(steadyState→False, anything else→True)"))

    # ─── physics_setup container (cross-solver migration target) ─────────────
    # Double-write pattern: keep the legacy top-level turbulence/thermophysics/
    # chemistry/combustion fields above AND mirror them into a structured
    # container alongside. Downstream consumers can switch to physics_setup
    # at their own pace; sim-knowledge YAML rules will migrate in a follow-up.
    physics_setup = _build_physics_setup(out)
    set_field(out, "physics_setup", physics_setup,
              FieldProvenance("A",
                  "container of {turbulence, thermophysics, chemistry, "
                  "combustion, radiation, multiphase}; each is either an "
                  "extracted dict or a NotExtracted/NotApplicable sentinel"))

    # ─── is_completed (vs endTime) ───────────────────────────────────────────
    if "time_control" in out and "latest_time" in inventory:
        end_time = out["time_control"].get("endTime")
        latest = inventory["latest_time"]
        if end_time is not None and latest is not None:
            try:
                set_field(out, "is_completed", float(latest) >= float(end_time),
                          FieldProvenance("A", "latest_time vs controlDict::endTime"))
            except (TypeError, ValueError):
                pass

    return out


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _build_physics_setup(tier3_out: dict) -> dict:
    """Build the cross-solver physics_setup container from already-extracted
    OpenFOAM Tier 3 fields.

    Sentinel choice per component:
      - if the legacy field exists in `out` → use it verbatim (extracted)
      - chemistry/combustion absent → NotApplicable (cold-flow case is the
        legitimate reason; a reacting case that lost extraction would be
        rare and rerunning would surface as a parser bug, not a missing
        field)
      - turbulence/thermophysics absent → NotExtracted (these should be
        present on every modern OF case; absence means our extractor
        couldn't read the file)
      - radiation/multiphase → NotApplicable for now (we don't extract
        these yet; flagged here for future fill-in)

    Returns a plain dict (not the pydantic model) so it serializes to JSON
    cleanly via the existing parse_case → MCP path.
    """
    from sim_parse.core.schema import NotApplicable, NotExtracted

    def _slot(key: str, *, na_reason: str = "", ne_reason: str = "",
              would_require: str = "") -> dict:
        existing = tier3_out.get(key)
        if existing:
            return existing
        if na_reason:
            return NotApplicable(reason=na_reason).model_dump()
        return NotExtracted(reason=ne_reason,
                            would_require=would_require or None).model_dump()

    return {
        "turbulence": _slot(
            "turbulence",
            ne_reason="constant/turbulenceProperties not parsed",
            would_require="re-read constant/turbulenceProperties",
        ),
        "thermophysics": _slot(
            "thermophysics",
            ne_reason="constant/thermophysicalProperties not parsed",
            would_require="re-read constant/thermophysicalProperties",
        ),
        "chemistry": _slot(
            "chemistry",
            na_reason=("non-reacting case detected — no chemistryProperties "
                       "file present and chemistry block not extracted"),
        ),
        "combustion": _slot(
            "combustion",
            na_reason=("non-reacting case detected — no combustionProperties "
                       "file present and combustion block not extracted"),
        ),
        "radiation": NotApplicable(
            reason="radiation extraction not yet implemented for OpenFOAM"
        ).model_dump(),
        "multiphase": NotApplicable(
            reason="multiphase extraction not yet implemented for OpenFOAM"
        ).model_dump(),
    }


@never_raise(default=None)
def _detect_transient(case_root: Path) -> tuple[bool, str] | None:
    """Read system/fvSchemes::ddtSchemes.default to classify steady vs transient.

    Returns (is_transient, ddt_default_value) or None if fvSchemes is missing
    or the ddtSchemes block is unparseable. `steadyState` → False, anything
    else (Euler, CrankNicolson, backward, etc.) → True.

    OpenFOAM convention: `ddtSchemes { default steadyState; ... }` for steady
    solvers; transient solvers pick a real time-derivative discretization.
    """
    fv = case_root / "system" / "fvSchemes"
    if not fv.is_file():
        return None
    text = fv.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    m = re.search(r"ddtSchemes\s*\{[^}]*\bdefault\s+(\w[\w\d_-]*)", text, re.DOTALL)
    if not m:
        return None
    ddt = m.group(1)
    return (ddt.lower() != "steadystate", ddt)


def _build_mesh_zones_skeleton(tier3_out: dict, patches: list[dict] | None) -> list[dict]:
    """Build the unified `mesh_zones` array from text-only sources.

    Returns a list with:
      - 1 volume zone called 'internalMesh' (n_cells from owner header)
      - N boundary zones from polyMesh/boundary

    All geometry fields (bounding_box / n_points / element_types) are None
    here; Tier 4 may fill them in via VTK.

    Schema is solver-agnostic — Fluent/CGNS/etc. emit objects with the same
    keys. See solvers/openfoam/tier4_field_stats._enrich_mesh_zones for the
    geometry enrichment path.
    """
    zones: list[dict] = []

    # Always emit the internalMesh volume zone so the schema is satisfied
    # even when mesh counts are unknown at Tier 3 (case has owner.gz with
    # no `note` field). Tier 4 fills geometry via VTK; downstream consumers
    # treat None fields as "TBD" rather than "absent".
    n_cells = tier3_out.get("mesh_cells")
    n_faces = tier3_out.get("mesh_faces")
    zones.append({
        "name": "internalMesh",
        "role": "volume",
        "n_cells": n_cells,
        "n_faces": n_faces,
        "n_points": None,             # Tier 4 may fill (per-zone, not total)
        "bounding_box": None,         # Tier 4 may fill
        "element_types": None,        # Tier 4 may fill
        "patch_type": None,
        "_source": "constant/polyMesh/owner header note",
    })

    if patches:
        for p in patches:
            zones.append({
                "name": p.get("name"),
                "role": "boundary",
                "n_cells": None,
                "n_faces": p.get("nFaces"),
                "n_points": None,
                "bounding_box": None,
                "element_types": None,
                "patch_type": p.get("type"),
                "_source": "constant/polyMesh/boundary",
            })
    return zones


def _read_time_control(control_dict: Path) -> dict | None:
    keys = [
        "application", "startFrom", "startTime", "stopAt", "endTime",
        "deltaT", "writeControl", "writeInterval", "writeFormat",
        "writePrecision", "writeCompression", "timeFormat", "timePrecision",
        "purgeWrite", "runTimeModifiable", "adjustTimeStep", "maxCo",
    ]
    out: dict = {}
    for k in keys:
        v = read_foam_value(control_dict, k)
        if v is not None:
            out[k] = _coerce_value(v)
    return out if out else None


def _read_turbulence(case_root: Path) -> dict | None:
    f = case_root / "constant" / "turbulenceProperties"
    if not f.is_file():
        return None
    out: dict = {}
    sim_type = read_foam_value(f, "simulationType")
    if sim_type:
        out["simulationType"] = sim_type
    # The model name is inside a block named after sim_type
    if sim_type:
        body = read_foam_block(f, sim_type)
        if body:
            inner = parse_block_to_dict(body)
            for k in ("RASModel", "LESModel", "model", "turbulence", "printCoeffs"):
                if k in inner:
                    out[k] = _coerce_value(inner[k] if isinstance(inner[k], str) else inner[k])
    return out if out else None


def _read_thermophysics(case_root: Path) -> dict | None:
    f = case_root / "constant" / "thermophysicalProperties"
    if not f.is_file():
        return None
    out: dict = {}
    body = read_foam_block(f, "thermoType")
    if body:
        out["thermoType"] = parse_block_to_dict(body)
    inert = read_foam_value(f, "inertSpecie")
    if inert:
        out["inertSpecie"] = inert
    chem_file = read_foam_value(f, "foamChemistryFile")
    if chem_file:
        out["foamChemistryFile"] = chem_file
    chem_thermo = read_foam_value(f, "foamChemistryThermoFile")
    if chem_thermo:
        out["foamChemistryThermoFile"] = chem_thermo
    chem_reader = read_foam_value(f, "chemistryReader")
    if chem_reader:
        out["chemistryReader"] = chem_reader
    return out if out else None


def _read_chemistry(case_root: Path) -> dict | None:
    f = case_root / "constant" / "chemistryProperties"
    if not f.is_file():
        return None
    out: dict = {"enabled": True}
    body = read_foam_block(f, "chemistryType")
    if body:
        out["chemistryType"] = parse_block_to_dict(body)
    chem_on = read_foam_value(f, "chemistry")
    if chem_on is not None:
        out["chemistry"] = chem_on  # 'on' / 'off'
    initial_dt = read_foam_value(f, "initialChemicalTimeStep")
    if initial_dt:
        out["initialChemicalTimeStep"] = _coerce_value(initial_dt)

    # reduction block (DAC etc)
    red_body = read_foam_block(f, "reduction")
    if red_body:
        red = parse_block_to_dict(red_body)
        # extract method
        for k in ("active", "method", "tolerance"):
            if k in red:
                out.setdefault("reduction", {})[k] = red[k]

    # tabulation (ISAT etc)
    tab_body = read_foam_block(f, "tabulation")
    if tab_body:
        tab = parse_block_to_dict(tab_body)
        for k in ("active", "method", "tolerance"):
            if k in tab:
                out.setdefault("tabulation", {})[k] = tab[k]
    return out


def _read_combustion(case_root: Path) -> dict | None:
    f = case_root / "constant" / "combustionProperties"
    if not f.is_file():
        return None
    out: dict = {}
    model = read_foam_value(f, "combustionModel")
    if model:
        out["combustionModel"] = model
    active = read_foam_value(f, "active")
    if active:
        out["active"] = active
    return out if out else None


def _read_spray(case_root: Path) -> dict | None:
    f = case_root / "constant" / "sprayCloudProperties"
    if not f.is_file():
        return None
    out: dict = {"present": True}
    sol = read_foam_value(f, "solution")
    if sol:
        out["solution_hint"] = sol[:80]
    return out


def _read_gravity(case_root: Path) -> list | None:
    f = case_root / "constant" / "g"
    if not f.is_file():
        return None
    text = read_text(f)
    m = re.search(r"value\s+\(([^)]+)\)", text)
    if m:
        try:
            return [float(x) for x in m.group(1).split()]
        except ValueError:
            return None
    return None


def _read_decomposition(case_root: Path) -> dict | None:
    f = case_root / "system" / "decomposeParDict"
    if not f.is_file():
        return None
    out: dict = {}
    n = read_foam_value(f, "numberOfSubdomains")
    if n:
        try:
            out["numberOfSubdomains"] = int(n)
        except ValueError:
            out["numberOfSubdomains"] = n
    method = read_foam_value(f, "method")
    if method:
        out["method"] = method
    return out if out else None


def _extract_inlet_bcs(case_root: Path, vars_to_try=("T", "U", "p")) -> dict:
    """Extract inlet boundary condition values from initial-time field files.

    For each variable, finds the field file in 0/, 0.orig/ (in that order),
    parses its boundaryField block, and extracts uniform fixedValue values
    from patches whose name contains 'inlet' (case-insensitive).

    Returns:
        dict like {
            "T_inlet": 292.0,                 # min over all inlet patches
            "T_inlet_per_patch": {"inletfuel": 292.0, "inletair": 292.0},
            "U_inlet": 42.2,                  # max magnitude over inlet patches
            ...
        }
        Empty dict if no usable BCs found.
    """
    out: dict = {}
    initial_dirs = ("0", "0.orig")

    for var in vars_to_try:
        var_file = None
        for d in initial_dirs:
            candidate = case_root / d / var
            if candidate.is_file():
                var_file = candidate
                break
        if var_file is None:
            continue

        bc_block = read_foam_block(var_file, "boundaryField")
        if not bc_block:
            continue

        bc_dict = parse_block_to_dict(bc_block)

        per_patch: dict = {}
        for patch_name, patch_info in bc_dict.items():
            if not isinstance(patch_info, dict):
                continue
            if "inlet" not in patch_name.lower():
                continue
            bc_type = patch_info.get("type")
            if bc_type not in ("fixedValue", "uniformFixedValue", "inletOutlet"):
                continue
            value_str = patch_info.get("value") or patch_info.get("inletValue", "")
            value = parse_uniform_value(value_str if isinstance(value_str, str) else "")
            if value is None:
                continue
            per_patch[patch_name] = value

        if not per_patch:
            continue

        # Aggregate per-patch into a single representative inlet value
        if isinstance(next(iter(per_patch.values())), list):
            # Vector — use max magnitude (likely the actual jet/fuel)
            magnitudes = {p: sum(c * c for c in v) ** 0.5 for p, v in per_patch.items()}
            top_patch = max(magnitudes, key=magnitudes.get)
            out[f"{var}_inlet"] = magnitudes[top_patch]
            out[f"{var}_inlet_vector"] = per_patch[top_patch]
        else:
            # Scalar — use min (most likely cold/ambient inlet)
            out[f"{var}_inlet"] = min(per_patch.values())

        out[f"{var}_inlet_per_patch"] = per_patch

    return out


def _read_case_origin_path(case_root: Path, log_files: list) -> str | None:
    """Find 'Case :' line in any log file's banner."""
    for lf_rel in log_files:
        lf = case_root / lf_rel
        if not lf.is_file():
            continue
        try:
            with open(lf, "r", encoding="utf-8", errors="replace") as f:
                head = f.read(2048)
        except OSError:
            continue
        m = re.search(r"^Case\s*:\s*(.+)$", head, re.MULTILINE)
        if m:
            return m.group(1).strip()
    return None


def _coerce_value(v):
    """Try to convert string to int/float; otherwise leave as string."""
    if not isinstance(v, str):
        return v
    s = v.strip()
    # Try int
    try:
        return int(s)
    except ValueError:
        pass
    # Try float
    try:
        return float(s)
    except ValueError:
        pass
    return s
