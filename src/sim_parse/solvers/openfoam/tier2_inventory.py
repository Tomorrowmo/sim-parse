"""OpenFOAM Tier 2 — Inventory.

Lists what's in the case directory without decoding any data:
    - time steps (numeric subdirs)
    - variables (file names in time directories)
    - scripts (Allrun, Allclean, Allmesh)
    - log files (top-level + processor*)
    - system files (known dicts in system/)
    - auxiliary (chemkin/, constant/triSurface/, ...)
    - post-processing (postProcessing/<name>/)
    - lagrangian clouds
"""
from __future__ import annotations

from pathlib import Path

from sim_parse.core.errors import never_raise
from sim_parse.core.provenance import (
    FieldProvenance,
    init_tier_output,
    set_field,
)


_KNOWN_SCRIPT_NAMES = {"Allrun", "Allclean", "Allmesh", "Allrun-parallel", "Allrun.pre"}
_KNOWN_SYSTEM_DICTS = {
    "controlDict", "fvSchemes", "fvSolution", "decomposeParDict",
    "blockMeshDict", "snappyHexMeshDict", "topoSetDict", "setFieldsDict",
    "setSetsDict", "fvOptions", "createPatchDict", "extrudeMeshDict",
    "cuttingPlane", "lineProbe", "massFlowMonitor", "sample", "sampling",
}

# Names that, in OpenFOAM convention, are not cell-centered physical scalars.
# Used by _classify_variable() to label fields as "diagnostic" / "face_flux" /
# "template" so downstream consumers can filter them out before treating the
# inventory list as a list of physical quantities.
_DIAGNOSTIC_FIELD_NAMES = {
    # Tabulation / chemistry diagnostics
    "TabulationResults",
    "cellCpuTimes",
    # Mesh / solver internals
    "cellID",
    "cellLevel",
    "pointLevel",
    "rDeltaT",
    "CourantNumber",
    "yPlus",
    # Reaction-rate diagnostics from PaSR / EDC etc. that share Qdot's namespace
    # are NOT in this list because they ARE physical quantities; only purely
    # diagnostic / non-physical fields belong here.
}

# Files whose name pattern indicates a face flux rather than a cell field.
# vtkOpenFOAMReader does not expose these via GetCellArrayName(), so they
# would be silently dropped at Tier 4 anyway; Tier 2 still labels them
# explicitly so the 'variables' list stays interpretable.
_FACE_FLUX_PREFIXES = ("phi",)

# Files that act as default-value templates in reactingMixture / sub-region
# configs — present in initial conditions but not real cell-data scalars.
_TEMPLATE_FIELD_NAMES = {
    "Ydefault",  # reactingMixture default mass-fraction template
}


