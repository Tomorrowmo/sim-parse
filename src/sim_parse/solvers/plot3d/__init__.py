"""Plot3D solver — Path A.

NASA Plot3D is a multi-block structured-grid format. Files come as:
    <case>.xyz     mesh (multi-block coordinates)
    <case>.q       solution (Q variables: rho, rho-u, rho-v, rho-w, rho-E)
    <case>.f       function (optional auxiliary scalar fields)

Sometimes packed in single combined files. Identification is harder
than CGNS — there's no magic header — so we go by extension AND
existence of a paired file.
"""
from sim_parse.core.registry import register_solver

from . import tier1_identify, tier2_inventory, tier3_metadata, tier4_field_stats

register_solver(
    "plot3d",
    identify=tier1_identify.identify,
    inventory=tier2_inventory.inventory,
    metadata=tier3_metadata.metadata,
    field_stats=tier4_field_stats.field_stats,
)
