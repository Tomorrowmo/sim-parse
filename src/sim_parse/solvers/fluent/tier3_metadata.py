"""Fluent Tier 3 — Metadata.

Uses vtkFLUENTReader / vtkFLUENTCFFReader's UpdateInformation() to peek at
mesh + array structure WITHOUT reading the full field data. This gives us:
    - cell zones (count, types)
    - face zones (boundary patches)
    - available cell data array names (variables)
    - mesh cell/point count

Falls back gracefully if VTK unavailable or reader fails — sets None fields.
"""
from __future__ import annotations

from pathlib import Path

from sim_parse.adapters.vtk_io import require_vtk
from sim_parse.core.errors import never_raise, safe_call
from sim_parse.core.provenance import (
    FieldProvenance,
    add_warning,
    init_tier_output,
    set_field,
)


@never_raise(default=None)
def metadata(case_root: Path, identity: dict, inventory: dict) -> dict | None:
    out = init_tier_output("A")

    cas_path = identity.get("cas_path")
    if not cas_path:
        add_warning(out, "No cas_path in identity; skipping Tier 3")
        return out

    sub_format = identity.get("sub_format", "legacy")

    # Carry forward sub-format info
    set_field(out, "fluent_sub_format", sub_format,
              FieldProvenance("A", "from Tier 1"))
    set_field(out, "compressed", identity.get("compressed", False),
              FieldProvenance("A", "from Tier 1"))

    try:
        vtk, _dsa = require_vtk()
    except RuntimeError as e:
        add_warning(out, f"VTK unavailable: {e}")
        return out

    reader = _make_reader(vtk, sub_format)
    if reader is None:
        add_warning(out, f"No VTK reader for fluent sub_format={sub_format}")
        return out

    # vtkFLUENTReader (9.4.x / 9.6.x) crashes on .cas.gz with STATUS_STACK_BUFFER_OVERRUN
    # — transparently decompress to %TEMP% before SetFileName.
    from sim_parse.solvers.fluent._decompress import ensure_decompressed_fluent_pair
    cas_for_vtk, _dat_for_vtk = ensure_decompressed_fluent_pair(
        cas_path, identity.get("dat_path")
    )
    if str(cas_for_vtk) != str(cas_path):
        add_warning(out,
            f"transparently decompressed .cas.gz to {cas_for_vtk} for vtkFLUENTReader "
            f"(reader cannot read .gz directly)")

    reader.SetFileName(str(cas_for_vtk))
    safe_call(reader.UpdateInformation, default=None)

    # Cell array names (variables in the dat file)
    n_arrs = safe_call(reader.GetNumberOfCellArrays, default=0)
    variables = []
    for i in range(n_arrs or 0):
        name = safe_call(reader.GetCellArrayName, i, default=None)
        if name:
            variables.append(name)
    set_field(out, "variables", variables,
              FieldProvenance("A", "vtkFLUENTReader.GetCellArrayName"))
    set_field(out, "n_variables", len(variables),
              FieldProvenance("A", "len(variables)"))

    # Time steps (transient cases)
    times_arr = safe_call(reader.GetTimeValues, default=None) if hasattr(reader, "GetTimeValues") else None
    if times_arr and hasattr(times_arr, "GetNumberOfTuples"):
        n_times = times_arr.GetNumberOfTuples()
        time_values = [times_arr.GetValue(i) for i in range(n_times)]
        set_field(out, "time_steps", time_values,
                  FieldProvenance("A", "vtkFLUENTReader.GetTimeValues"))

    # Force a Update() to populate cell zones / mesh counts
    safe_call(reader.Update, default=None)
    output = safe_call(reader.GetOutput, default=None)

    if output is not None:
        # output is vtkMultiBlockDataSet; iterate (with names if available)
        from sim_parse.adapters.vtk_io import iterate_named_blocks

        n_blocks_total_cells = 0          # sum over ALL blocks (legacy back-compat)
        n_blocks_total_points = 0
        cell_zones = []
        mesh_zones = []
        n_volume_cells = 0                # sum over volume zones only — the
                                          # actual physical cell count
        for i, (path, block) in enumerate(iterate_named_blocks(output)):
            if block is None:
                continue
            n_cells = safe_call(block.GetNumberOfCells, default=0) or 0
            n_points = safe_call(block.GetNumberOfPoints, default=0) or 0
            n_blocks_total_cells += n_cells
            n_blocks_total_points += n_points

            # Back-compat: keep the old cell_zones list with the original
            # shape so existing consumers don't break.
            cell_zones.append({
                "block_index": i,
                "n_cells": n_cells,
                "n_points": n_points,
            })

            # Cross-solver mesh_zones schema; role inferred from name suffix.
            zone = _build_fluent_mesh_zone(block, path, i, n_cells, n_points)
            if zone:
                mesh_zones.append(zone)
                if zone["role"] == "volume":
                    n_volume_cells += n_cells

        # mesh_cells should be the count of *physical* 3D cells. vtkFLUENTReader
        # exposes face zones as additional blocks whose 'cells' are actually
        # faces, so naive summation over-counts. Use the role-aware total.
        set_field(out, "mesh_cells", n_volume_cells if n_volume_cells > 0 else n_blocks_total_cells,
                  FieldProvenance("A",
                      "sum over volume zones (mesh_zones[role=volume]); "
                      "falls back to all-block sum if role inference yields zero"))
        # mesh_points: vtkFLUENTReader shares one global point array across
        # blocks; n_blocks_total_points is therefore (n_unique × n_blocks),
        # which is meaningless. Use the per-zone n_points reported by the
        # global block (same for every zone, so any zone's value is fine).
        unique_points = mesh_zones[0]["n_points"] if mesh_zones else n_blocks_total_points
        set_field(out, "mesh_points", unique_points,
                  FieldProvenance("A",
                      "vtkFLUENTReader exposes a single global point set "
                      "shared by all blocks; this is its size"))
        set_field(out, "cell_zones", cell_zones,
                  FieldProvenance("A", "vtkMultiBlockDataSet block iteration (legacy schema)"))
        set_field(out, "n_cell_zones", len(cell_zones),
                  FieldProvenance("A", "len(cell_zones)"))
        if mesh_zones:
            set_field(out, "mesh_zones", mesh_zones,
                      FieldProvenance("A",
                          "vtkFLUENTReader block walk: role classified by "
                          "name suffix (':fluid'/':wall'/':pressure-*'/...)"))

            # Top-level `boundaries` mirror — same shape as OpenFOAM Tier 3
            # so cross-solver consumers can ask "what patches are there?"
            # without filtering mesh_zones themselves.
            boundaries = _project_boundaries_from_mesh_zones(mesh_zones)
            if boundaries:
                set_field(out, "boundaries", boundaries,
                          FieldProvenance("A",
                              "projection of mesh_zones[role=boundary]; "
                              "schema mirrors OpenFOAM polyMesh/boundary"))

    # transcript file → can extract solver type / iteration count
    if inventory and inventory.get("transcript_files"):
        trn_info = _scan_transcript(case_root, inventory["transcript_files"])
        if trn_info:
            set_field(out, "transcript_summary", trn_info,
                      FieldProvenance("A", "*.trn header parsing"))

    # ─── physics_setup container — all NotExtracted for legacy .cas ──────────
    # Fluent's physics setup (turbulence/material/reactions/...) lives in the
    # `models` section of the .cas binary. vtkFLUENTReader doesn't expose
    # this; we'd need an h5py path for .cas.h5 (CFF) or a dedicated section
    # parser for legacy. Mark every component NotExtracted with the reason
    # so consumers know it's parser debt, not a missing case feature.
    from sim_parse.core.schema import physics_setup_unextractable
    sub_format = identity.get("sub_format", "legacy")
    if sub_format == "cff":
        ne_reason = "Fluent CFF (.cas.h5) physics models in HDF5 attrs not yet read"
        ne_would = "h5py reader for .cas.h5 models/* groups"
    else:
        ne_reason = "Fluent legacy .cas binary section 39 (models) not parsed"
        ne_would = "binary .cas section parser (Section 39 / 41 / 45)"
    set_field(out, "physics_setup",
              physics_setup_unextractable(ne_reason, ne_would).model_dump(),
              FieldProvenance("A",
                  "all components NotExtracted; Fluent-specific reason carried inside"))

    return out


