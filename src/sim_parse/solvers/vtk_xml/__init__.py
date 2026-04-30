"""VTK XML solver — Path A.

Covers VTK's XML-based file family (the formats ParaView writes):
    .vtu   UnstructuredGrid
    .vtp   PolyData
    .vts   StructuredGrid
    .vti   ImageData
    .vtr   RectilinearGrid
    .vtm   MultiBlockDataSet (collection)
    .pvtu  Parallel UnstructuredGrid (one .pvtu + N .vtu pieces)
    .pvtp / .pvts / ...

The XML header is the discriminator — every file starts with
'<VTKFile type="..."'. We pick the right reader by extension; tier 4
walks blocks identically across types.

Single-file primary input. No "case folder" concept.
"""
from sim_parse.core.registry import register_solver

from . import tier1_identify, tier2_inventory, tier3_metadata, tier4_field_stats

register_solver(
    "vtk_xml",
    identify=tier1_identify.identify,
    inventory=tier2_inventory.inventory,
    metadata=tier3_metadata.metadata,
    field_stats=tier4_field_stats.field_stats,
)