@never_raise(default=None)
def inventory(case_root: Path, identity: dict) -> dict | None:
    case_root = Path(case_root)
    out = init_tier_output("A")

    # 1. Time steps — top-level numeric directory names (excluding 0.orig and similar)
    time_steps = _scan_time_directories(case_root)
    set_field(out, "time_steps", time_steps,
              FieldProvenance("A", "top-level numeric directories"))
    set_field(out, "n_time_steps", len(time_steps),
              FieldProvenance("A", "len(time_steps)"))
    set_field(out, "latest_time", max(time_steps) if time_steps else None,
              FieldProvenance("A", "max(time_steps)"))

    # 2. Variables — union of file names across all time dirs (excluding lagrangian/uniform/)
    #    Plus per-variable kind classification so downstream can filter phantom
    #    / non-physical entries out of the list.
    initial_dir_names = _initial_condition_dir_names(case_root)
    variables_per_time = _collect_variables_per_time(case_root, time_steps, initial_dir_names)
    variables_sorted = sorted(variables_per_time.keys())
    set_field(out, "variables", variables_sorted,
              FieldProvenance("A", "union of files in time directories"))
    variables_meta = _classify_variables(variables_per_time, time_steps, initial_dir_names)
    set_field(out, "variables_meta", variables_meta,
              FieldProvenance("A", "per-variable kind classification (file presence + name heuristics)"))

    # 3. Scripts — Allrun, Allclean, etc.
    scripts = sorted(
        f.name for f in case_root.iterdir()
        if f.is_file() and f.name in _KNOWN_SCRIPT_NAMES
    )
    set_field(out, "scripts", scripts,
              FieldProvenance("A", "top-level Allrun/Allclean/... files"))

    # 4. Log files — top-level + processor*
    log_files = _scan_log_files(case_root)
    set_field(out, "log_files", log_files,
              FieldProvenance("A", "log.* in top-level and processor*/"))

    # 5. System files — known dicts in system/
    system_dir = case_root / "system"
    if system_dir.is_dir():
        system_files = sorted(
            f.name for f in system_dir.iterdir()
            if f.is_file() and (f.name in _KNOWN_SYSTEM_DICTS or _looks_like_dict(f))
        )
    else:
        system_files = []
    set_field(out, "system_files", system_files,
              FieldProvenance("A", "system/ dictionary files"))

    # 6. Auxiliary — chemkin/, constant/triSurface/, README*, etc.
    auxiliary = _scan_auxiliary(case_root)
    set_field(out, "auxiliary", auxiliary,
              FieldProvenance("A", "chemkin/, triSurface/, README*"))

    # 7. Post-processing
    post_proc_dir = case_root / "postProcessing"
    if post_proc_dir.is_dir():
        post_processing = sorted(d.name for d in post_proc_dir.iterdir() if d.is_dir())
    else:
        post_processing = []
    set_field(out, "post_processing", post_processing,
              FieldProvenance("A", "postProcessing/ subdirectories"))

    # 8. Regions (multi-region only)
    if identity.get("layout") == "multi-region":
        constant_dir = case_root / "constant"
        regions = sorted([
            d.name for d in constant_dir.iterdir()
            if d.is_dir() and (d / "polyMesh").is_dir()
        ])
        set_field(out, "regions", regions,
                  FieldProvenance("A", "constant/<region>/polyMesh/"))

    # 9. Lagrangian clouds (if any time has lagrangian/)
    if identity.get("lagrangian"):
        clouds = _scan_lagrangian_clouds(case_root, time_steps)
        set_field(out, "lagrangian_clouds", clouds,
                  FieldProvenance("A", "<time>/lagrangian/<cloud>/ subdirs"))

    # 10. Lifecycle hint: are there 0.orig or similar?
    if initial_dir_names:
        set_field(out, "initial_condition_dirs", initial_dir_names,
                  FieldProvenance("A", "0.orig/initial/... directories"))

    return out


def _scan_time_directories(case_root: Path) -> list[float]:
    """Return sorted list of numeric time-directory names (as floats).

    Excludes 0.orig, 0_orig, etc. — only pure numeric names.
    """
    times: list[float] = []
    for entry in case_root.iterdir():
        if not entry.is_dir():
            continue
        try:
            t = float(entry.name)
            times.append(t)
        except ValueError:
            continue
    times.sort()
    return times


def _collect_variables_per_time(
    case_root: Path,
    time_steps: list[float],
    initial_dir_names: list[str],
) -> dict[str, list]:
    """Map each variable name → list of time-dir labels where the file appears.

    A "time-dir label" is the float for numeric time dirs, or the string name
    for non-numeric initial-condition dirs (e.g. "0.orig").
    """
    per_time: dict[str, list] = {}

    # Numeric time dirs
    for t in time_steps:
        candidates = [str(t), _format_time(t)]
        for cand in candidates:
            tdir = case_root / cand
            if tdir.is_dir():
                for f in tdir.iterdir():
                    if f.is_file():
                        name = f.name
                        if name.endswith(".gz"):
                            name = name[:-3]
                        per_time.setdefault(name, []).append(t)
                break

    # Non-numeric initial-condition dirs (0.orig / initial / ...)
    for ic_name in initial_dir_names:
        ic_dir = case_root / ic_name
        if not ic_dir.is_dir():
            continue
        for f in ic_dir.iterdir():
            if f.is_file():
                name = f.name
                if name.endswith(".gz"):
                    name = name[:-3]
                per_time.setdefault(name, []).append(ic_name)

    return per_time


def _initial_condition_dir_names(case_root: Path) -> list[str]:
    return sorted(
        d.name for d in case_root.iterdir()
        if d.is_dir() and d.name in {"0.orig", "0_orig", "initial"}
    )


