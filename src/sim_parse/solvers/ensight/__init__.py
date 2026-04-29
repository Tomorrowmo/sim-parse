"""EnSight Gold solver — Path A.

Recognizes:
    - directory containing one *.case file (+ companion .geo / data files)
    - a single *.case file passed directly

The .case file is the entry point: ASCII text with FORMAT, GEOMETRY (model),
and VARIABLE sections that name the per-field data files.
"""
from sim_parse.core.registry import register_solver

from . import tier1_identify, tier2_inventory, tier3_metadata, tier4_field_stats

register_solver(
    "ensight_gold",
    identify=tier1_identify.identify,
    inventory=tier2_inventory.inventory,
    metadata=tier3_metadata.metadata,
    field_stats=tier4_field_stats.field_stats,
)
