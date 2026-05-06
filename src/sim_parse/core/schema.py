"""Minimal cross-solver schema (audit-baseline v0).

Purpose: surface schema drift between solvers. NOT enforced at runtime yet.
Run scripts/audit_schema.py to compare each solver's actual output against
this schema and produce a violation list.

Sentinel design (3 kinds of "missing"):
    NotApplicable  — physics not part of this case (cold-flow has no chemistry)
    NotExtracted   — physics is in the case but our parser can't read it
    NotSet         — case author didn't configure it (e.g. forgot turbulence model)

Tier coverage in v0:
    Tier 1 (identify) — minimal required field set
    Tier 3 (metadata) — only mesh_zones is required; physics_setup deferred to v1
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


# ─── Sentinels ─────────────────────────────────────────────────────────────

class NotApplicable(BaseModel):
    """This physics is not part of the case."""
    status: Literal["not_applicable"] = "not_applicable"
    reason: str


class NotExtracted(BaseModel):
    """The physics IS in the case but we can't extract it (parser debt)."""
    status: Literal["not_extracted"] = "not_extracted"
    reason: str
    would_require: str | None = None


class NotSet(BaseModel):
    """The case author didn't configure this (e.g. defaulted to laminar)."""
    status: Literal["not_set"] = "not_set"


# ─── Tier 1 ────────────────────────────────────────────────────────────────

class Tier1Identify(BaseModel):
    """Tier 1 — required minimum every solver must produce."""
    model_config = ConfigDict(extra="allow")  # allow solver-specific extras

    format: str                            # required: "openfoam" / "fluent" / ...
    solver: str | None = None              # null is OK (don't fill placeholder!)
    version: str | None = None


# ─── Tier 3 ────────────────────────────────────────────────────────────────

class MeshZone(BaseModel):
    """One zone in the unified cross-solver mesh_zones list."""
    model_config = ConfigDict(extra="allow")

    name: str
    role: Literal["volume", "boundary", "unknown"]
    n_cells: int | None = None
    n_faces: int | None = None
    n_points: int | None = None
    bounding_box: list[float] | None = None
    element_types: dict[str, int] | None = None
    patch_type: str | None = None


class Tier3Metadata(BaseModel):
    """Tier 3 — required minimum: mesh_zones must exist (even if skeleton)."""
    model_config = ConfigDict(extra="allow")

    mesh_zones: list[MeshZone]
    mesh_cells: int | None = None
    mesh_points: int | None = None
    mesh_faces: int | None = None


# ─── Physics setup container (OF migration target; all solvers ladder up) ─────


# Each component is one of:
#   dict                — extracted, real content
#   NotApplicable model — physics not part of this case (cold-flow has no chem)
#   NotExtracted model  — case has it but parser can't read it (parser debt)
#   NotSet model        — case author left default / didn't configure
#   None                — legacy / unspecified (treat as "we don't know yet")
PhysicsField = dict | NotApplicable | NotExtracted | NotSet | None


class PhysicsSetup(BaseModel):
    """Cross-solver physics-setup container.

    OpenFOAM populates components from the corresponding constant/* dicts:
      turbulence       constant/turbulenceProperties
      thermophysics    constant/thermophysicalProperties
      chemistry        constant/chemistryProperties
      combustion       constant/combustionProperties
      radiation        constant/radiationProperties (if present)
      multiphase       constant/transportProperties / phaseProperties

    Fluent (legacy .cas binary) emits NotExtracted for everything except
    when h5py can read .cas.h5; CGNS emits NotExtracted (data exchange
    format doesn't carry solver setup).

    Container is intentionally additive — adding a new component is
    forward-compatible for downstream consumers because they default to
    `None` (interpreted as "don't know").
    """
    model_config = ConfigDict(extra="allow")

    turbulence:    PhysicsField = None
    thermophysics: PhysicsField = None
    chemistry:     PhysicsField = None
    combustion:    PhysicsField = None
    radiation:     PhysicsField = None
    multiphase:    PhysicsField = None


def physics_setup_unextractable(reason: str, would_require: str | None = None
                                 ) -> PhysicsSetup:
    """Build a PhysicsSetup where every component is NotExtracted.

    For solvers (Fluent legacy, CGNS, ...) where Tier 3 currently can't
    pull physics setup. Same `reason` for all components — caller passes
    in the parser-debt explanation once.
    """
    not_ext = NotExtracted(reason=reason, would_require=would_require)
    return PhysicsSetup(
        turbulence=not_ext,
        thermophysics=not_ext,
        chemistry=not_ext,
        combustion=not_ext,
        radiation=not_ext,
        multiphase=not_ext,
    )


__all__ = [
    "NotApplicable", "NotExtracted", "NotSet",
    "Tier1Identify", "MeshZone", "Tier3Metadata",
    "PhysicsSetup", "PhysicsField", "physics_setup_unextractable",
]