# ─── mesh_zones schema (cross-solver) ─────────────────────────────────────────

# Fluent zone-name suffixes (everything after ':') tell us volume vs face.
# Source: Fluent UDF Manual Zone Type list. Conservative — anything we
# don't recognize gets role='unknown' with explicit reason.
_FLUENT_VOLUME_ZONE_TYPES = {"fluid", "solid"}
_FLUENT_INTERIOR_ZONE_TYPES = {"interior"}     # internal faces; not a boundary
_FLUENT_BOUNDARY_ZONE_TYPES = {
    "wall", "axis", "symmetry", "outflow", "exhaust-fan", "intake-fan",
    "inlet-vent", "outlet-vent",
    "pressure-inlet", "pressure-outlet", "pressure-far-field",
    "mass-flow-inlet", "mass-flow-outlet",
    "velocity-inlet",
    "porous-jump", "fan", "radiator",
    "interface", "periodic", "shadow",
}


def _project_boundaries_from_mesh_zones(mesh_zones: list[dict]) -> list[dict]:
    """Project the boundary zones out of the unified mesh_zones list into a
    flat list with the OpenFOAM-style polyMesh/boundary schema.

    Cross-solver consumers (sim-knowledge rules, sim-post zone selection,
    LLM tool prompts) can iterate `boundaries` uniformly without knowing
    which solver produced it.
    """
    return [
        {
            "name": z.get("name"),
            "type": z.get("patch_type") or "",
            "nFaces": z.get("n_faces"),
        }
        for z in (mesh_zones or [])
        if z.get("role") == "boundary"
    ]


