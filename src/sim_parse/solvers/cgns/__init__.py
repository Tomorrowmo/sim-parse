"""CGNS solver — Path A.

Recognizes:
    - .cgns files (HDF5-CGNS via top-level 0x89HDF magic + CGNSBase_* group;
      ADF-CGNS via 'ADF Database Version' header at offset 4)
    - directories containing exactly one .cgns file

Two CGNS subtypes coexist in the wild:
    1. HDF5-based CGNS (modern; vtkCGNSReader handles cleanly)
    2. ADF-based CGNS (legacy; vtkCGNSReader support is patchy/disabled
       in many VTK builds — we detect it but degrade T3/T4 gracefully).

Tier 1+2 always succeed (identification + file inspection); Tier 3+4
depend on vtkCGNSReader behavior on the specific file.
"""
from sim_parse.core.registry import register_solver

from . import tier1_identify, tier2_inventory, tier3_metadata, tier4_field_stats

register_solver(
    "cgns",
    identify=tier1_identify.identify,
    inventory=tier2_inventory.inventory,
    metadata=tier3_metadata.metadata,
    field_stats=tier4_field_stats.field_stats,
)
