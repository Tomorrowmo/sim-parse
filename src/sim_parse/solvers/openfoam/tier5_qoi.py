"""OpenFOAM Tier 5 — QOI (Quantities of Interest) long-format records.

Generic engine in `core/canonical/qoi_engine.py` does the heavy lifting; this
file is a thin shim that:
    1. Loads QOI rules (built-in default or user-provided yaml)
    2. Calls evaluator with the parse_case full output
    3. Returns the long-format record list
"""
from __future__ import annotations

from pathlib import Path

from sim_parse.core.canonical.qoi_engine import evaluate_qoi_rules, load_qoi_rules
from sim_parse.core.errors import never_raise


@never_raise(default=[])
def qoi(
    case_root: Path,
    identity: dict,
    metadata: dict,
    field_stats: dict,
    *,
    parse_result: dict | None = None,
    qoi_rules_path: str | Path | None = None,
) -> list[dict]:
    """Extract QOI long-format records.

    Args:
        case_root: case directory.
        identity, metadata, field_stats: tier 1/3/4 outputs.
        parse_result: optional pre-built dict containing tier_N_* keys (if not
                      provided, reconstructed from individual args).
        qoi_rules_path: optional external YAML to override built-in rules.

    Returns:
        list of records: {variable, value, unit, source, confidence, ...}
    """
    rules = load_qoi_rules(qoi_rules_path)
    if not rules:
        return []

    if parse_result is None:
        # Reconstruct what evaluate_qoi_rules expects
        parse_result = {
            "tier_1_identify": identity or {},
            "tier_3_metadata": metadata or {},
            "tier_4_field_stats": field_stats or {},
        }

    return evaluate_qoi_rules(rules, parse_result)
