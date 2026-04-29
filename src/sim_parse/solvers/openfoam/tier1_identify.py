"""OpenFOAM Tier 1 — Identify.

Looks for the canonical directory structure:
    <case>/system/controlDict   (mandatory)
    <case>/constant/            (mandatory)

Then extracts solver / version / layout / parallelism / lagrangian flags.
"""
from __future__ import annotations

from pathlib import Path

from sim_parse.core.errors import never_raise
from sim_parse.core.provenance import (
    FieldProvenance,
    add_warning,
    init_tier_output,
    set_field,
)
from sim_parse.solvers.openfoam._foam_dict import (
    read_foam_file_version,
    read_foam_value,
)


@never_raise(default=None)
def identify(case_root: Path) -> dict | None:
    """Return identification dict if this is an OpenFOAM case; None otherwise."""
    case_root = Path(case_root)
    control_dict = case_root / "system" / "controlDict"
    constant_dir = case_root / "constant"

    if not control_dict.is_file() or not constant_dir.is_dir():
        return None  # not an OpenFOAM case

    out = init_tier_output("A")
    set_field(out, "format", "openfoam",
              FieldProvenance("A", "system/+constant/ directory signature"))

    # Solver: from controlDict::application
    application = read_foam_value(control_dict, "application")
    if application:
        set_field(out, "solver", application,
                  FieldProvenance("A", "system/controlDict::application"))
    else:
        set_field(out, "solver", None)
        add_warning(out, "controlDict::application not found")

    # Version: from FoamFile banner of any dict file
    version = (
        read_foam_file_version(control_dict)
        or _scan_other_dicts_for_version(case_root)
    )
    if version:
        set_field(out, "version", version,
                  FieldProvenance("A", "FoamFile banner Version: field"))
    else:
        set_field(out, "version", None)

    # Layout: single-region vs multi-region
    polymesh = constant_dir / "polyMesh"
    if polymesh.is_dir():
        layout = "single-region"
    else:
        # multi-region: look for constant/<region>/polyMesh/
        regions = [d.name for d in constant_dir.iterdir()
                   if d.is_dir() and (d / "polyMesh").is_dir()]
        layout = "multi-region" if regions else "unknown"
        if regions:
            out["regions"] = regions
    set_field(out, "layout", layout,
              FieldProvenance("A", "presence of constant/polyMesh/ vs constant/<region>/polyMesh/"))

    # Decomposed: presence of processor* dirs
    decomposed = any(
        p.is_dir() and p.name.startswith("processor") and p.name[9:].isdigit()
        for p in case_root.iterdir()
    )
    set_field(out, "decomposed", decomposed,
              FieldProvenance("A", "processor* directories"))

    # Lagrangian: any time directory has lagrangian/ subdir
    lagrangian = _any_lagrangian(case_root)
    set_field(out, "lagrangian", lagrangian,
              FieldProvenance("A", "<time>/lagrangian/ directory"))

    return out


def _scan_other_dicts_for_version(case_root: Path) -> str | None:
    """Try other common dict files for version banner."""
    candidates = [
        case_root / "constant" / "turbulenceProperties",
        case_root / "constant" / "thermophysicalProperties",
        case_root / "system" / "fvSchemes",
        case_root / "system" / "fvSolution",
    ]
    for c in candidates:
        if c.is_file():
            v = read_foam_file_version(c)
            if v:
                return v
    return None


def _any_lagrangian(case_root: Path) -> bool:
    """Check if any top-level numeric time directory has a lagrangian/ subdir."""
    for entry in case_root.iterdir():
        if not entry.is_dir():
            continue
        # Time directory: name parses as float
        try:
            float(entry.name)
        except ValueError:
            continue
        if (entry / "lagrangian").is_dir():
            return True
    return False