def _classify_fluent_zone_by_name(name: str) -> tuple[str, str]:
    """Parse a Fluent block name → (role, zone_type).

    Two naming conventions seen in the wild:
      A) <name>:<type>  e.g. 'tria-3-wall:wall'           (type is suffix)
      B) <type>:<name>  e.g. 'wall:tria-3-wall'           (type is prefix)
                              'fluid:tets'
                              'pressure-far-field:tria-2-outlet'

    Try both ends of the colon; whichever matches a known Fluent zone
    type wins. Both conventions appear depending on Fluent version /
    how the case was set up — vtkFLUENTReader doesn't normalize the order.

    Returns:
        (role, zone_type) where role ∈ {volume, boundary, interface, unknown}.
        zone_type is the canonical Fluent type tag, or the raw token if
        nothing matched (so callers still see what we tried).
    """
    if ":" not in name:
        return "unknown", ""

    # Both candidates: prefix (before first :) and suffix (after last :).
    # Convention B is the "wall:tria-3-wall" pattern; convention A is the
    # legacy "tria-3-wall:wall" pattern.
    prefix = name.partition(":")[0].strip().lower()
    suffix = name.rpartition(":")[2].strip().lower()

    for candidate in (prefix, suffix):  # prefer prefix (convention B)
        if candidate in _FLUENT_VOLUME_ZONE_TYPES:
            return "volume", candidate
        if candidate in _FLUENT_INTERIOR_ZONE_TYPES:
            return "interface", candidate
        if candidate in _FLUENT_BOUNDARY_ZONE_TYPES:
            return "boundary", candidate

    # Nothing matched — return the suffix (legacy convention A) as the
    # raw type tag so callers can still see what was there.
    return "unknown", suffix


def _build_fluent_mesh_zone(block, path: tuple, block_index: int,
                             n_cells: int, n_points: int) -> dict | None:
    """Build one mesh_zones entry from a vtkFLUENTReader block.

    Role inference uses the Fluent zone-type suffix (`:fluid`, `:wall`,
    `:pressure-far-field`, ...) carried in the block name. We do NOT
    use cell-type sniffing because vtkFLUENTReader reports cells uniformly
    as vtkTetra/vtkHex even for face zones, making it unreliable.

    n_points and bounding_box come from the block but, in vtkFLUENTReader,
    point arrays are SHARED across blocks — every block returns the same
    global point set. We surface this honestly via `geometry_aliased=True`
    so consumers don't mistake a per-zone bbox for an actual zone-localized
    geometry.
    """
    if block is None:
        return None
    name = path[-1] if path and path[-1] else f"block_{block_index}"
    role, zone_type = _classify_fluent_zone_by_name(name)

    bbox = None
    try:
        bnds = [0.0] * 6
        block.GetBounds(bnds)
        if all(abs(b) < 1e100 for b in bnds):
            bbox = [float(b) for b in bnds]
    except Exception:
        pass

    # Map n_cells to the right schema slot based on role
    if role == "volume":
        n_cells_field, n_faces_field = n_cells, None
    elif role in ("boundary", "interface"):
        # In Fluent, face-zone "cells" are actually faces.
        n_cells_field, n_faces_field = None, n_cells
    else:
        n_cells_field, n_faces_field = n_cells if n_cells else None, None

    return {
        "name": name,
        "role": role,
        "n_cells": n_cells_field,
        "n_faces": n_faces_field,
        "n_points": n_points,
        "bounding_box": bbox,
        "element_types": None,        # vtkFLUENTReader cell-type signal is
                                       # not reliable per zone; left empty.
        "patch_type": zone_type or None,
        "geometry_aliased": True,     # bbox/n_points reflect global mesh,
                                       # not this block alone — see docstring.
        "_source": f"vtkFLUENTReader block_index={block_index}",
        "block_path": list(path),
    }


def _make_reader(vtk_module, sub_format: str):
    """Instantiate the right reader for the sub-format."""
    if sub_format == "cff":
        try:
            from vtkmodules.vtkIOFLUENTCFF import vtkFLUENTCFFReader
            return vtkFLUENTCFFReader()
        except ImportError:
            return None
    # legacy + legacy_gz both use the same reader (vtk handles gz)
    try:
        from vtkmodules.vtkIOGeometry import vtkFLUENTReader
        return vtkFLUENTReader()
    except ImportError:
        return None


def _scan_transcript(case_root: Path, trn_filenames: list) -> dict | None:
    """Pull simple info from .trn (Fluent transcript log)."""
    case_root = Path(case_root)
    if case_root.is_file():
        case_root = case_root.parent
    out: dict = {}
    for trn_name in trn_filenames[:1]:  # just first for now
        trn_path = case_root / trn_name
        if not trn_path.is_file():
            continue
        try:
            text = trn_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Fluent banner: "ANSYS Fluent ... Release X.X.X"
        import re
        m = re.search(r"ANSYS Fluent.*Release\s+(\S+)", text[:2048])
        if m:
            out["fluent_version"] = m.group(1)
        # Final iteration count if present
        iters = re.findall(r"^\s*(\d+)\s+\d", text, flags=re.MULTILINE)
        if iters:
            try:
                out["last_iteration_in_transcript"] = int(iters[-1])
            except ValueError:
                pass
    return out if out else None
