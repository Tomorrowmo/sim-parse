"""OpenFOAM solver Path A implementation.

Tier 1+2+3 are pure-Python text/header parsing (no VTK).
Tier 4+ require VTK (lazy-imported when called).
"""
from sim_parse.core.registry import register_solver
from sim_parse.solvers.openfoam.tier1_identify import identify
from sim_parse.solvers.openfoam.tier2_inventory import inventory
from sim_parse.solvers.openfoam.tier3_metadata import metadata
from sim_parse.solvers.openfoam.tier4_field_stats import field_stats
from sim_parse.solvers.openfoam.tier5_qoi import qoi
from sim_parse.solvers.openfoam.tier6_export import export_vtu

register_solver(
    name="openfoam",
    identify=identify,
    inventory=inventory,
    metadata=metadata,
    field_stats=field_stats,
    qoi=qoi,
    export_vtu=export_vtu,
    priority=80,  # high priority — directory-based formats checked early
)