def _classify_variables(
    variables_per_time: dict[str, list],
    time_steps: list[float],
    initial_dir_names: list[str],
) -> dict[str, dict]:
    """Heuristic kind label for each file in the time directories.

    Returns:
        {var_name: {kind, times_present, evidence}}

    Kinds:
        cell_field  — default for unknown names; a real per-cell field
                      (Tier 4 will refine to cell_scalar / cell_vector / cell_tensor
                      via vtkOpenFOAMReader).
        face_flux   — face-defined quantity (phi / phi_<name>); not a cell scalar.
        diagnostic  — solver internal / non-physical (TabulationResults, rDeltaT, ...).
        template    — config template (Ydefault) — not a real field.
        ic_only     — present in initial-condition dir(s) only, absent in
                      every numeric latestTime — likely an unused initial value
                      file (e.g. G when radiation is off, Ydefault).

    Each label is heuristic and traceable; downstream code MUST cross-check
    against vtkOpenFOAMReader.GetCellArrayName() to confirm before using.
    """
    latest_time = max(time_steps) if time_steps else None

    def appears_in_latest(times: list) -> bool:
        if latest_time is None:
            return False
        return any(isinstance(t, float) and t == latest_time for t in times)

    def appears_in_any_numeric(times: list) -> bool:
        return any(isinstance(t, float) for t in times)

    out: dict[str, dict] = {}
    for name, times in variables_per_time.items():
        # times is a mix of floats (numeric dirs) and strings (initial-only dirs)
        kind: str
        evidence: str

        if name in _TEMPLATE_FIELD_NAMES:
            kind = "template"
            evidence = f"name '{name}' matches reactingMixture template-field convention"
        elif name in _DIAGNOSTIC_FIELD_NAMES:
            kind = "diagnostic"
            evidence = f"name '{name}' matches diagnostic-field whitelist"
        elif _is_face_flux_name(name):
            kind = "face_flux"
            evidence = f"name '{name}' starts with face-flux prefix {_FACE_FLUX_PREFIXES}"
        elif not appears_in_any_numeric(times) and times:
            # File present ONLY in non-numeric initial-condition dirs (0.orig
            # / initial / ...). Numeric timesteps don't have it at all, so
            # it almost certainly was never advanced by the solver.
            kind = "ic_only"
            evidence = (
                f"file present only in initial-condition dirs {sorted(times)}, "
                f"no numeric timestep contains it"
            )
        else:
            # Default: anything that appears in at least one numeric timestep
            # is provisionally a cell field. Tier 4 will verify via VTK.
            # NOTE: we deliberately do NOT mark "present in 0/ but absent in
            # latestTime/" as ic_only — OpenFOAM keeps unchanged fields
            # available through time advance without rewriting them, and
            # vtkOpenFOAMReader still exposes them as valid cell arrays.
            kind = "cell_field"
            evidence = "default classification; refine via VTK in Tier 4"

        out[name] = {
            "kind": kind,
            "times_present": [str(t) for t in times],
            "evidence": evidence,
        }
    return out


def _is_face_flux_name(name: str) -> bool:
    """Match phi / phi_<species> exactly. Conservative — don't catch unrelated
    names that happen to start with 'phi'."""
    if name in _FACE_FLUX_PREFIXES:
        return True
    for prefix in _FACE_FLUX_PREFIXES:
        if name.startswith(prefix + "_") or name.startswith(prefix + "."):
            return True
    return False


def _format_time(t: float) -> str:
    """OpenFOAM time directory name format: integer floats are bare ('5000', not '5000.0')."""
    if t == int(t):
        return str(int(t))
    return str(t)


def _scan_log_files(case_root: Path) -> list[str]:
    """Find log files at top level and in processor* dirs."""
    found: list[str] = []
    # Top-level
    for f in case_root.iterdir():
        if f.is_file() and f.name.startswith("log."):
            found.append(f.name)
    # processor*/
    for d in case_root.iterdir():
        if d.is_dir() and d.name.startswith("processor"):
            for f in d.iterdir():
                if f.is_file() and f.name.startswith("log."):
                    found.append(f"{d.name}/{f.name}")
    return sorted(found)


def _looks_like_dict(path: Path) -> bool:
    """Quick heuristic: file is a FoamFile dictionary (has FoamFile header)."""
    try:
        with open(path, "rb") as f:
            head = f.read(512)
        return b"FoamFile" in head
    except OSError:
        return False


def _scan_auxiliary(case_root: Path) -> list[str]:
    """List auxiliary resources (relative paths)."""
    out: list[str] = []
    candidates = [
        case_root / "chemkin",
        case_root / "constant" / "triSurface",
        case_root / "constant" / "geometry",
    ]
    for c in candidates:
        if c.exists():
            out.append(str(c.relative_to(case_root)).replace("\\", "/"))
    # README and notes
    for f in case_root.iterdir():
        if f.is_file() and f.name.lower().startswith(("readme", "notes")):
            out.append(f.name)
    return sorted(out)


def _scan_lagrangian_clouds(case_root: Path, time_steps: list[float]) -> list[str]:
    """List unique cloud names across time dirs."""
    clouds: set[str] = set()
    for t in time_steps:
        for tname in (str(t), _format_time(t)):
            ldir = case_root / tname / "lagrangian"
            if ldir.is_dir():
                for d in ldir.iterdir():
                    if d.is_dir():
                        clouds.add(d.name)
                break
    return sorted(clouds)
