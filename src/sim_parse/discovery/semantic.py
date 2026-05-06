"""Tier 7 semantic interpretation.

Phase 1: HEURISTIC engine (rule-based, deterministic). Reads tier 1-5 outputs
and produces semantic-layer fields (physics_class / nl_summary / time_semantics /
run_health / complexity_class / candidate_domains) WITHOUT calling an LLM.

Phase 3+: optional LLM upgrade — interpret() will detect a registered LLM
client and use RAG against sim-knowledge/physics; falls back to heuristic when
no client present.

This file is deliberately solver-agnostic: it works on whatever tier 1-5
shape is given, in line with design.md §11 mechanism #1 "结构化知识库" idea.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sim_parse.core.errors import never_raise


# Sibling sim-knowledge repo path (same trick as qoi_engine.py uses).
_SIM_KNOWLEDGE = Path(__file__).resolve().parents[3].parent / "sim-knowledge"
_DOMAIN_INFERENCE_YAML = _SIM_KNOWLEDGE / "physics" / "domain_inference.yaml"

# Cached after first read; cleared on process restart.
_CLUES_CACHE: list[dict] | None = None


@never_raise(default={})
def interpret(parse_result: dict) -> dict:
    """Generate Tier 7 semantic fields from a full parse_case result."""
    out: dict = {
        "_path": "C",  # Tier 7 lives in the LLM/Discovery side of the cascade
        "_provenance": {},
        "_errors": [],
        "_warnings": [],
    }

    # Heuristic plug-ins (each contributes 1 or more fields)
    _add_physics_class(out, parse_result)
    _add_time_semantics(out, parse_result)
    _add_run_health(out, parse_result)
    _add_complexity_class(out, parse_result)
    _add_candidate_domains(out, parse_result)
    _add_nl_summary(out, parse_result)

    return out


# ─── candidate_domains: routing via sim-knowledge YAML clue table ─────────────


@never_raise(default=[])
def _load_domain_inference_clues() -> list[dict]:
    """Read sim-knowledge/physics/domain_inference.yaml. Cached.

    Silent-fail on missing sibling repo or yaml — returns []. Zero-config
    consumers of sim-parse (no sim-knowledge installed) get an empty
    candidate_domains list, not an error.
    """
    global _CLUES_CACHE
    if _CLUES_CACHE is not None:
        return _CLUES_CACHE
    if not _DOMAIN_INFERENCE_YAML.is_file():
        _CLUES_CACHE = []
        return _CLUES_CACHE
    try:
        import yaml
    except ImportError:
        _CLUES_CACHE = []
        return _CLUES_CACHE
    try:
        with open(_DOMAIN_INFERENCE_YAML, encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        _CLUES_CACHE = list(doc.get("clues") or [])
    except Exception:
        _CLUES_CACHE = []
    return _CLUES_CACHE


def _add_candidate_domains(out: dict, r: dict) -> None:
    """Apply sim-knowledge clue table; emit ranked domain candidates.

    Output shape:
        candidate_domains: {
            value: [
                {"domain": "combustion", "score": 6,
                 "matched_clues": ["combustion-by-species", "combustion-by-application"]},
                {"domain": "fluid_dynamics", "score": 3, "matched_clues": [...]},
                ...
            ],
            confidence: HIGH/MED/LOW,
            source: "sim-knowledge/physics/domain_inference.yaml (N clues)",
        }
    """
    clues = _load_domain_inference_clues()
    if not clues:
        _set(out, "candidate_domains", {
            "value": [],
            "confidence": "LOW",
            "source": "sim-knowledge/physics/domain_inference.yaml not found",
        })
        return

    t1 = r.get("tier_1_identify", {}) or {}
    t2 = r.get("tier_2_inventory", {}) or {}
    t3 = r.get("tier_3_metadata", {}) or {}

    # Combine variable lists across tiers (different solvers fill different tiers).
    fields = set()
    for src in (t2.get("variables"), t3.get("variables")):
        for name in (src or []):
            if isinstance(name, str):
                fields.add(name)
    fields_lower = {f.lower() for f in fields}

    application = (t3.get("application") or t1.get("solver") or "").lower()

    # Boundary patch types (used by if_patch_type clues, none today)
    patch_types_lower = set()
    for b in (t3.get("boundaries") or []):
        if isinstance(b, dict):
            t = (b.get("type") or "").lower()
            if t:
                patch_types_lower.add(t)

    _CONFIDENCE_WEIGHT = {"strong": 3, "moderate": 2, "weak": 1}

    by_domain: dict[str, dict] = {}

    for clue in clues:
        if not isinstance(clue, dict):
            continue
        if not _clue_matches(clue, fields_lower, application, patch_types_lower):
            continue
        domain = clue.get("domain")
        if not domain:
            continue
        weight = _CONFIDENCE_WEIGHT.get(
            (clue.get("confidence") or "").lower(), 1)
        entry = by_domain.setdefault(domain, {
            "domain": domain, "score": 0, "matched_clues": [],
        })
        entry["score"] += weight
        entry["matched_clues"].append(clue.get("id") or "?")

    ranked = sorted(by_domain.values(), key=lambda e: -e["score"])

    if not ranked:
        confidence = "LOW"
    elif ranked[0]["score"] >= 5:
        confidence = "HIGH"
    else:
        confidence = "MED"

    _set(out, "candidate_domains", {
        "value": ranked,
        "confidence": confidence,
        "source": f"sim-knowledge/physics/domain_inference.yaml ({len(clues)} clues)",
    })


def _clue_matches(clue: dict, fields_lower: set[str],
                  application: str, patch_types_lower: set[str]) -> bool:
    """Evaluate one clue against the case signals. ALL specified conditions
    must match; missing conditions are treated as 'always satisfied'."""
    # if_any_field: any one of the listed names must be present
    any_fields = clue.get("if_any_field")
    if any_fields:
        wanted = {str(f).lower() for f in any_fields}
        if not (wanted & fields_lower):
            return False

    # if_all_fields: every listed name must be present
    all_fields = clue.get("if_all_fields")
    if all_fields:
        wanted = {str(f).lower() for f in all_fields}
        if not wanted.issubset(fields_lower):
            return False

    # if_no_fields: NONE of the listed names may be present (negative gate)
    no_fields = clue.get("if_no_fields")
    if no_fields:
        forbidden = {str(f).lower() for f in no_fields}
        if forbidden & fields_lower:
            return False

    # if_application_substring: app name must contain at least one
    app_subs = clue.get("if_application_substring")
    if app_subs:
        if not any(str(s).lower() in application for s in app_subs):
            return False

    # if_patch_type: at least one boundary patch must have one of these types
    patch_types = clue.get("if_patch_type")
    if patch_types:
        wanted = {str(t).lower() for t in patch_types}
        if not (wanted & patch_types_lower):
            return False

    return True


# ─── Heuristic engines ───────────────────────────────────────────────────────


def _add_physics_class(out: dict, r: dict) -> None:
    """Infer physics class(es) from solver / models / variables.

    Cross-solver: recognizes both OpenFOAM lowercase names (T, U, p, rho)
    and Fluent UPPERCASE names (TEMPERATURE, X_VELOCITY, PRESSURE, DENSITY, MACH).
    """
    classes = []

    t1 = r.get("tier_1_identify", {}) or {}
    t2 = r.get("tier_2_inventory", {}) or {}
    t3 = r.get("tier_3_metadata", {}) or {}
    t4 = r.get("tier_4_field_stats", {}) or {}

    solver = (t1.get("solver") or "").lower()
    fmt = (t1.get("format") or "").lower()
    has_chem = bool(t3.get("chemistry") and t3["chemistry"].get("enabled"))
    has_combustion = bool(t3.get("combustion"))
    has_lagrangian = bool(t1.get("lagrangian"))

    # Variable names — UPPERCASE-fold for cross-solver matching.
    # OpenFOAM puts variables list in Tier 2 (filename scan); Fluent puts it in Tier 3
    # (read via vtkFLUENTReader). Combine both sources.
    vars_list = list(t2.get("variables") or []) + list(t3.get("variables") or [])
    vars_upper = {v.upper().replace("-", "_") for v in vars_list if isinstance(v, str)}

    def _has_var(name_or_pattern):
        """Match UPPERCASE-folded variable names."""
        return name_or_pattern.upper() in vars_upper

    def _has_any_var(*patterns):
        return any(_has_var(p) for p in patterns)

    # Temperature: T (OpenFOAM), TEMPERATURE / STATIC_TEMPERATURE (Fluent)
    has_T = _has_any_var("T", "TEMPERATURE", "STATIC_TEMPERATURE", "TOTAL_TEMPERATURE")
    # Velocity
    has_U = _has_any_var("U", "VELOCITY", "VELOCITY_MAGNITUDE",
                         "X_VELOCITY", "Y_VELOCITY", "Z_VELOCITY")
    # Pressure
    has_p = _has_any_var("P", "PRESSURE", "STATIC_PRESSURE", "TOTAL_PRESSURE")
    # Density
    has_rho = _has_any_var("RHO", "DENSITY")
    # Mach
    has_Ma = _has_any_var("MA", "MACH", "MACH_NUMBER")
    # Species (combustion indicators)
    species_names = ("CH4", "CO", "CO2", "H2O", "OH", "H2", "MF_CO", "MF_CO2", "MF_OH")
    has_species = any(_has_var(s) for s in species_names)

    # ── Class assembly ────────────────────────────────────────────────────────
    # Combustion family
    if has_combustion or has_chem or has_species:
        classes.append("combustion")
    if "spray" in solver or has_lagrangian:
        classes.append("dispersed_phase")
    # Heat
    if has_T:
        classes.append("heat_transfer")
    # Flow (essentially always for CFD)
    if has_U:
        classes.append("flow")
    # Compressible
    if has_rho or has_Ma:
        classes.append("compressible")
    # Multi-region CHT
    if t1.get("layout") == "multi-region":
        classes.append("conjugate_heat_transfer")

    # Field-data-based regime detection (Tier 4 must be present)
    var_ranges = (t4 or {}).get("variable_ranges", {}) or {}
    regime = _infer_speed_regime(var_ranges)
    if regime:
        classes.append(regime)

    if not classes:
        classes = ["unknown"]

    if has_combustion or has_chem:
        confidence = "HIGH"
    elif classes != ["unknown"]:
        confidence = "MED"
    else:
        confidence = "LOW"

    _set(out, "physics_class", {
        "value": "+".join(classes),
        "confidence": confidence,
        "source": "heuristic from solver/chemistry/variables/Tier4 regime",
    })


def _infer_speed_regime(var_ranges: dict) -> str | None:
    """Infer compressibility regime from peak velocity magnitude.

    Looks at any velocity-like variable:
        - 'U' magnitude_max (OpenFOAM vector)
        - 'X_VELOCITY' / 'Y_VELOCITY' / 'Z_VELOCITY' max (Fluent components)
        - 'velocity-magnitude' max (Fluent CFF)
    """
    candidates = []
    if "U" in var_ranges and "magnitude_max" in (var_ranges["U"] or {}):
        candidates.append(var_ranges["U"]["magnitude_max"])
    for name in ("X_VELOCITY", "Y_VELOCITY", "Z_VELOCITY", "velocity-magnitude",
                 "VELOCITY_MAGNITUDE"):
        if name in var_ranges:
            stats = var_ranges[name]
            for k in ("magnitude_max", "max"):
                v = stats.get(k)
                if v is not None:
                    candidates.append(abs(v))
                    break
    if not candidates:
        return None
    u_max = max(candidates)

    # Speed of sound at room temp ~ 340 m/s; use that as proxy
    sound_speed = 340.0
    Ma_approx = u_max / sound_speed
    if Ma_approx < 0.3:
        return None             # incompressible — no regime tag
    if Ma_approx < 0.8:
        return "subsonic"
    if Ma_approx < 1.2:
        return "transonic"
    if Ma_approx < 5.0:
        return "supersonic"
    return "hypersonic"


def _add_time_semantics(out: dict, r: dict) -> None:
    """Identify whether the time axis is physical seconds, iterations, or LTS pseudo-time."""
    t1 = r.get("tier_1_identify", {}) or {}
    t3 = r.get("tier_3_metadata", {}) or {}
    solver = (t1.get("solver") or "")
    case_origin = (t3.get("case_origin_path") or "")
    tc = t3.get("time_control") or {}

    is_lts = "LTS" in solver or "LTS" in case_origin or "LTS" in str(case_origin)
    end_t = tc.get("endTime")
    delta_t = tc.get("deltaT")

    if is_lts:
        semantics = "pseudo_time_LTS"
        confidence = "HIGH"
        source = f"solver/path contains 'LTS'; endTime={end_t} treated as iteration count"
    elif "simple" in solver.lower():
        semantics = "iteration_pseudo_time"
        confidence = "HIGH"
        source = "SIMPLE-family steady-state solver — endTime is iteration count"
    elif solver and any(s in solver.lower() for s in ("piso", "pimple", "spray")):
        semantics = "physical_time"
        confidence = "HIGH"
        source = "transient solver name"
    elif end_t is not None and delta_t is not None:
        # Heuristic: integer endTime + integer deltaT often means iteration count
        try:
            if float(end_t).is_integer() and float(delta_t).is_integer() and float(delta_t) >= 1:
                semantics = "iteration_or_pseudo_time"
                confidence = "MED"
                source = "integer endTime/deltaT — likely iteration count, not seconds"
            else:
                semantics = "physical_time"
                confidence = "MED"
                source = "fractional endTime/deltaT"
        except (TypeError, ValueError):
            semantics = "unknown"
            confidence = "LOW"
            source = "could not parse time control"
    else:
        semantics = "unknown"
        confidence = "LOW"
        source = "no time-control hints found"

    _set(out, "time_semantics", {
        "value": semantics, "confidence": confidence, "source": source,
    })


def _add_run_health(out: dict, r: dict) -> None:
    """Combine Tier 5 booleans + completeness into a single health verdict.

    Cross-solver: handles OpenFOAM (has is_completed) and Fluent (uses Tier 4
    presence + transcript summary as a proxy).
    """
    t1 = r.get("tier_1_identify", {}) or {}
    t3 = r.get("tier_3_metadata", {}) or {}
    t4 = r.get("tier_4_field_stats", {}) or {}
    qoi = r.get("tier_5_qoi") or []

    qoi_by_name = {q["variable"]: q for q in qoi if isinstance(q, dict) and "variable" in q}
    is_diverged = qoi_by_name.get("is_diverged", {}).get("value")
    is_completed = t3.get("is_completed")           # OpenFOAM-only (vs endTime)
    is_extinguished = qoi_by_name.get("is_extinguished", {}).get("value")

    has_errors = bool(t4.get("_errors") or r.get("errors"))
    has_field_data = bool(t4.get("variable_ranges"))   # Tier 4 successfully read fields
    has_results = t1.get("has_paired_results", True)   # Fluent: .dat present?

    # Verdict logic
    if is_diverged is True:
        verdict, conf, source = "diverged", "HIGH", "Tier 5 is_diverged=True"
    elif has_errors:
        verdict, conf, source = "concerning", "MED", "Tier 4 produced errors"
    elif is_completed is True and is_diverged is False:
        verdict, conf, source = "healthy", "HIGH", "completed + not diverged"
        if is_extinguished is True and "combustion" in str(out.get("physics_class", {}).get("value", "")):
            verdict, conf, source = "completed_but_extinguished", "MED", "flame went out"
    elif is_completed is False:
        verdict, conf, source = "incomplete", "MED", "latest_time < endTime"
    elif is_completed is None and has_field_data and has_results:
        # Fluent / format without explicit completion field; use Tier 4 success as proxy
        verdict, conf, source = "healthy", "MED", "field stats extracted; not diverged"
    else:
        verdict, conf, source = "unknown", "LOW", "insufficient signals"

    _set(out, "run_health", {
        "value": verdict, "confidence": conf, "source": source,
    })


def _add_complexity_class(out: dict, r: dict) -> None:
    """Classify case complexity by mesh size + physics richness.

    Physics richness uses a cross-solver score:
      OpenFOAM-style: turbulence/chemistry/combustion/lagrangian/mixture dicts
      Fluent-style:   variable count / cell zone count / regime / report files
    """
    t1 = r.get("tier_1_identify", {}) or {}
    t2 = r.get("tier_2_inventory", {}) or {}
    t3 = r.get("tier_3_metadata", {}) or {}
    t4 = r.get("tier_4_field_stats", {}) or {}

    cells = t3.get("mesh_cells") or 0

    if cells < 1_000:
        size = "tiny"
    elif cells < 100_000:
        size = "small"
    elif cells < 5_000_000:
        size = "medium"
    elif cells < 50_000_000:
        size = "large"
    else:
        size = "huge"

    score = 0

    # OpenFOAM-style indicators
    if t3.get("turbulence"):
        score += 1
    if t3.get("chemistry"):
        score += 2
    if t3.get("combustion"):
        score += 1
    if t1.get("lagrangian"):
        score += 2
    if t3.get("thermophysics", {}).get("thermoType", {}).get("mixture"):
        score += 1

    # Fluent-style / general indicators
    n_variables = t3.get("n_variables") or len(t2.get("variables") or [])
    if n_variables >= 10:
        score += 1                               # rich field set
    if n_variables >= 20:
        score += 1
    n_zones = t3.get("n_cell_zones") or 0
    if n_zones >= 3:
        score += 1                               # multi-zone
    # Compressibility flag from Tier 7 physics_class so far
    pc_value = (out.get("physics_class") or {}).get("value", "")
    if "compressible" in pc_value or "supersonic" in pc_value or "hypersonic" in pc_value or "transonic" in pc_value:
        score += 1
    # Report files (post-processing setup)
    if t4.get("report_qoi") and len(t4["report_qoi"]) >= 3:
        score += 1

    physics_label = "simple" if score <= 1 else "moderate" if score <= 3 else "complex"

    _set(out, "complexity_class", {
        "value": f"{size}_{physics_label}",
        "confidence": "HIGH",
        "source": f"mesh_cells={cells}, physics_score={score}",
    })


def _add_nl_summary(out: dict, r: dict) -> None:
    """Generate a 1-sentence Chinese natural-language summary.

    Cross-solver: OpenFOAM uses turbulence/combustion/chemistry dicts;
    Fluent uses tier 7 physics_class + Tier 5 force coefficients.
    """
    t1 = r.get("tier_1_identify", {}) or {}
    t3 = r.get("tier_3_metadata", {}) or {}
    qoi = r.get("tier_5_qoi") or []
    qoi_by_name = {q["variable"]: q for q in qoi if isinstance(q, dict) and "variable" in q}

    parts = []

    # Solver + version
    solver = t1.get("solver") or t1.get("format") or "未知求解器"
    version = t1.get("version") or t3.get("transcript_summary", {}).get("fluent_version") or "?"
    parts.append(f"{solver} ({version})")

    # Mesh
    cells = t3.get("mesh_cells")
    if cells:
        if cells < 100_000:
            mesh_str = f"网格 {cells} 单元（小型）"
        elif cells < 1_000_000:
            mesh_str = f"网格 {cells//1000}k 单元"
        else:
            mesh_str = f"网格 {cells//1_000_000}M 单元"
        parts.append(mesh_str)

    # Turbulence (OpenFOAM-style)
    turb = t3.get("turbulence") or {}
    if turb:
        sim_type = turb.get("simulationType")
        model = turb.get("RASModel") or turb.get("LESModel") or turb.get("model")
        if sim_type and model:
            parts.append(f"{sim_type}/{model}")

    # Combustion (OpenFOAM-style)
    comb = t3.get("combustion")
    if comb and comb.get("combustionModel"):
        parts.append(f"{comb['combustionModel']} 燃烧")
    chem = t3.get("chemistry")
    if chem and chem.get("foamChemistryFile"):
        f = chem.get("foamChemistryFile", "")
        if "GRI" in f.upper() or "grimech" in f.lower():
            parts.append("GRI-Mech 化学")

    # Physics class (cross-solver)
    pc_value = (out.get("physics_class") or {}).get("value", "")
    if pc_value and pc_value != "unknown":
        # show the regime if compressible
        for regime in ("hypersonic", "supersonic", "transonic", "subsonic"):
            if regime in pc_value:
                parts.append(regime)
                break

    # QOI summary
    T_max = qoi_by_name.get("max_temperature", {}).get("value")
    U_max = qoi_by_name.get("max_velocity_magnitude", {}).get("value")
    Qdot_max = qoi_by_name.get("max_heat_release_rate", {}).get("value")
    Cl = qoi_by_name.get("lift_coefficient_last", {}).get("value")
    Cd = qoi_by_name.get("drag_coefficient_last", {}).get("value")

    if T_max:
        parts.append(f"T 峰 {T_max:.0f} K")
    if Qdot_max and Qdot_max > 0:
        parts.append(f"Qdot 峰 {Qdot_max:.2e} W/m³")
    if U_max and U_max > 1:
        parts.append(f"|U| 峰 {U_max:.0f} m/s")
    if Cl is not None and Cd is not None:
        parts.append(f"CL={Cl:.3g}, CD={Cd:.3g}")

    # Run health
    rh_val = (out.get("run_health") or {}).get("value")
    if rh_val and rh_val != "unknown":
        parts.append(f"状态：{rh_val}")

    summary = "；".join(parts) + "。"
    _set(out, "nl_summary", {
        "value": summary,
        "confidence": "HIGH",
        "source": "template + Tier 3/5/7 fields",
    })


# ─── Helper ───────────────────────────────────────────────────────────────────


def _set(out: dict, key: str, value: Any) -> None:
    out[key] = value
