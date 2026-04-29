"""Tecplot Tier 2 — Inventory (header parsing, no full data load)."""
from __future__ import annotations

from pathlib import Path

from sim_parse.core.errors import never_raise, safe_call
from sim_parse.core.provenance import (
    FieldProvenance,
    add_warning,
    init_tier_output,
    set_field,
)
from sim_parse.solvers.tecplot._header_parser import (
    TecplotHeaderError,
    parse_ascii_header,
    parse_binary_header,
)


@never_raise(default=None)
def inventory(case_root: Path, identity: dict) -> dict | None:
    out = init_tier_output("A")

    file_path = Path(identity.get("file_path", ""))
    if not file_path.is_file():
        add_warning(out, f"file not found: {file_path}")
        return out

    sub_format = identity.get("sub_format", "ascii")

    if sub_format.startswith("binary"):
        try:
            header = parse_binary_header(file_path)
        except TecplotHeaderError as e:
            add_warning(out, f"could not parse Tecplot binary header: {e}")
            header = {}
        except Exception as e:
            add_warning(out, f"binary header parse exception: {e}")
            header = {}
    else:
        header = safe_call(parse_ascii_header, file_path, default={}) or {}

    if not header:
        return out

    if header.get("title"):
        set_field(out, "title", header["title"],
                  FieldProvenance("A", "TITLE field in Tecplot file"))

    var_names = header.get("variables") or []
    set_field(out, "variables", list(var_names),
              FieldProvenance("A", "VARIABLES section parsed from Tecplot header"))

    # variables_meta — Tecplot has no role/scope distinction in the file;
    # everything is a field on the mesh. Tier 4 (when VTK reader works)
    # will refine to cell_scalar / cell_vector / point_scalar via rank.
    if var_names:
        vmeta = {n: {"kind": "cell_field",
                     "evidence": "declared in Tecplot VARIABLES section"}
                 for n in var_names}
        set_field(out, "variables_meta", vmeta,
                  FieldProvenance("A", "Tecplot VARIABLES list"))

    # Zones from header (count + names + zone-type tag if binary)
    zones = header.get("zones") or []
    if zones:
        set_field(out, "tecplot_zones", zones,
                  FieldProvenance("A", "ZONE entries from Tecplot header"))
        set_field(out, "n_zones", len(zones),
                  FieldProvenance("A", "len(zones)"))

    # Sub-format detail (binary version, byte order, file_type)
    detail = {
        "byte_order": header.get("byte_order"),
        "file_type": header.get("file_type"),  # 0=full, 1=grid only, 2=solution only
        "n_zones_seen": header.get("n_zones_seen"),
    }
    detail = {k: v for k, v in detail.items() if v is not None}
    if detail:
        set_field(out, "format_detail", detail,
                  FieldProvenance("A", "Tecplot binary header structural fields"))

    return out
