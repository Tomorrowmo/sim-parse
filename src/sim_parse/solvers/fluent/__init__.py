"""Fluent solver Path A implementation.

Handles three format variants:
    - Legacy binary: <case>.cas + <case>.dat (V18 and earlier)
    - Legacy gzipped: <case>.cas.gz + <case>.dat.gz
    - CFF / HDF5: <case>.cas.h5 + <case>.dat.h5 (V19+)

Tier 1 (identify) is pure-Python: looks at file extension + magic.
Tier 2 (inventory) reads file pair info + supplementary files (.trn, .out).
Tier 3+ uses vtkFLUENTReader / vtkFLUENTCFFReader for content extraction.
"""
from sim_parse.core.registry import register_solver
from sim_parse.solvers.fluent.tier1_identify import identify
from sim_parse.solvers.fluent.tier2_inventory import inventory
from sim_parse.solvers.fluent.tier3_metadata import metadata
from sim_parse.solvers.fluent.tier4_field_stats import field_stats
from sim_parse.solvers.fluent.tier6_export import export_vtu

register_solver(
    name="fluent",
    identify=identify,
    inventory=inventory,
    metadata=metadata,
    field_stats=field_stats,
    export_vtu=export_vtu,
    priority=70,                   # below openfoam (80, directory check is most certain)
)
