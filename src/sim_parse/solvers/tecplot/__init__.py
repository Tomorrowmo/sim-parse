"""Tecplot solver — Path A (single-file primary).

Recognizes binary Tecplot (TDV magic header) and ASCII Tecplot
(TITLE/VARIABLES/ZONE keywords). Works on a single .plt / .dat file as
the primary input — there is no "case folder" concept in Tecplot.

Implementation has two layers:
    - Pure-Python header reader extracts variable names + zone summary
      from binary v75/v101+ files (works across versions VTK doesn't
      support, like TDV112 from Tecplot 360 EX).
    - vtkTecplotReader / vtkTecplotBinaryReader for full statistics when
      version-compatible. When the reader fails, Tier 1+2 still give
      useful metadata; Tier 4 emits an explicit warning.
"""
from sim_parse.core.registry import register_solver

from . import tier1_identify, tier2_inventory, tier3_metadata, tier4_field_stats

register_solver(
    "tecplot",
    identify=tier1_identify.identify,
    inventory=tier2_inventory.inventory,
    metadata=tier3_metadata.metadata,
    field_stats=tier4_field_stats.field_stats,
)
