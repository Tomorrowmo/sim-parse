"""Plot3D Tier 2 — Inventory."""
from __future__ import annotations

from pathlib import Path

from sim_parse.core.errors import never_raise
from sim_parse.core.provenance import (
    FieldProvenance,
    init_tier_output,
    set_field,
)


# The 5 canonical Q variables in standard Plot3D solution files. There's
# no in-file metadata — these are convention.
_PLOT3D_Q_VARIABLES = ["Density", "Momentum_X", "Momentum_Y", "Momentum_Z", "StagnationEnergy"]


@never_raise(default=None)
def inventory(case_root: Path, identity: dict) -> dict | None:
    out = init_tier_output("A")
    xyz_path = identity.get("xyz_path")
    q_path = identity.get("q_path")
    has_q = identity.get("has_paired_solution", False)

    set_field(out, "xyz_path", xyz_path,
              FieldProvenance("A", "from Tier 1"))
    if q_path:
        set_field(out, "q_path", q_path,
                  FieldProvenance("A", "from Tier 1"))

    if has_q:
        set_field(out, "variables", list(_PLOT3D_Q_VARIABLES),
                  FieldProvenance("A",
                      "canonical Plot3D Q-variable convention (Density, Momentum_X/Y/Z, StagnationEnergy)"))
        set_field(out, "variables_meta", {
            v: {"kind": "cell_field",
                "evidence": "Plot3D Q file convention"}
            for v in _PLOT3D_Q_VARIABLES
        }, FieldProvenance("A", "Plot3D Q convention"))
    else:
        set_field(out, "variables", [],
                  FieldProvenance("A", "no .q file — mesh only"))

    return out
