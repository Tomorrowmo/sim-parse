"""Path C: LLM Discovery Agent.

Phase 1 status: STUB. Real implementation needs:
    - LLM client (httpx → Ollama or external)
    - Sandbox for py_exec (multiprocessing + read-only FS)
    - Consumes sim-knowledge/formats/ for inspection playbooks

When called, returns {"unsupported": True, ...}.
"""
from __future__ import annotations

from pathlib import Path


def discover(case_root: Path, target_tier: int = 4) -> dict:
    """Stub for Path C. Always returns 'unsupported' for now."""
    return {
        "unsupported": True,
        "reason": "Path C (LLM Discovery) not implemented in Phase 1",
        "_path": "C",
        "case_root": str(case_root),
    }


def fill_missing(case_root: Path, missing_fields: list, context: dict) -> dict:
    """Stub: when called to fill specific missing fields via LLM."""
    return {
        "_path": "C",
        "_warnings": [f"Path C asked to fill {len(missing_fields)} missing fields, not implemented"],
    }
