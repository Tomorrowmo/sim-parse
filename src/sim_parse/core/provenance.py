"""Provenance tracking: which Path / source / confidence each field came from.

Convention: Tier outputs include a `_provenance` dict keyed by field name:
    {
        "mesh_cells": 3466,
        "turbulence_model": "kEpsilon",
        "_provenance": {
            "mesh_cells":         {"path": "A", "source": "polyMesh/owner header", "confidence": "HIGH"},
            "turbulence_model":   {"path": "A", "source": "constant/turbulenceProperties", "confidence": "HIGH"},
        }
    }

In addition, tier output dicts have:
    "_path": "A" | "B" | "C"   — primary path used for this tier
    "_errors":   list[str]     — fatal errors encountered (field is None)
    "_warnings": list[str]     — non-fatal advisories
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

PathTag = Literal["A", "B", "C", "D"]
Confidence = Literal["HIGH", "MED", "LOW", "PENDING"]


@dataclass(frozen=True)
class FieldProvenance:
    """How a single field was extracted."""
    path: PathTag
    source: str
    confidence: Confidence = "HIGH"

    def to_dict(self) -> dict:
        return {"path": self.path, "source": self.source, "confidence": self.confidence}


def init_tier_output(path: PathTag = "A") -> dict:
    """Create an empty tier output dict with required meta fields."""
    return {
        "_path": path,
        "_provenance": {},
        "_errors": [],
        "_warnings": [],
    }


def set_field(out: dict, name: str, value: Any, prov: FieldProvenance | None = None) -> None:
    """Set a field with optional provenance tracking. Mutates `out`."""
    out[name] = value
    if prov is not None:
        out["_provenance"][name] = prov.to_dict()


def add_error(out: dict, message: str) -> None:
    """Append a fatal error message to the tier output."""
    out["_errors"].append(message)


def add_warning(out: dict, message: str) -> None:
    """Append a non-fatal warning."""
    out["_warnings"].append(message)
