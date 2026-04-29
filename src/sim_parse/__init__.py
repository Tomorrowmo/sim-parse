"""sim-parse — Universal CAE simulation result parser.

Public API:
    parse_case(case_root, target_tier=4, ...) -> dict
    to_vtu(case_root, time=None, fields=None, output=None) -> Path
    register_parser(name, ...) -> decorator
"""
from pathlib import Path

from sim_parse.core.cascade import parse_case
from sim_parse.core.registry import get_solver, list_solvers, register_parser


def to_vtu(case_root, time=None, fields=None, output=None):
    """Export a case to VTU (Tier 6).

    Args:
        case_root: case directory.
        time: select this time step (default: latest).
        fields: variable subset (default: solver-specific common set).
        output: output VTU path (default: <case>/.simparse_cache/...).

    Returns:
        Path to the written VTU file, or None on failure.
    """
    case_root = Path(case_root)
    # Identify first to find the right solver
    result = parse_case(case_root, target_tier=1)
    fmt = result.get("tier_1_identify", {}).get("format")
    if not fmt:
        return None
    bundle = get_solver(fmt)
    if not bundle or "export_vtu" not in bundle:
        return None
    export_result = bundle["export_vtu"](
        case_root, result["tier_1_identify"],
        time=time, fields=fields, output=output,
    )
    if not export_result or not export_result.get("vtu_path"):
        return None
    return Path(export_result["vtu_path"])


__version__ = "0.1.0"
__all__ = ["parse_case", "to_vtu", "register_parser", "list_solvers", "__version__"]


# Auto-register built-in solver parsers on import
from sim_parse.solvers import openfoam as _openfoam_module  # noqa: F401, E402
from sim_parse.solvers import fluent as _fluent_module  # noqa: F401, E402
from sim_parse.solvers import ensight as _ensight_module  # noqa: F401, E402
from sim_parse.solvers import tecplot as _tecplot_module  # noqa: F401, E402
